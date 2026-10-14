"""Human review (fake LLM, Postgres): approvals and corrections survive reconcile
rebuilds, changed sources send a review back to the queue, name-match decisions take
effect, every action is audited, previews render, and the app's pages run."""

from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from streamlit.testing.v1 import AppTest

from data_gen.ground_truth import GroundTruth
from pipeline.domains import get_pack
from pipeline.ingest.models import IngestedFile
from pipeline.reconcile.engine import reconcile_pack
from pipeline.review import queries, service, ui
from pipeline.review.preview import build_preview, evidence_of, mark_lines
from pipeline.store.models import GoldenRecord, GoldenReview, MatchDecision, ReviewEvent
from tests.test_reconcile_pipeline import SETTINGS, _prepare

APP = Path(__file__).resolve().parents[1] / "app"


def _gts(invoices_gt: GroundTruth, contacts_gt: GroundTruth) -> dict[str, GroundTruth]:
    return {"invoices": invoices_gt, "contacts": contacts_gt}


def _by_key(db: Session, key: str) -> GoldenRecord:
    return db.scalars(select(GoldenRecord).where(GoldenRecord.golden_key == key)).one()


# --- no database -------------------------------------------------------------------------


def test_reviewer_input_is_parsed_by_the_pack_schema() -> None:
    info = get_pack("invoices")
    assert service.parse_value(info, "total", " 1,234.50 ".replace(",", "")) == "1234.50"
    assert service.parse_value(info, "invoice_date", "2024-03-01") == "2024-03-01"
    assert service.parse_value(info, "po_number", "") is None
    with pytest.raises(ValueError):
        service.parse_value(info, "invoice_date", "not a date")
    with pytest.raises(ValueError):
        service.parse_value(info, "nonexistent", "x")
    with pytest.raises(ValueError):
        service.actor("   ")
    assert service.actor(" Sam  Lee ") == "human:Sam Lee"


def test_evidence_lines_are_marked() -> None:
    lines = mark_lines("Invoice No: A-1\nTotal: $5.00\nThanks", ["$5.00"], first=10)
    assert [(n, hit) for n, _, hit in lines] == [(10, False), (11, True), (12, False)]


def test_golden_key_ignores_order() -> None:
    assert service.golden_key(["b", "a"]) == service.golden_key(["a", "b"])
    assert service.golden_key(["a"]) != service.golden_key(["a", "b"])


# --- with the database -------------------------------------------------------------------


def test_reviews_survive_rebuilds_and_are_audited(
    db_session: Session,
    ingested: dict[str, IngestedFile],
    invoices_gt: GroundTruth,
    contacts_gt: GroundTruth,
    corpus_dir: Path,
) -> None:
    fake = _prepare(db_session, ingested, _gts(invoices_gt, contacts_gt), "invoices", corpus_dir)
    reconcile_pack(db_session, "invoices", SETTINGS, fake)
    queue = queries.review_queue(db_session, "invoices")
    assert queue
    first, second = (db_session.get(GoldenRecord, r["id"]) for r in queue[:2])
    assert first is not None and second is not None
    k1, k2 = first.golden_key, second.golden_key

    service.approve(db_session, first.id, "Sam", "checked against the PDF")
    findings = service.correct(db_session, second.id, "currency", "EUR", "Sam", SETTINGS, "per PDF")
    assert all(f["rule"] != "required_missing" or "currency" not in f["fields"] for f in findings)
    with pytest.raises(ValueError):
        service.correct(db_session, second.id, "invoice_date", "soon", "Sam", SETTINGS)

    stats = reconcile_pack(db_session, "invoices", SETTINGS, fake)
    assert stats.reviews == {"approvals": 1, "corrections": 1}
    assert _by_key(db_session, k1).status == "approved"
    g2 = _by_key(db_session, k2)
    assert g2.fields["currency"] == "EUR"
    assert g2.provenance["currency"]["rule"] == "corrected by human:Sam"
    assert g2.provenance["currency"]["machine_value"] != "EUR"

    # Re-linking changes the sources behind the approved record (simulated by moving the
    # stored review to a key no record has): the approval is not applied blindly.
    review = db_session.scalars(select(GoldenReview)).one()
    review.golden_key = "0" * 32
    db_session.commit()
    stats = reconcile_pack(db_session, "invoices", SETTINGS, fake)
    assert stats.reviews["orphaned"] >= 1
    orphaned = [
        g
        for g in db_session.scalars(select(GoldenRecord))
        if any(f["rule"] == "review_orphaned" for f in g.findings)
    ]
    assert orphaned and all(g.status == "needs_review" for g in orphaned)

    service.unapprove(db_session, orphaned[0].id, "Sam")
    actions = list(db_session.scalars(select(ReviewEvent.action).order_by(ReviewEvent.id)))
    assert actions == ["approve", "correct", "unapprove"]
    log = queries.audit_log(db_session, "invoices")
    assert log[0]["action"] == "unapprove" and log[-1]["who"] == "human:Sam"


def test_name_match_decision_takes_effect_on_the_next_run(
    db_session: Session,
    ingested: dict[str, IngestedFile],
    invoices_gt: GroundTruth,
    contacts_gt: GroundTruth,
    corpus_dir: Path,
) -> None:
    _prepare(db_session, ingested, _gts(invoices_gt, contacts_gt), "contacts", corpus_dir)
    first = reconcile_pack(db_session, "contacts", SETTINGS, None)  # no model: all pending
    pending = queries.pending_matches(db_session, "contacts")
    assert pending and pending[0]["a_spellings"] and pending[0]["b_spellings"]
    p = pending[0]
    service.decide_match(db_session, "contacts", p["b"], p["a"], True, "Sam", "same")
    row = db_session.scalars(
        select(MatchDecision).where(MatchDecision.a == p["a"], MatchDecision.b == p["b"])
    ).one()
    assert row.decision == "same" and row.decided_by == "human:Sam"
    second = reconcile_pack(db_session, "contacts", SETTINGS, None)
    assert second.entities == first.entities - 1
    assert len(queries.pending_matches(db_session, "contacts")) == len(pending) - 1


def test_previews_and_pages_render(
    db_session: Session,
    ingested: dict[str, IngestedFile],
    invoices_gt: GroundTruth,
    contacts_gt: GroundTruth,
    corpus_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _prepare(db_session, ingested, _gts(invoices_gt, contacts_gt), "invoices", corpus_dir)
    reconcile_pack(db_session, "invoices", SETTINGS, fake)
    # A golden record built from a PDF and spreadsheet rows: every preview kind renders.
    kinds = set()
    for g in db_session.scalars(select(GoldenRecord).where(GoldenRecord.source_count >= 3)):
        for sv in queries.golden_sources(db_session, g.id):
            p = build_preview(
                db_session, sv.raw, sv.file, evidence_of(sv.field_meta), str(corpus_dir)
            )
            kinds.add(p.kind)
            if p.kind == "pages":
                assert p.pages and p.pages[0][:4] == b"\x89PNG"
                assert any(hit for _, _, hit in p.lines)  # evidence found in the text layer
            if p.kind == "table":
                assert p.highlight_row is not None and p.columns[0] in ("row", "line")
        if {"pages", "table"} <= kinds:
            break
    assert {"pages", "table"} <= kinds

    exc = queries.exceptions(db_session, "invoices")
    assert exc["duplicates"] and exc["unmatched"] and exc["conflicts"]
    assert queries.overview(db_session)["golden"]

    # The app's pages, run headless against this test's data.
    def factory() -> Session:
        return Session(bind=db_session.connection(), join_transaction_mode="create_savepoint")

    monkeypatch.setattr(ui, "SESSION_FACTORY", factory)
    monkeypatch.setenv("FILES_ROOT", str(corpus_dir))
    for page in ("overview", "review", "exceptions", "guide"):
        at = AppTest.from_file(str(APP / "views" / f"{page}.py"), default_timeout=60)
        at.session_state["pack"] = "invoices"
        at.run()
        assert not at.exception, (page, at.exception)
    at = AppTest.from_file(str(APP / "views" / "review.py"), default_timeout=60)
    at.session_state["pack"] = "invoices"
    at.session_state["reviewer"] = "Sam"
    at.run()
    assert not at.exception
    at.button(key=next(b.key for b in at.button if b.key and b.key.startswith("approve_"))).click()
    at.run()
    assert not at.exception
    n = db_session.scalar(select(func.count()).select_from(ReviewEvent))
    assert n == 1
