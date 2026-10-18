"""The Milestone 7 eval: score what's in the database against a pack's ground truth
(D-042). No model calls; it reads extracted records, golden records and the call log.

Scores the machine's output. Human corrections are reverted to the stored machine value,
and statuses are recomputed from findings, so a reviewed database scores the same as an
unreviewed one.
"""

import subprocess
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from data_gen.ground_truth import GroundTruth, Occurrence
from evals.reconcile_check import match_occurrences, score
from evals.spot_check import same
from pipeline.config import Settings
from pipeline.domains import PackInfo, get_pack
from pipeline.extract.runner import extractor_version
from pipeline.reconcile.names import normalize
from pipeline.store.models import (
    ColumnMapping,
    ExtractedRecord,
    ExtractionResult,
    GoldenRecord,
    LLMCall,
    RawRecordRow,
    RecordLink,
    SourceFile,
)
from pipeline.validate.engine import validator_version

# Planted problems a reviewer should see even when every value ends up right: a bad total
# on a document, a stale status, a re-sent copy (D-042). Per pack, by ground-truth defect.
ISSUE_DEFECTS: dict[str, frozenset[str]] = {
    "invoices": frozenset(
        {
            "total_mismatch",
            "subtotal_mismatch",
            "stale_status",
            "reissued_copy",
            "rescan",
            "exact_copy",
        }
    ),
    "contacts": frozenset(),
}


def _pct(a: float, b: float) -> float | None:
    return round(100 * a / b, 1) if b else None


@dataclass
class Tally:
    hits: int = 0
    total: int = 0

    def add(self, ok: bool) -> None:
        self.hits += ok
        self.total += 1

    def as_dict(self) -> dict[str, Any]:
        return {"correct": self.hits, "total": self.total, "accuracy": _pct(self.hits, self.total)}


def _git_commit() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None


# --- source records (extraction) ---------------------------------------------------------


def extraction_accuracy(session: Session, gt: GroundTruth) -> dict[str, Any]:
    """Each extracted record against what its source actually states (`expected`)."""
    matched = match_occurrences(session, gt)
    info = get_pack(gt.pack)
    rows = session.execute(
        select(
            ExtractedRecord.id, ExtractedRecord.fields, SourceFile.format, ExtractionResult.method
        )
        .join(ExtractionResult, ExtractionResult.id == ExtractedRecord.result_id)
        .join(RawRecordRow, RawRecordRow.id == ExtractionResult.raw_record_id)
        .join(SourceFile, SourceFile.id == RawRecordRow.source_file_id)
        .where(
            ExtractionResult.pack == gt.pack,
            ExtractionResult.extractor_version == extractor_version(info),
        )
    ).all()
    by_field: dict[str, Tally] = defaultdict(Tally)
    by_format: dict[str, Tally] = defaultdict(Tally)
    by_format_field: dict[str, dict[str, Tally]] = defaultdict(lambda: defaultdict(Tally))
    by_method: dict[str, Tally] = defaultdict(Tally)
    seen: set[str] = set()
    unmatched = 0
    for rec_id, fields, fmt, method in rows:
        hit = matched.get(rec_id)
        if hit is None:
            unmatched += 1
            continue
        _, occ = hit
        seen.add(occ.occurrence_id)
        for name, want in occ.expected.items():
            ok = same(fields.get(name), want)
            for t in (
                by_field[name],
                by_format[fmt],
                by_format_field[fmt][name],
                by_method[method],
            ):
                t.add(ok)
    planted = [o for r in gt.records for o in r.occurrences]
    total = Tally(sum(t.hits for t in by_field.values()), sum(t.total for t in by_field.values()))
    return {
        "records": len(rows),
        "overall": total.as_dict(),
        "by_field": {k: v.as_dict() for k, v in sorted(by_field.items())},
        "by_format": {k: v.as_dict() for k, v in sorted(by_format.items())},
        "by_format_field": {
            f: {k: v.as_dict() for k, v in sorted(d.items())}
            for f, d in sorted(by_format_field.items())
        },
        "by_method": {k: v.as_dict() for k, v in sorted(by_method.items())},
        "occurrences_found": {
            "found": len(seen),
            "planted": len(planted),
            "recall": _pct(len(seen), len(planted)),
        },
        "extracted_without_ground_truth": unmatched,
    }


# --- golden records (reconciliation + review queue) --------------------------------------


def _machine_fields(g: GoldenRecord) -> dict[str, Any]:
    out = dict(g.fields)
    for name, prov in (g.provenance or {}).items():
        if str(prov.get("rule", "")).startswith("corrected by"):
            out[name] = prov.get("machine_value")
    return out


def _machine_status(g: GoldenRecord) -> str:
    sev = {f["severity"] for f in g.findings if f["rule"] != "review_orphaned"}
    return "rejected" if "reject" in sev else "needs_review" if "review" in sev else "auto_approved"


def _same_value(info: PackInfo, name: str, got: Any, want: Any) -> bool:
    """Organization names are scored as names of the right organization: case, accents
    and legal suffixes don't count ('Lumière Éclairage' names 'Lumière Éclairage SARL')."""
    if info.reconcile and name == info.reconcile.entity_field and got and want:
        return normalize(str(got)) == normalize(str(want))
    return same(got, want)


def golden_quality(session: Session, gt: GroundTruth, show: int = 15) -> dict[str, Any]:
    matched = match_occurrences(session, gt)
    info = get_pack(gt.pack)
    truth = {r.record_id: r for r in gt.records}
    issue_defects = ISSUE_DEFECTS.get(gt.pack, frozenset())
    links = session.execute(
        select(RecordLink.golden_id, RecordLink.extracted_record_id)
        .join(GoldenRecord)
        .where(GoldenRecord.pack == gt.pack)
    ).all()
    occs: dict[int, list[tuple[str, Occurrence]]] = defaultdict(list)
    for gid, rec_id in links:
        if rec_id in matched:
            occs[gid].append(matched[rec_id])
    homes: dict[str, set[int]] = defaultdict(set)
    for gid, items in occs.items():
        for rid, _ in items:
            homes[rid].add(gid)

    by_field: dict[str, Tally] = defaultdict(Tally)
    confusion: Counter[tuple[str, str]] = Counter()  # (machine status, wrong|issue|clean)
    missed: list[dict[str, Any]] = []
    for g in session.scalars(select(GoldenRecord).where(GoldenRecord.pack == gt.pack)):
        items = occs.get(g.id, [])
        if not items:
            continue
        weight = Counter(rid for rid, _ in items)
        rid = weight.most_common(1)[0][0]
        fields = _machine_fields(g)
        knowable = {
            k
            for r, o in items
            if r == rid
            for k, v in o.expected.items()
            if v not in (None, "", [])
        }
        wrong = []
        for name, want in truth[rid].truth.items():
            if name in knowable:
                ok = _same_value(info, name, fields.get(name), want)
                by_field[name].add(ok)
                if not ok:
                    wrong.append(name)
        mixed = len(weight) > 1
        split = len(homes[rid]) > 1
        defects = {d for r, o in items if r == rid for d in o.defects} & issue_defects
        sources = {o.source for r, o in items if r == rid}
        if (
            gt.pack == "invoices"
            and any(s.endswith(".pdf") for s in sources)
            and not any("ap_register" in s for s in truth_sources(truth[rid].occurrences))
        ):
            defects.add("unrecorded_invoice")
        kind = "wrong" if (wrong or mixed or split) else "issue" if defects else "clean"
        status = _machine_status(g)
        confusion[(status, kind)] += 1
        if status == "auto_approved" and kind == "wrong" and len(missed) < show:
            missed.append(
                {
                    "golden_id": g.id,
                    "true_record": rid,
                    "wrong_fields": wrong,
                    "mixed": mixed,
                    "split": split,
                }
            )

    def n(status: str | None = None, kind: str | None = None) -> int:
        return sum(
            v
            for (s, k), v in confusion.items()
            if (status is None or s == status) and (kind is None or k == kind)
        )

    review, auto = n("needs_review"), n("auto_approved")
    wrong_total = n(kind="wrong")
    rec = score(session, gt)
    tp, fp, fn = (rec.entity_pairs[k] for k in ("tp", "fp", "fn"))
    total = Tally(sum(t.hits for t in by_field.values()), sum(t.total for t in by_field.values()))
    return {
        "golden_records": rec.golden,
        "field_accuracy": {
            "overall": total.as_dict(),
            "by_field": {k: v.as_dict() for k, v in sorted(by_field.items())},
        },
        "statuses": {s: n(s) for s in ("auto_approved", "needs_review", "rejected")},
        "outcomes": {f"{s}/{k}": v for (s, k), v in sorted(confusion.items())},
        "review_queue": {
            "size": review,
            "precision_wrong": _pct(n("needs_review", "wrong"), review),
            "precision_wrong_or_issue": _pct(
                n("needs_review", "wrong") + n("needs_review", "issue"), review
            ),
            "error_recall": _pct(n("needs_review", "wrong"), wrong_total),
        },
        "missed_errors": {
            "count": n("auto_approved", "wrong"),
            "of_auto_approved": auto,
            "rate": _pct(n("auto_approved", "wrong"), auto),
            "examples": missed,
        },
        "dedupe": {
            "pure": rec.pure,
            "golden": rec.golden,
            "purity": _pct(rec.pure, rec.golden),
            "true_records": rec.true_records,
            "in_one_golden": rec.whole,
            "completeness": _pct(rec.whole, rec.true_records),
        },
        "entities": {
            "pairs_merged_right": tp,
            "wrongly_merged": fp,
            "wrongly_split": fn,
            "precision": _pct(tp, tp + fp),
            "recall": _pct(tp, tp + fn),
        },
    }


def truth_sources(occurrences: list[Occurrence]) -> set[str]:
    return {o.source for o in occurrences}


# --- cost and time -----------------------------------------------------------------------


def cost_and_time(session: Session, pack: str) -> dict[str, Any]:
    """Model spend to produce the current results, per 100 source documents. Cached calls
    count once, at what they cost when made. Time is the sum of call latencies (calls run
    concurrently, so wall-clock time is lower)."""
    info = get_pack(pack)
    ids: set[int] = set()
    for call_ids, mapping in session.execute(
        select(ExtractionResult.llm_call_ids, ColumnMapping.llm_call_id)
        .outerjoin(ColumnMapping, ColumnMapping.id == ExtractionResult.column_mapping_id)
        .where(
            ExtractionResult.pack == pack,
            ExtractionResult.extractor_version == extractor_version(info),
        )
    ):
        ids.update(call_ids or [])
        if mapping:
            ids.add(mapping)
    calls = list(session.scalars(select(LLMCall).where(LLMCall.id.in_(ids))))
    calls += list(
        session.scalars(
            select(LLMCall).where(LLMCall.pack == pack, LLMCall.purpose == "entity_match")
        )
    )
    docs = len(
        set(
            session.scalars(
                select(SourceFile.path).where(SourceFile.pack == pack, SourceFile.status == "ok")
            )
        )
    )
    cost = sum(float(c.cost_usd or 0) for c in calls)
    secs = sum(c.latency_ms or 0 for c in calls) / 1000
    by_purpose: dict[str, dict[str, Any]] = defaultdict(lambda: {"calls": 0, "cost_usd": 0.0})
    for c in calls:
        by_purpose[c.purpose]["calls"] += 1
        by_purpose[c.purpose]["cost_usd"] = round(
            by_purpose[c.purpose]["cost_usd"] + float(c.cost_usd or 0), 6
        )
    return {
        "documents": docs,
        "calls": len(calls),
        "models": dict(Counter(c.model_served or c.model_requested for c in calls)),
        "input_tokens": sum(c.input_tokens or 0 for c in calls),
        "output_tokens": sum(c.output_tokens or 0 for c in calls),
        "cost_usd": round(cost, 4),
        "cost_per_100_docs": round(100 * cost / docs, 4) if docs else None,
        "model_seconds": round(secs, 1),
        "model_seconds_per_100_docs": round(100 * secs / docs, 1) if docs else None,
        "by_purpose": dict(by_purpose),
    }


# --- the whole eval ----------------------------------------------------------------------


def evaluate(session: Session, gt: GroundTruth, settings: Settings, label: str) -> dict[str, Any]:
    info = get_pack(gt.pack)
    return {
        "meta": {
            "label": label,
            "pack": gt.pack,
            "at": datetime.now(UTC).isoformat(timespec="seconds"),
            "seed": gt.seed,
            "generator_version": gt.generator_version,
            "true_records": len(gt.records),
            "extractor_version": extractor_version(info),
            "validator_version": validator_version(info, settings),
            "git_commit": _git_commit(),
        },
        "extraction": extraction_accuracy(session, gt),
        "golden": golden_quality(session, gt),
        "cost": cost_and_time(session, gt.pack),
    }
