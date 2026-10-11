"""End-to-end extraction on the real corpus, in Postgres, with the fake LLM (no API key)."""

from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from data_gen.ground_truth import GroundTruth
from pipeline.config import Settings
from pipeline.extract.client import LLMRequest, LLMResponse
from pipeline.extract.runner import Extractor, estimate
from pipeline.ingest.models import IngestedFile, location_covers
from pipeline.store.ingest_repo import save_ingest
from pipeline.store.models import (
    ColumnMapping,
    ExtractedRecord,
    ExtractionResult,
    LLMCall,
    RawRecordRow,
    SourceFile,
)
from tests.fakes import FakeLLM, refuse_for

SETTINGS = Settings(_env_file=None, anthropic_api_key="sk-test-not-real", extract_workers=4)


def _load(db: Session, ingested: dict[str, IngestedFile], pack: str) -> None:
    save_ingest(db, pack, [f for p, f in ingested.items() if p.startswith(f"{pack}/")])


def _extract(db: Session, fake: FakeLLM, pack: str, corpus_dir: Path, **kw: Any) -> Any:
    return Extractor(db, fake, pack, SETTINGS, files_root=corpus_dir, log=lambda _: None).run(**kw)


def _gts(invoices_gt: GroundTruth, contacts_gt: GroundTruth) -> dict[str, GroundTruth]:
    return {"invoices": invoices_gt, "contacts": contacts_gt}


def _records(
    db: Session, source: str
) -> list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]]:
    """(raw_record.location, fields, field_meta) for every extracted record of one file."""
    q = (
        select(RawRecordRow.location, ExtractedRecord.fields, ExtractedRecord.field_meta)
        .join(ExtractionResult, ExtractionResult.raw_record_id == RawRecordRow.id)
        .join(ExtractedRecord, ExtractedRecord.result_id == ExtractionResult.id)
        .join(SourceFile, SourceFile.id == RawRecordRow.source_file_id)
        .where(SourceFile.path == source)
    )
    return [(loc, fields, meta) for loc, fields, meta in db.execute(q)]


@pytest.mark.parametrize("pack", ["invoices", "contacts"])
def test_every_raw_record_gets_a_result(
    db_session: Session,
    ingested: dict[str, IngestedFile],
    invoices_gt: GroundTruth,
    contacts_gt: GroundTruth,
    corpus_dir: Path,
    pack: str,
) -> None:
    _load(db_session, ingested, pack)
    fake = FakeLLM(_gts(invoices_gt, contacts_gt), pack)
    stats = _extract(db_session, fake, pack, corpus_dir)
    n_raw = db_session.scalar(select(func.count()).select_from(RawRecordRow))
    n_results = db_session.scalar(select(func.count()).select_from(ExtractionResult))
    assert n_raw == n_results == stats.raw_records
    assert stats.by_status["failed"] == 0
    assert stats.escalated == 0  # the fake is confident and grounded
    # Structured sources cost one call per header layout, not per row.
    mappings = db_session.scalar(select(func.count()).select_from(ColumnMapping))
    assert mappings is not None and 1 <= mappings <= 10
    assert stats.by_method["column_mapping"] > 200
    assert stats.api_calls == len(fake.requests) == mappings + stats.by_method["llm"]
    calls = db_session.scalars(select(LLMCall)).all()
    assert all(c.cost_usd > 0 for c in calls)


def test_mapped_invoice_rows_match_ground_truth(
    db_session: Session,
    ingested: dict[str, IngestedFile],
    invoices_gt: GroundTruth,
    contacts_gt: GroundTruth,
    corpus_dir: Path,
) -> None:
    _load(db_session, ingested, "invoices")
    _extract(
        db_session, FakeLLM(_gts(invoices_gt, contacts_gt), "invoices"), "invoices", corpus_dir
    )
    checked = Counter[str]()
    for src in ("invoices/ap_register.csv", "invoices/procurement_export.csv",
                "invoices/ap_aging.xlsx"):  # fmt: skip
        records = _records(db_session, src)
        occs = [o for r in invoices_gt.records for o in r.occurrences if o.source == src]
        assert len(records) == len(occs)
        for occ in occs:
            (match,) = [(f, m) for loc, f, m in records if location_covers(loc, occ.location)]
            fields, meta = match
            for name in ("vendor_name", "invoice_number", "total", "payment_status", "due_date"):
                if name in occ.expected:
                    want = occ.expected[name]
                    want = want.strip() if isinstance(want, str) else want  # copy trims ends
                    got = fields[name]
                    if name == "total":  # compare money as numbers: 15554.6 == 15554.60
                        got, want = Decimal(got), Decimal(want)
                    assert got == want, (src, name, occ.location)
                    checked[name] += 1
            if "date_without_year" in occ.defects:
                assert fields["invoice_date"] is None
                assert "missing_year" in meta["invoice_date"]["flags"]
            else:
                assert fields["invoice_date"] == occ.expected["invoice_date"]
            if "currency" in occ.expected:
                assert fields["currency"] == occ.expected["currency"]
    assert checked["vendor_name"] == 538 and checked["payment_status"] > 250


def test_mapped_contact_rows_match_ground_truth(
    db_session: Session,
    ingested: dict[str, IngestedFile],
    invoices_gt: GroundTruth,
    contacts_gt: GroundTruth,
    corpus_dir: Path,
) -> None:
    _load(db_session, ingested, "contacts")
    _extract(
        db_session, FakeLLM(_gts(invoices_gt, contacts_gt), "contacts"), "contacts", corpus_dir
    )
    compared = 0
    for src in ("contacts/crm_export.csv", "contacts/exports/webform_submissions.jsonl"):
        records = _records(db_session, src)
        for r in contacts_gt.records:
            for occ in r.occurrences:
                if occ.source != src:
                    continue
                (fields,) = [f for loc, f, _ in records if location_covers(loc, occ.location)]
                for name in ("email", "company"):
                    if name in occ.expected:
                        assert fields[name] == occ.expected[name].strip()
                        compared += 1
                assert fields["first_name"].lower() == occ.expected["first_name"].lower()
                phone = occ.expected.get("phone")
                if phone and phone.startswith("+1"):
                    assert fields["phone"] == phone
    assert compared > 300


def test_scanned_pdfs_are_sent_as_documents(
    db_session: Session,
    ingested: dict[str, IngestedFile],
    invoices_gt: GroundTruth,
    contacts_gt: GroundTruth,
    corpus_dir: Path,
) -> None:
    _load(db_session, ingested, "invoices")
    fake = FakeLLM(_gts(invoices_gt, contacts_gt), "invoices")
    _extract(db_session, fake, "invoices", corpus_dir)
    scans = [
        r for r in fake.requests if r.meta.get("source", "").split("/")[-1].startswith("scan_")
    ]
    assert len(scans) == 20
    for req in scans:
        assert req.content[0]["type"] == "document"
        assert req.content[0]["source"]["data"].startswith("JVBER")  # base64 of "%PDF"
    text_pdf = next(r for r in fake.requests if r.meta.get("kind") == "document"
                    and r.content[0]["type"] == "text")  # fmt: skip
    assert "L1: " in text_pdf.content[0]["text"]


def test_rerun_is_free_and_cache_survives_deleted_results(
    db_session: Session,
    ingested: dict[str, IngestedFile],
    invoices_gt: GroundTruth,
    contacts_gt: GroundTruth,
    corpus_dir: Path,
) -> None:
    _load(db_session, ingested, "contacts")
    fake = FakeLLM(_gts(invoices_gt, contacts_gt), "contacts")
    first = _extract(db_session, fake, "contacts", corpus_dir, limit=15)
    assert first.api_calls > 0
    # A second run picks up where the first stopped: none of the first 15 are redone.
    second = _extract(db_session, fake, "contacts", corpus_dir, limit=15)
    llm_results = db_session.scalars(
        select(ExtractionResult.raw_record_id).where(ExtractionResult.method == "llm")
    ).all()
    assert second.raw_records == 15 and len(llm_results) == len(set(llm_results)) == 30
    # Wipe results: the next run re-derives them entirely from the llm_calls cache.
    db_session.execute(delete(ExtractionResult))
    before = len(fake.requests)
    third = _extract(db_session, fake, "contacts", corpus_dir, limit=15)
    assert third.api_calls == len(fake.requests) - before == 0
    assert third.cache_hits >= 15


def _one_doc(db: Session, source_part: str) -> ExtractionResult:
    q = (
        select(ExtractionResult)
        .join(RawRecordRow, RawRecordRow.id == ExtractionResult.raw_record_id)
        .join(SourceFile, SourceFile.id == RawRecordRow.source_file_id)
        .where(SourceFile.path.contains(source_part), ExtractionResult.method == "llm")
    )
    return db.scalars(q).first()  # type: ignore[return-value]


def test_refusal_escalates_to_the_bigger_model(
    db_session: Session,
    ingested: dict[str, IngestedFile],
    invoices_gt: GroundTruth,
    contacts_gt: GroundTruth,
    corpus_dir: Path,
) -> None:
    _load(db_session, ingested, "invoices")
    fake = FakeLLM(_gts(invoices_gt, contacts_gt), "invoices")
    target = "tidewater_T00149.pdf"
    fake.overrides.append(refuse_for(SETTINGS.extract_model, target))
    stats = _extract(db_session, fake, "invoices", corpus_dir)
    res = _one_doc(db_session, target)
    assert res.escalated and res.status == "ok"
    assert res.model == SETTINGS.escalation_model
    assert "refusal" in res.reasons[0]
    assert len(res.llm_call_ids) == 2
    assert stats.escalated == 1
    assert res.records[0].fields["invoice_number"] == "T00149"


def test_broken_output_is_retried_then_escalated(
    db_session: Session,
    ingested: dict[str, IngestedFile],
    invoices_gt: GroundTruth,
    contacts_gt: GroundTruth,
    corpus_dir: Path,
) -> None:
    _load(db_session, ingested, "invoices")
    fake = FakeLLM(_gts(invoices_gt, contacts_gt), "invoices")
    target = "tidewater_T00149.pdf"
    seen: list[str] = []

    def broken(req: LLMRequest) -> LLMResponse | None:
        if target in str(req.meta.get("source")) and req.model == SETTINGS.extract_model:
            seen.append(req.model)
            return LLMResponse("invalid", None, req.model, "end_turn", error="invalid JSON")
        return None

    fake.overrides.append(broken)
    _extract(db_session, fake, "invoices", corpus_dir)
    assert seen == [SETTINGS.extract_model] * 2  # first try + one retry
    res = _one_doc(db_session, target)
    assert res.escalated and res.status == "ok" and len(res.llm_call_ids) == 3
    failed_calls = db_session.scalars(select(LLMCall).where(LLMCall.status == "invalid")).all()
    assert len(failed_calls) == 2 and all(c.cache_key is None for c in failed_calls)


def test_low_confidence_escalates_and_total_failure_is_recorded(
    db_session: Session,
    ingested: dict[str, IngestedFile],
    invoices_gt: GroundTruth,
    contacts_gt: GroundTruth,
    corpus_dir: Path,
) -> None:
    _load(db_session, ingested, "invoices")
    fake = FakeLLM(_gts(invoices_gt, contacts_gt), "invoices")
    unsure, hopeless = "tidewater_T00149.pdf", "Invoice_INV003298.pdf"

    def hook(req: LLMRequest) -> LLMResponse | None:
        src = str(req.meta.get("source"))
        if unsure in src and req.model == SETTINGS.extract_model:
            data = FakeLLM._doc(fake, req)
            for rec in data["records"]:
                rec["total"]["confidence"] = 0.2
            return LLMResponse("ok", data, req.model, "end_turn", input_tokens=5)
        if hopeless in src:
            return LLMResponse("error", None, req.model, error="500: overloaded")
        return None

    fake.overrides.append(hook)
    _extract(db_session, fake, "invoices", corpus_dir)
    res = _one_doc(db_session, unsure)
    assert res.escalated and res.model == SETTINGS.escalation_model
    assert any("low confidence total" in r for r in res.reasons)
    dead = _one_doc(db_session, hopeless)
    assert dead.status == "failed" and dead.records == []
    assert any("escalation failed" in r for r in dead.reasons)


def test_missing_source_file_for_vision_fails_cleanly(
    db_session: Session,
    ingested: dict[str, IngestedFile],
    invoices_gt: GroundTruth,
    contacts_gt: GroundTruth,
    tmp_path: Path,
) -> None:
    _load(db_session, ingested, "invoices")
    fake = FakeLLM(_gts(invoices_gt, contacts_gt), "invoices")
    stats = Extractor(db_session, fake, "invoices", SETTINGS, files_root=tmp_path,
                      log=lambda _: None).run()  # fmt: skip
    assert stats.by_status["failed"] == 20
    res = _one_doc(db_session, "scan_")
    assert "source file not found" in res.reasons[0]


def test_estimate_makes_no_calls(db_session: Session, ingested: dict[str, IngestedFile]) -> None:
    _load(db_session, ingested, "contacts")
    text = estimate(db_session, "contacts", SETTINGS)
    assert "documents" in text and "estimated cost" in text
    assert db_session.scalar(select(func.count()).select_from(LLMCall)) == 0
