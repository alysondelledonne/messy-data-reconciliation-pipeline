"""Ingest the whole generated corpus and check it against ground truth.

The headline test: every place a true record appears (every ground-truth
occurrence) is covered by an ingested record from the same file. If ingest loses
or misplaces anything, it fails.
"""

import fnmatch
from collections import defaultdict
from typing import Any

import pytest

from data_gen.ground_truth import GroundTruth, Occurrence
from pipeline.ingest.ingest import DEFAULT_EXCLUDE, ingest_bytes
from pipeline.ingest.models import IngestedFile, RawRecord, location_covers

NON_DATA_KINDS = {"total_row", "stray_note", "sheet_text", "heading", "malformed_line"}


@pytest.fixture(scope="session")
def ingested(corpus: dict[str, bytes]) -> dict[str, IngestedFile]:
    out = {}
    for path, data in sorted(corpus.items()):
        name = path.rsplit("/", 1)[-1]
        if any(fnmatch.fnmatch(name, pat) for pat in DEFAULT_EXCLUDE):
            continue
        out[path] = ingest_bytes(path, data)
    return out


def _covering(f: IngestedFile, occ: Occurrence) -> list[RawRecord]:
    return [
        r
        for r in f.records
        if r.kind not in NON_DATA_KINDS and location_covers(r.location, occ.location)
    ]


def test_every_file_ingests_cleanly(ingested: dict[str, IngestedFile]) -> None:
    bad = {p: f.error for p, f in ingested.items() if f.status != "ok"}
    assert not bad


@pytest.mark.parametrize("pack", ["invoices", "contacts"])
def test_every_ground_truth_occurrence_is_covered(
    ingested: dict[str, IngestedFile], corpus: dict[str, bytes], pack: str
) -> None:
    gt = GroundTruth.model_validate_json(corpus[f"{pack}/ground_truth.json"])
    missing = []
    total = 0
    for r in gt.records:
        for occ in r.occurrences:
            total += 1
            if len(_covering(ingested[occ.source], occ)) != 1:
                missing.append(occ.occurrence_id)
    assert total > 600
    assert not missing, f"{len(missing)}/{total} occurrences not covered, e.g. {missing[:5]}"


def test_scanned_detection_matches_ground_truth(
    ingested: dict[str, IngestedFile], invoices_gt: GroundTruth
) -> None:
    truth = {o.source: o.format for r in invoices_gt.records for o in r.occurrences}
    pdfs = {p: f.format for p, f in ingested.items() if p.endswith(".pdf")}
    assert pdfs == {p: truth[p] for p in pdfs}


def _occurrences(gt: GroundTruth, source: str) -> list[tuple[dict[str, Any], Occurrence]]:
    return [(r.truth, o) for r in gt.records for o in r.occurrences if o.source == source]


def _only(f: IngestedFile, occ: Occurrence) -> RawRecord:
    (rec,) = _covering(f, occ)
    return rec


def test_legacy_ap_dates_and_amounts_normalize_to_truth(
    ingested: dict[str, IngestedFile], invoices_gt: GroundTruth
) -> None:
    src = "invoices/ap_register.csv"
    f = ingested[src]
    assert f.encoding == "cp1252"
    partial = 0
    for _, occ in _occurrences(invoices_gt, src):
        rec = _only(f, occ)
        date = rec.normalized["Inv Date"]
        if "date_without_year" in occ.defects:
            partial += 1
            assert date["value"] is None
            assert date["partial"] == "--" + occ.expected["invoice_date"][5:]
        else:
            assert date["value"] == occ.expected["invoice_date"], rec.raw
        assert rec.normalized["Amount"]["value"].rstrip("0").rstrip(".") == (
            occ.expected["total"].rstrip("0").rstrip(".")
        ), rec.raw
        assert rec.raw["Vendor Name"] == occ.expected["vendor_name"]
    assert partial > 0


def test_procurement_export_normalizes(
    ingested: dict[str, IngestedFile], invoices_gt: GroundTruth
) -> None:
    src = "invoices/procurement_export.csv"
    f = ingested[src]
    assert f.encoding == "utf-8-sig"
    for _, occ in _occurrences(invoices_gt, src):
        rec = _only(f, occ)
        assert rec.normalized["invoice_dt"]["value"] == occ.expected["invoice_date"]
        assert rec.normalized["gross_amount"]["value"] == occ.expected["total"]


def test_aging_report_fill_down_and_serial_dates(
    ingested: dict[str, IngestedFile], invoices_gt: GroundTruth
) -> None:
    src = "invoices/ap_aging.xlsx"
    f = ingested[src]
    occs = _occurrences(invoices_gt, src)
    for _, occ in occs:
        rec = _only(f, occ)
        assert rec.raw["Supplier"] == occ.expected["vendor_name"]
        assert ("filled_down:Supplier" in rec.flags) == ("supplier_fill_down" in occ.defects)
        assert rec.normalized["Doc Date"]["value"] == occ.expected["invoice_date"]
        assert rec.normalized["Due Date"]["value"] == occ.expected["due_date"]
        if "date_as_serial" in occ.defects:
            assert rec.normalized["Doc Date"]["flags"] == ["excel_serial_date"]
    assert sum(r.kind == "total_row" for r in f.records) > 10
    assert sum(r.kind == "row" for r in f.records) == len(occs)


def test_email_parts_hold_the_right_person(
    ingested: dict[str, IngestedFile], contacts_gt: GroundTruth
) -> None:
    for r in contacts_gt.records:
        for occ in r.occurrences:
            if occ.format != "eml":
                continue
            rec = _only(ingested[occ.source], occ)
            if "email" in occ.expected:
                assert occ.expected["email"] in rec.text, (occ.occurrence_id, rec.text)
            assert occ.expected["last_name"] in rec.text


def test_text_sources_contain_their_mentions(
    ingested: dict[str, IngestedFile], contacts_gt: GroundTruth
) -> None:
    by_kind: dict[str, int] = defaultdict(int)
    for r in contacts_gt.records:
        for occ in r.occurrences:
            if occ.format in ("eml",):
                continue
            rec = _only(ingested[occ.source], occ)
            by_kind[rec.kind] += 1
            assert occ.expected["first_name"].upper() in rec.text.upper(), occ.occurrence_id
    assert set(by_kind) >= {"row", "table_row", "paragraph", "document", "json_object", "log_line"}


def test_exact_duplicate_pdfs_share_a_hash(ingested: dict[str, IngestedFile]) -> None:
    by_hash: dict[str, list[str]] = defaultdict(list)
    for p, f in ingested.items():
        by_hash[f.sha256].append(p)
    assert sum(1 for paths in by_hash.values() if len(paths) > 1) == 2
