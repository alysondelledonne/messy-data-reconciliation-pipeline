"""Validation on the real corpus (fake LLM, Postgres): planted problems are caught,
clean records are approved, and re-running is idempotent."""

from collections import Counter
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from data_gen.ground_truth import GroundTruth, Occurrence
from pipeline.config import Settings
from pipeline.extract.runner import Extractor
from pipeline.ingest.models import IngestedFile, location_covers
from pipeline.store.ingest_repo import save_ingest
from pipeline.store.models import (
    ExtractedRecord,
    ExtractionResult,
    RawRecordRow,
    SourceFile,
    Validation,
)
from pipeline.validate.runner import validate_pack
from tests.fakes import FakeLLM

SETTINGS = Settings(_env_file=None, anthropic_api_key="sk-test-not-real", extract_workers=4)


def _run(
    db: Session, ingested: dict[str, IngestedFile], gts: dict[str, GroundTruth], pack: str,
    corpus_dir: Path,
) -> Any:  # fmt: skip
    save_ingest(db, pack, [f for p, f in ingested.items() if p.startswith(f"{pack}/")])
    Extractor(db, FakeLLM(gts, pack), pack, SETTINGS, files_root=corpus_dir,
              log=lambda _: None).run()  # fmt: skip
    return validate_pack(db, pack, SETTINGS)


Verdict = tuple[str, dict[str, Any], dict[str, Any], str, list[dict[str, Any]]]


def _verdicts(db: Session) -> list[Verdict]:
    q = (
        select(SourceFile.path, RawRecordRow.location, ExtractedRecord.fields, Validation.status,
               Validation.findings)
        .join(ExtractedRecord, ExtractedRecord.id == Validation.extracted_record_id)
        .join(ExtractionResult, ExtractionResult.id == ExtractedRecord.result_id)
        .join(RawRecordRow, RawRecordRow.id == ExtractionResult.raw_record_id)
        .join(SourceFile, SourceFile.id == RawRecordRow.source_file_id)
    )  # fmt: skip
    return [(p, loc, fields, s, f) for p, loc, fields, s, f in db.execute(q)]


def _verdict_for(verdicts: list[Verdict], occ: Occurrence) -> tuple[str, set[str]]:
    """The verdict of the extracted record that best matches this occurrence (a document can
    hold several records)."""
    hits = [v for v in verdicts if v[0] == occ.source and location_covers(v[1], occ.location)]
    assert hits, occ.occurrence_id
    best = max(hits, key=lambda v: sum(v[2].get(k) == x for k, x in occ.expected.items()))
    return best[3], {f["rule"] for f in best[4]}


def test_invoice_problems_are_caught_and_clean_invoices_pass(
    db_session: Session,
    ingested: dict[str, IngestedFile],
    invoices_gt: GroundTruth,
    contacts_gt: GroundTruth,
    corpus_dir: Path,
) -> None:
    gts = {"invoices": invoices_gt, "contacts": contacts_gt}
    stats = _run(db_session, ingested, gts, "invoices", corpus_dir)
    assert stats.records == 694 and stats.by_status["rejected"] == 0
    verdicts = _verdicts(db_session)
    caught: Counter[str] = Counter()
    clean_pdfs = clean_ok = 0
    for r in invoices_gt.records:
        for occ in r.occurrences:
            status, rules = _verdict_for(verdicts, occ)
            d = set(occ.defects)
            if d & {"total_mismatch", "subtotal_mismatch"}:
                assert status == "needs_review", occ.occurrence_id
                assert rules & {"totals_add_up", "line_items_sum"}, occ.occurrence_id
                caught["totals"] += 1
            if "missing_invoice_number" in d:
                assert status == "needs_review" and "required_missing" in rules
                caught["missing_number"] += 1
            if "date_without_year" in d:
                assert status == "needs_review" and "missing_year" in rules
                caught["missing_year"] += 1
            if occ.format == "pdf" and not d - {"multi_page"} and "currency" in occ.expected:
                clean_pdfs += 1
                clean_ok += status == "auto_approved"
    # 8 planted bad totals, plus re-sent / re-scanned copies of them
    assert caught["totals"] >= 8
    assert caught["missing_number"] > 20 and caught["missing_year"] > 10
    assert clean_pdfs > 50 and clean_ok == clean_pdfs


def test_contact_problems_are_caught(
    db_session: Session,
    ingested: dict[str, IngestedFile],
    invoices_gt: GroundTruth,
    contacts_gt: GroundTruth,
    corpus_dir: Path,
) -> None:
    gts = {"invoices": invoices_gt, "contacts": contacts_gt}
    stats = _run(db_session, ingested, gts, "contacts", corpus_dir)
    assert stats.by_status["auto_approved"] / stats.records > 0.85
    verdicts = _verdicts(db_session)
    partial = uk_national = 0
    for r in contacts_gt.records:
        for occ in r.occurrences:
            if "partial_name" in occ.defects:
                status, rules = _verdict_for(verdicts, occ)
                assert status == "needs_review" and "required_missing" in rules
                partial += 1
            phone = occ.expected.get("phone", "")
            if occ.source.endswith("crm_export.csv") and phone.startswith("+44"):
                status, rules = _verdict_for(verdicts, occ)
                # national UK formats can't be made international safely -> review
                uk_national += "phone_format" in rules
    assert partial > 5 and uk_national > 5


def test_revalidating_replaces_previous_verdicts(
    db_session: Session,
    ingested: dict[str, IngestedFile],
    invoices_gt: GroundTruth,
    contacts_gt: GroundTruth,
    corpus_dir: Path,
) -> None:
    gts = {"invoices": invoices_gt, "contacts": contacts_gt}
    first = _run(db_session, ingested, gts, "contacts", corpus_dir)
    again = validate_pack(db_session, "contacts", SETTINGS)
    assert again.by_status == first.by_status
    n = db_session.scalar(select(func.count()).select_from(Validation))
    assert n == first.records
    stricter = validate_pack(db_session, "contacts",
                             Settings(_env_file=None, review_confidence=0.99))  # fmt: skip
    assert stricter.by_status["needs_review"] > first.by_status["needs_review"]
    # a new threshold is a new validator version: both sets of verdicts are kept
    assert db_session.scalar(select(func.count()).select_from(Validation)) == 2 * first.records
