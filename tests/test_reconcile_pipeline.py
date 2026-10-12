"""Reconciliation on the real corpus (fake LLM, Postgres): ingest -> extract -> validate ->
reconcile, scored against ground truth."""

from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from data_gen.ground_truth import GroundTruth
from evals.reconcile_check import record_truth, score
from pipeline.config import Settings
from pipeline.extract.runner import Extractor
from pipeline.ingest.models import IngestedFile
from pipeline.reconcile.engine import ReconcileStats, reconcile_pack
from pipeline.store.ingest_repo import save_ingest
from pipeline.store.models import (
    Entity,
    GoldenRecord,
    MatchDecision,
    RecordLink,
)
from pipeline.validate.runner import validate_pack
from tests.fakes import FakeLLM

SETTINGS = Settings(_env_file=None, anthropic_api_key="sk-test-not-real", extract_workers=4)


def _prepare(
    db: Session, ingested: dict[str, IngestedFile], gts: dict[str, GroundTruth], pack: str,
    corpus_dir: Path,
) -> FakeLLM:  # fmt: skip
    save_ingest(db, pack, [f for p, f in ingested.items() if p.startswith(f"{pack}/")])
    fake = FakeLLM(gts, pack)
    Extractor(db, fake, pack, SETTINGS, files_root=corpus_dir, log=lambda _: None).run()
    validate_pack(db, pack, SETTINGS)
    return fake


def _golden_by_truth(db: Session, gt: GroundTruth) -> dict[str, GoldenRecord]:
    """true record_id -> the golden record holding it."""
    truth_of = record_truth(db, gt)
    out: dict[str, GoldenRecord] = {}
    for golden, rec_id in db.execute(
        select(GoldenRecord, RecordLink.extracted_record_id).join(RecordLink)
    ):
        if rec_id in truth_of:
            out[truth_of[rec_id]] = golden
    return out


def _rules(g: GoldenRecord) -> set[str]:
    return {f["rule"] for f in g.findings}


def _gts(invoices_gt: GroundTruth, contacts_gt: GroundTruth) -> dict[str, GroundTruth]:
    return {"invoices": invoices_gt, "contacts": contacts_gt}


def test_invoices_reconcile_to_the_true_records(
    db_session: Session, ingested: dict[str, IngestedFile], invoices_gt: GroundTruth,
    contacts_gt: GroundTruth, corpus_dir: Path,
) -> None:  # fmt: skip
    fake = _prepare(db_session, ingested, _gts(invoices_gt, contacts_gt), "invoices", corpus_dir)
    stats = reconcile_pack(db_session, "invoices", SETTINGS, fake)
    s = score(db_session, invoices_gt)
    assert s.pure == s.golden  # no golden record mixes two invoices
    assert s.whole == s.true_records == len(invoices_gt.records)  # none split
    assert s.entity_pairs["fp"] == 0 and s.entity_pairs["fn"] == 0  # lookalikes kept apart
    assert stats.entities == len(invoices_gt.entities)
    assert any(k.startswith("llm_") for k in stats.matches)  # a close call went to the model
    # a bad total on an invoice's only source can't be corrected, only flagged
    lone_mismatches = sum(
        len(r.occurrences) == 1 and "total_mismatch" in r.occurrences[0].defects
        for r in invoices_gt.records
    )
    assert s.field_totals["total"] - s.field_hits["total"] == lone_mismatches
    for name in ("subtotal", "tax", "payment_status", "currency", "line_items", "po_number"):
        assert s.field_hits[name] == s.field_totals[name], name

    golden = _golden_by_truth(db_session, invoices_gt)
    checked: dict[str, int] = {"stale": 0, "mismatch": 0, "unrecorded": 0, "dup": 0}
    for r in invoices_gt.records:
        g = golden[r.record_id]
        defects = {d for o in r.occurrences for d in o.defects}
        sources = {o.source for o in r.occurrences}
        if "stale_status" in defects:
            # the register wins over the stale document, and the conflict is reviewed
            assert g.fields["payment_status"] == r.truth["payment_status"]
            assert g.status == "needs_review" and "source_conflict" in _rules(g)
            checked["stale"] += 1
        if defects & {"total_mismatch", "subtotal_mismatch"} and len(sources) > 1:
            assert g.fields["total"] == r.truth["total"]
            checked["mismatch"] += 1
        if any(s.endswith(".pdf") for s in sources) and not any(
            "ap_register" in s for s in sources
        ):
            assert "not_in_ap_register" in _rules(g) and g.status == "needs_review"
            checked["unrecorded"] += 1
        if defects & {"reissued_copy", "rescan", "exact_copy"}:
            assert "duplicate_document" in _rules(g)
            checked["dup"] += 1
    assert checked["stale"] == 5 and checked["mismatch"] >= 3
    assert checked["unrecorded"] > 10 and checked["dup"] >= 4
    # every value is traceable: provenance names the supporting records for each field
    any_golden = next(iter(golden.values()))
    assert set(any_golden.provenance) == set(any_golden.fields)
    assert all("rule" in p and "support" in p for p in any_golden.provenance.values())


def test_contacts_reconcile_to_the_true_people(
    db_session: Session, ingested: dict[str, IngestedFile], invoices_gt: GroundTruth,
    contacts_gt: GroundTruth, corpus_dir: Path,
) -> None:  # fmt: skip
    fake = _prepare(db_session, ingested, _gts(invoices_gt, contacts_gt), "contacts", corpus_dir)
    reconcile_pack(db_session, "contacts", SETTINGS, fake)
    s = score(db_session, contacts_gt)
    assert s.pure == s.golden  # namesakes and look-alike companies never merged
    assert s.whole / s.true_records > 0.97
    assert s.entity_pairs["fp"] == 0 and s.entity_pairs["fn"] == 0
    for name in ("last_name", "city", "country", "company"):
        assert s.field_hits[name] / s.field_totals[name] > 0.99, name
    assert s.field_hits["phone"] / s.field_totals["phone"] > 0.95

    # stale titles, phones and employers lose to newer sources
    golden = _golden_by_truth(db_session, contacts_gt)
    stale = right = 0
    for r in contacts_gt.records:
        for o in r.occurrences:
            for d in o.defects:
                fld = {"stale_title": "job_title", "stale_phone": "phone",
                       "stale_company": "company"}.get(d)  # fmt: skip
                # knowable only if some other source states the current value
                if fld is None or not any(
                    x.expected.get(fld) == r.truth[fld] for x in r.occurrences
                ):
                    continue
                stale += 1
                right += golden[r.record_id].fields[fld] == r.truth[fld]
    assert stale > 15 and right / stale > 0.9


def test_rerun_is_idempotent_and_reuses_decisions(
    db_session: Session, ingested: dict[str, IngestedFile], invoices_gt: GroundTruth,
    contacts_gt: GroundTruth, corpus_dir: Path,
) -> None:  # fmt: skip
    fake = _prepare(db_session, ingested, _gts(invoices_gt, contacts_gt), "contacts", corpus_dir)
    first = reconcile_pack(db_session, "contacts", SETTINGS, fake)
    calls = len([r for r in fake.requests if r.purpose == "entity_match"])
    again = reconcile_pack(db_session, "contacts", SETTINGS, fake)
    assert len([r for r in fake.requests if r.purpose == "entity_match"]) == calls  # no new calls
    assert _same(first, again)
    assert set(again.matches) == {"decided_before:llm"}
    n_golden = db_session.scalar(select(func.count()).select_from(GoldenRecord))
    assert n_golden == first.golden
    assert db_session.scalar(select(func.count()).select_from(Entity)) == first.entities


def _same(a: ReconcileStats, b: ReconcileStats) -> bool:
    keys: tuple[str, ...] = ("golden", "entities", "by_status", "conflicts", "links")
    return all(getattr(a, k) == getattr(b, k) for k in keys)


def test_a_human_decision_overrides_the_model(
    db_session: Session, ingested: dict[str, IngestedFile], invoices_gt: GroundTruth,
    contacts_gt: GroundTruth, corpus_dir: Path,
) -> None:  # fmt: skip
    fake = _prepare(db_session, ingested, _gts(invoices_gt, contacts_gt), "contacts", corpus_dir)
    first = reconcile_pack(db_session, "contacts", SETTINGS, fake)
    row = db_session.scalars(
        select(MatchDecision).where(MatchDecision.decision == "same").order_by(MatchDecision.id)
    ).first()
    assert row is not None
    row.decision, row.decided_by = "different", "human:reviewer"
    db_session.commit()
    second = reconcile_pack(db_session, "contacts", SETTINGS, fake)
    assert second.entities == first.entities + 1
    assert second.matches["decided_before:human"] == 1
    db_session.refresh(row)
    assert row.decided_by == "human:reviewer"  # the model never overwrites a person


def test_without_a_model_close_calls_wait_for_a_human(
    db_session: Session, ingested: dict[str, IngestedFile], invoices_gt: GroundTruth,
    contacts_gt: GroundTruth, corpus_dir: Path,
) -> None:  # fmt: skip
    _prepare(db_session, ingested, _gts(invoices_gt, contacts_gt), "invoices", corpus_dir)
    stats = reconcile_pack(db_session, "invoices", SETTINGS, None)
    pending = db_session.scalars(
        select(MatchDecision).where(MatchDecision.decision == "pending")
    ).all()
    assert pending and stats.matches["pending"] == len(pending)
    flagged: list[Any] = [
        g for g in db_session.scalars(select(GoldenRecord)) if "entity_match_pending" in _rules(g)
    ]
    assert flagged and all(g.status == "needs_review" for g in flagged)
    # still never merges the lookalikes
    assert score(db_session, invoices_gt).entity_pairs["fp"] == 0
