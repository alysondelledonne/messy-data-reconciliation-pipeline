"""Compare what has been extracted so far against ground truth. Makes no API calls.

    uv run python -m evals.spot_check --pack invoices [--show 10]

A quick sanity check, not the Milestone 7 eval. It scores only the records
extracted so far, field by field, against the values each source actually states
(`expected` in ground truth).
"""

import argparse
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from sqlalchemy import select

from data_gen.ground_truth import GroundTruth, Occurrence
from pipeline.ingest.models import location_covers
from pipeline.store.db import make_engine, make_session
from pipeline.store.models import ExtractedRecord, ExtractionResult, RawRecordRow, SourceFile


def same(a: Any, b: Any) -> bool:
    if a is None or b is None:
        return a is b
    if isinstance(a, list) or isinstance(b, list):
        return isinstance(a, list) and isinstance(b, list) and len(a) == len(b)
    try:
        return Decimal(str(a)) == Decimal(str(b))
    except InvalidOperation:
        return " ".join(str(a).split()).casefold() == " ".join(str(b).split()).casefold()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pack", required=True)
    ap.add_argument("--corpus", type=Path, default=Path("data/generated"))
    ap.add_argument("--show", type=int, default=10, help="mismatches to print")
    args = ap.parse_args()

    gt = GroundTruth.model_validate_json(
        (args.corpus / args.pack / "ground_truth.json").read_text()
    )
    by_source: dict[str, list[Occurrence]] = defaultdict(list)
    for r in gt.records:
        for o in r.occurrences:
            by_source[o.source].append(o)

    q = (
        select(SourceFile.path, RawRecordRow.location, ExtractionResult.method,
               ExtractionResult.escalated, ExtractedRecord.fields)
        .join(ExtractionResult, ExtractionResult.raw_record_id == RawRecordRow.id)
        .join(ExtractedRecord, ExtractedRecord.result_id == ExtractionResult.id)
        .join(SourceFile, SourceFile.id == RawRecordRow.source_file_id)
        .where(ExtractionResult.pack == args.pack)
    )  # fmt: skip
    hits: Counter[tuple[str, str]] = Counter()
    totals: Counter[tuple[str, str]] = Counter()
    unmatched = 0
    records = 0
    mismatches: list[str] = []
    with make_session(make_engine()) as session:
        rows = session.execute(q).all()
    for path, location, method, escalated, fields in rows:
        records += 1
        source = f"{args.pack}/{path}"
        candidates = [o for o in by_source.get(source, []) if location_covers(location, o.location)]
        if not candidates:
            unmatched += 1
            mismatches.append(f"  no ground-truth record at {source} {location}: {fields}")
            continue
        # A document can hold several records: compare with the closest one.
        occ = max(
            candidates, key=lambda o: sum(same(fields.get(k), v) for k, v in o.expected.items())
        )
        kind = "documents (LLM)" if method == "llm" else "mapped rows"
        for name, want in occ.expected.items():
            totals[(kind, name)] += 1
            if same(fields.get(name), want):
                hits[(kind, name)] += 1
            elif len(mismatches) < args.show + unmatched:
                tag = " [escalated]" if escalated else ""
                mismatches.append(
                    f"  {source} {location}{tag}\n    {name}: got {fields.get(name)!r}, "
                    f"source says {want!r}"
                )

    print(f"{args.pack}: {records} extracted records checked ({unmatched} with no matching "
          "ground-truth record)\n")  # fmt: skip
    for kind in ("documents (LLM)", "mapped rows"):
        names = sorted(n for k, n in totals if k == kind)
        if not names:
            continue
        print(f"{kind}")
        for n in names:
            h, t = hits[(kind, n)], totals[(kind, n)]
            print(f"  {n:<16} {h:>5}/{t:<5} {100 * h / t:5.1f}%")
        print()
    if mismatches:
        print("Examples to look at:")
        print("\n".join(mismatches[: args.show]))


if __name__ == "__main__":
    main()
