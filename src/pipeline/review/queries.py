"""Read-only queries behind the review app's pages. Plain rows (lists of dicts), no
Streamlit, so they can be tested."""

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pipeline.domains import PackInfo, get_pack
from pipeline.reconcile.names import normalize
from pipeline.store.models import (
    Entity,
    ExtractedRecord,
    ExtractionResult,
    GoldenRecord,
    LLMCall,
    MatchDecision,
    RawRecordRow,
    RecordLink,
    ReviewEvent,
    SourceFile,
    Validation,
)

# Findings raised by reconciliation rather than by a pack rule on one record.
DUPLICATE_RULES = frozenset({"duplicate_document", "duplicate_row"})
UNMATCHED_RULES = frozenset({"not_in_ap_register", "no_document"})


def packs(session: Session) -> list[str]:
    return list(session.scalars(select(SourceFile.pack).distinct().order_by(SourceFile.pack)))


def label(info: PackInfo, fields: dict[str, Any]) -> str:
    """A short name for a record: its first few required fields."""
    names = [n for n, f in info.schema.model_fields.items() if f.is_required()]
    if info.reconcile and info.reconcile.entity_field not in names:
        names.append(info.reconcile.entity_field)
    shown = [str(fields[n]) for n in names if fields.get(n) not in (None, "", [])]
    return " · ".join(shown[:4]) or "(no identifying fields)"


# --- overview ----------------------------------------------------------------------------


def overview(session: Session) -> dict[str, list[dict[str, Any]]]:
    golden = [
        {"pack": p, "status": s, "records": n}
        for p, s, n in session.execute(
            select(GoldenRecord.pack, GoldenRecord.status, func.count())
            .group_by(GoldenRecord.pack, GoldenRecord.status)
            .order_by(GoldenRecord.pack, GoldenRecord.status)
        )
    ]
    sources = [
        {"pack": p, "format": f, "status": s, "records": n}
        for p, f, s, n in session.execute(
            select(SourceFile.pack, SourceFile.format, Validation.status, func.count())
            .select_from(Validation)
            .join(ExtractedRecord, ExtractedRecord.id == Validation.extracted_record_id)
            .join(ExtractionResult, ExtractionResult.id == ExtractedRecord.result_id)
            .join(RawRecordRow, RawRecordRow.id == ExtractionResult.raw_record_id)
            .join(SourceFile, SourceFile.id == RawRecordRow.source_file_id)
            .group_by(SourceFile.pack, SourceFile.format, Validation.status)
            .order_by(SourceFile.pack, SourceFile.format, Validation.status)
        )
    ]
    files = [
        {"pack": p, "format": f, "files": n}
        for p, f, n in session.execute(
            select(SourceFile.pack, SourceFile.format, func.count())
            .group_by(SourceFile.pack, SourceFile.format)
            .order_by(SourceFile.pack, SourceFile.format)
        )
    ]
    cost = [
        {"pack": p, "purpose": pu, "calls": n, "cost_usd": float(c or 0)}
        for p, pu, n, c in session.execute(
            select(LLMCall.pack, LLMCall.purpose, func.count(), func.sum(LLMCall.cost_usd))
            .group_by(LLMCall.pack, LLMCall.purpose)
            .order_by(LLMCall.pack, LLMCall.purpose)
        )
    ]
    return {"golden": golden, "sources": sources, "files": files, "cost": cost}


# --- review queue ------------------------------------------------------------------------


def review_queue(
    session: Session,
    pack: str,
    statuses: tuple[str, ...] = ("needs_review",),
    rule: str | None = None,
) -> list[dict[str, Any]]:
    info = get_pack(pack)
    out = []
    q = (
        select(GoldenRecord)
        .where(GoldenRecord.pack == pack, GoldenRecord.status.in_(statuses))
        .order_by(GoldenRecord.id)
    )
    for g in session.scalars(q):
        rules = [f["rule"] for f in g.findings if f["severity"] != "info"]
        if rule and rule not in rules:
            continue
        out.append(
            {
                "id": g.id,
                "record": label(info, g.fields),
                "status": g.status,
                "reasons": ", ".join(dict.fromkeys(rules)),
                "sources": g.source_count,
            }
        )
    return out


@dataclass
class SourceView:
    link_reason: str
    extracted_id: int
    fields: dict[str, Any]
    field_meta: dict[str, Any]
    raw: RawRecordRow
    file: SourceFile
    validation: list[dict[str, Any]]


def golden_sources(session: Session, golden_id: int) -> list[SourceView]:
    rows = session.execute(
        select(RecordLink.reason, ExtractedRecord, RawRecordRow, SourceFile)
        .join(ExtractedRecord, ExtractedRecord.id == RecordLink.extracted_record_id)
        .join(ExtractionResult, ExtractionResult.id == ExtractedRecord.result_id)
        .join(RawRecordRow, RawRecordRow.id == ExtractionResult.raw_record_id)
        .join(SourceFile, SourceFile.id == RawRecordRow.source_file_id)
        .where(RecordLink.golden_id == golden_id)
        .order_by(SourceFile.path, RawRecordRow.id)
    ).all()
    vals: dict[int, list[dict[str, Any]]] = defaultdict(list)
    ids = [x.id for _, x, _, _ in rows]
    for v in session.scalars(select(Validation).where(Validation.extracted_record_id.in_(ids))):
        vals[v.extracted_record_id] = v.findings
    return [
        SourceView(reason, x.id, x.fields, x.field_meta, raw, sf, vals.get(x.id, []))
        for reason, x, raw, sf in rows
    ]


def pending_matches(session: Session, pack: str) -> list[dict[str, Any]]:
    """Name pairs waiting for a person, with the spellings behind each normalized name."""
    spellings: dict[str, list[str]] = defaultdict(list)
    mentions: dict[str, int] = defaultdict(int)
    for e in session.scalars(select(Entity).where(Entity.pack == pack)):
        for a in e.aliases:
            norm = normalize(a["name"])
            spellings[norm].append(a["name"])
            mentions[norm] += int(a.get("count", 0))
    out = []
    for d in session.scalars(
        select(MatchDecision)
        .where(MatchDecision.pack == pack, MatchDecision.decision == "pending")
        .order_by(MatchDecision.a, MatchDecision.b)
    ):
        out.append(
            {
                "a": d.a,
                "b": d.b,
                "a_spellings": spellings.get(d.a, []),
                "b_spellings": spellings.get(d.b, []),
                "a_mentions": mentions.get(d.a, 0),
                "b_mentions": mentions.get(d.b, 0),
                "confidence": d.confidence,
                "reason": d.reason,
                "decided_by": d.decided_by,
            }
        )
    return out


# --- exceptions --------------------------------------------------------------------------


def exceptions(session: Session, pack: str) -> dict[str, list[dict[str, Any]]]:
    info = get_pack(pack)
    dups, unmatched, conflicts, failures = [], [], [], []
    for g in session.scalars(
        select(GoldenRecord).where(GoldenRecord.pack == pack).order_by(GoldenRecord.id)
    ):
        name = label(info, g.fields)
        for f in g.findings:
            row = {
                "id": g.id,
                "record": name,
                "status": g.status,
                "rule": f["rule"],
                "severity": f["severity"],
                "message": f["message"],
            }
            if f["rule"] in DUPLICATE_RULES:
                dups.append(row)
            elif f["rule"] in UNMATCHED_RULES:
                unmatched.append(row)
            elif f["severity"] in ("review", "reject") and f["rule"] != "source_conflict":
                failures.append(row)
        if g.source_count == 1 and not any(f["rule"] in UNMATCHED_RULES for f in g.findings):
            unmatched.append(
                {
                    "id": g.id,
                    "record": name,
                    "status": g.status,
                    "rule": "single_source",
                    "severity": "info",
                    "message": "Found in one source only.",
                }
            )
        for c in g.conflicts:
            conflicts.append(
                {
                    "id": g.id,
                    "record": name,
                    "field": c["field"],
                    "chosen": c["chosen"],
                    "rule": c["rule"],
                    "chosen_from": ", ".join(c["chosen_sources"]),
                    "lost": "; ".join(
                        f"{a['value']} ({', '.join(a['sources'])})" for a in c["alternatives"]
                    ),
                }
            )
    return {
        "duplicates": dups,
        "unmatched": unmatched,
        "conflicts": conflicts,
        "rule_failures": failures,
    }


def audit_log(session: Session, pack: str, limit: int = 200) -> list[dict[str, Any]]:
    return [
        {
            "at": e.at,
            "who": e.actor,
            "action": e.action,
            "target": e.target,
            "field": e.field,
            "old": e.old,
            "new": e.new,
            "note": e.note,
        }
        for e in session.scalars(
            select(ReviewEvent)
            .where(ReviewEvent.pack == pack)
            .order_by(ReviewEvent.at.desc(), ReviewEvent.id.desc())
            .limit(limit)
        )
    ]
