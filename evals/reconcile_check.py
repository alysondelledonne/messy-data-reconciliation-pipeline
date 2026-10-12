"""Score reconciliation against ground truth. Makes no API calls.

    uv run python -m evals.reconcile_check --pack invoices [--show 10]

Like `spot_check`, a quick check rather than the Milestone 7 eval. It reports:
- record linking: how many golden records are pure (all their source records describe
  one true record) and how many true records ended up in exactly one golden record;
- entities: pairs of spellings correctly merged / wrongly merged / wrongly split;
- field accuracy of the golden records against the true (clean) values.
"""

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from data_gen.ground_truth import GroundTruth, Occurrence
from evals.spot_check import same
from pipeline.ingest.models import location_covers
from pipeline.reconcile.names import normalize
from pipeline.store.models import (
    Entity,
    ExtractedRecord,
    ExtractionResult,
    GoldenRecord,
    RawRecordRow,
    RecordLink,
    SourceFile,
)


@dataclass
class ReconcileScore:
    golden: int = 0
    pure: int = 0
    true_records: int = 0
    whole: int = 0  # true records found in exactly one golden record
    unlinked: int = 0  # true records whose every extracted record was rejected
    split: list[str] = field(default_factory=list)
    mixed: list[int] = field(default_factory=list)
    entity_pairs: Counter[str] = field(default_factory=Counter)  # tp / fp / fn
    wrong_merges: list[tuple[str, str]] = field(default_factory=list)
    field_hits: Counter[str] = field(default_factory=Counter)
    field_totals: Counter[str] = field(default_factory=Counter)
    field_misses: list[str] = field(default_factory=list)

    def summary(self, show: int = 10) -> str:
        tp, fp, fn = (self.entity_pairs[k] for k in ("tp", "fp", "fn"))
        lines = [
            f"golden records: {self.golden}, pure {self.pure} ({_pct(self.pure, self.golden)})",
            f"true records:   {self.true_records}, in exactly one golden record "
            f"{self.whole} ({_pct(self.whole, self.true_records)}); "
            f"{self.unlinked} more only in rejected records",
            f"entity spelling pairs: merged correctly {tp}, wrongly merged {fp}, "
            f"wrongly split {fn}",
            "golden field accuracy vs truth (fields some source states):",
        ]
        for name in sorted(self.field_totals):
            h, t = self.field_hits[name], self.field_totals[name]
            lines.append(f"  {name:<16} {h:>5}/{t:<5} {_pct(h, t)}")
        if self.wrong_merges:
            lines.append(
                "wrongly merged spellings: "
                + "; ".join(f"{a} = {b}" for a, b in self.wrong_merges[:show])
            )
        if self.split:
            lines.append("split true records: " + ", ".join(self.split[:show]))
        if self.field_misses:
            lines.append("field misses:")
            lines += self.field_misses[:show]
        return "\n".join(lines)


def _pct(a: int, b: int) -> str:
    return f"{100 * a / b:.1f}%" if b else "n/a"


def record_truth(session: Session, gt: GroundTruth) -> dict[int, str]:
    """extracted_record id -> ground-truth record_id."""
    return {k: rid for k, (rid, _) in match_occurrences(session, gt).items()}


def match_occurrences(session: Session, gt: GroundTruth) -> dict[int, tuple[str, Occurrence]]:
    """extracted_record id -> (record_id, occurrence): the closest planted record at that
    location."""
    owner: dict[str, str] = {}
    by_source: dict[str, list[Occurrence]] = defaultdict(list)
    for r in gt.records:
        for o in r.occurrences:
            by_source[o.source].append(o)
            owner[o.occurrence_id] = r.record_id
    rows = session.execute(
        select(ExtractedRecord.id, SourceFile.path, RawRecordRow.location, ExtractedRecord.fields)
        .join(ExtractionResult, ExtractionResult.id == ExtractedRecord.result_id)
        .join(RawRecordRow, RawRecordRow.id == ExtractionResult.raw_record_id)
        .join(SourceFile, SourceFile.id == RawRecordRow.source_file_id)
        .where(ExtractionResult.pack == gt.pack)
    ).all()
    out: dict[int, tuple[str, Occurrence]] = {}
    for rec_id, path, location, fields in rows:
        source = path if path.startswith(f"{gt.pack}/") else f"{gt.pack}/{path}"
        cands = [o for o in by_source.get(source, []) if location_covers(location, o.location)]
        if cands:
            occ = max(
                cands, key=lambda o: sum(same(fields.get(k), v) for k, v in o.expected.items())
            )
            out[rec_id] = (owner[occ.occurrence_id], occ)
    return out


def score(session: Session, gt: GroundTruth, show: int = 10) -> ReconcileScore:
    s = ReconcileScore()
    matched = match_occurrences(session, gt)
    truth_of = {k: rid for k, (rid, _) in matched.items()}
    # Fields some extracted source states for each true record; the rest can't be known.
    knowable: dict[str, set[str]] = defaultdict(set)
    for rid, occ in matched.values():
        knowable[rid] |= {k for k, v in occ.expected.items() if v not in (None, [], "")}
    true_by_id = {r.record_id: r for r in gt.records}
    links = session.execute(
        select(RecordLink.golden_id, RecordLink.extracted_record_id)
        .join(GoldenRecord)
        .where(GoldenRecord.pack == gt.pack)
    ).all()
    members: dict[int, set[str]] = defaultdict(set)
    weight: Counter[tuple[int, str]] = Counter()
    for golden_id, rec_id in links:
        if rec_id in truth_of:
            members[golden_id].add(truth_of[rec_id])
            weight[(golden_id, truth_of[rec_id])] += 1
    goldens = {
        g.id: g for g in session.scalars(select(GoldenRecord).where(GoldenRecord.pack == gt.pack))
    }
    s.golden = len(goldens)
    homes: dict[str, set[int]] = defaultdict(set)
    for gid, recs in members.items():
        for r in recs:
            homes[r].add(gid)
        if len(recs) == 1:
            s.pure += 1
        else:
            s.mixed.append(gid)
    s.true_records = len(homes)
    s.unlinked = len(set(truth_of.values()) - set(homes))
    for rid, gids in homes.items():
        if len(gids) == 1:
            s.whole += 1
        else:
            s.split.append(rid)

    # Field accuracy: each true record scored on the golden record holding most of it, on
    # the fields at least one of its sources states.
    for rid, gids in homes.items():
        gid = max(gids, key=lambda g: weight[(g, rid)])
        fields = goldens[gid].fields
        for name, want in true_by_id[rid].truth.items():
            if name not in knowable[rid]:
                continue
            s.field_totals[name] += 1
            if same(fields.get(name), want):
                s.field_hits[name] += 1
            elif len(s.field_misses) < show:
                s.field_misses.append(f"  {rid}.{name}: got {fields.get(name)!r}, truth {want!r}")

    # Entities: pairs of normalized spellings.
    planted: dict[str, str] = {}
    for e in gt.entities:
        for n in (e.canonical_name, *e.aliases):
            planted[normalize(n)] = e.entity_id
    found: dict[str, int] = {}
    for ent in session.scalars(select(Entity).where(Entity.pack == gt.pack)):
        for alias in ent.aliases:
            found[normalize(alias["name"])] = ent.id
    names = sorted(set(planted) & set(found))
    for a, b in combinations(names, 2):
        truly, merged = planted[a] == planted[b], found[a] == found[b]
        if truly and merged:
            s.entity_pairs["tp"] += 1
        elif merged:
            s.entity_pairs["fp"] += 1
            s.wrong_merges.append((a, b))
        elif truly:
            s.entity_pairs["fn"] += 1
    return s


def main() -> None:
    from pipeline.store.db import make_engine, make_session

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pack", required=True)
    ap.add_argument("--corpus", type=Path, default=Path("data/generated"))
    ap.add_argument("--show", type=int, default=10)
    args = ap.parse_args()
    gt = GroundTruth.model_validate_json(
        (args.corpus / args.pack / "ground_truth.json").read_text()
    )
    with make_session(make_engine()) as session:
        print(score(session, gt, args.show).summary(args.show))


if __name__ == "__main__":
    main()
