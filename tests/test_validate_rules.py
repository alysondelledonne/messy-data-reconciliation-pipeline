"""Pack rules and the validation engine, on hand-built records."""

from datetime import date
from typing import Any

import pytest

from pipeline.config import Settings
from pipeline.domains import get_pack
from pipeline.domains.contacts import rules as cr
from pipeline.domains.invoices import rules as ir
from pipeline.validate.engine import validate_record, validator_version
from pipeline.validate.types import RecordView

SETTINGS = Settings(_env_file=None)
INV = get_pack("invoices")
CON = get_pack("contacts")


def _inv(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "invoice_number": "HES-00115",
        "vendor_name": "Harborview Elevator Services LLC",
        "invoice_date": "2024-03-01",
        "due_date": "2024-03-31",
        "currency": "USD",
        "po_number": None,
        "line_items": [
            {"description": "Labor", "quantity": "2", "unit_price": "100.00", "amount": "200.00"},
            {"description": "Part", "quantity": "1", "unit_price": "50.00", "amount": "50.00"},
        ],
        "subtotal": "250.00",
        "tax": "20.00",
        "total": "270.00",
        "payment_status": None,
    }
    return base | over


def _meta(values: dict[str, Any], conf: float = 0.95) -> dict[str, Any]:
    return {
        k: {"confidence": conf, "final_confidence": conf, "evidence": "x", "grounded": True,
            "flags": []}
        for k, v in values.items()
        if v is not None
    }  # fmt: skip


def view(values: dict[str, Any], **kw: Any) -> RecordView:
    return RecordView(values=values, meta=kw.pop("meta", _meta(values)), **kw)


def rules_hit(fn: Any, values: dict[str, Any]) -> list[str]:
    return [f.rule for f in fn(view(values))]


# --- invoices ---------------------------------------------------------------------------


def test_clean_invoice_is_auto_approved() -> None:
    verdict = validate_record(INV, view(_inv()), SETTINGS)
    assert verdict.status == "auto_approved" and verdict.reasons == []


def test_line_items_must_sum_to_subtotal() -> None:
    (f,) = ir.line_items_sum(view(_inv(subtotal="200.00", total="220.00")))
    assert f.message == ("Line items add up to 250.00 but the subtotal says 200.00 (off by 50.00).")
    assert rules_hit(ir.line_items_sum, _inv(subtotal="250.01")) == []  # within a cent


def test_line_amount_is_quantity_times_price() -> None:
    bad = _inv(line_items=[{"description": "x", "quantity": "3", "unit_price": "10.00",
                            "amount": "40.00"}])  # fmt: skip
    (f,) = ir.line_amounts(view(bad))
    assert f.message == "Line 1: 3 x 10.00 = 30.00, but the line says 40.00."


def test_totals_add_up_with_and_without_subtotal() -> None:
    (f,) = ir.totals_add_up(view(_inv(total="300.00")))
    assert f.message == (
        "Subtotal 250.00 + tax 20.00 = 270.00, but the total says 300.00 (off by 30.00)."
    )
    no_sub = _inv(subtotal=None, total="300.00")  # statement layouts print no subtotal
    (g,) = ir.totals_add_up(view(no_sub))
    assert g.message.startswith("Line items 250.00 + tax 20.00 = 270.00")
    assert ir.totals_add_up(view(_inv(subtotal=None, line_items=[]))) == []


def test_plausible_amounts_and_dates() -> None:
    assert rules_hit(ir.plausible_amounts, _inv(total="-5.00")) == ["non_positive_total"]
    assert rules_hit(ir.plausible_amounts, _inv(tax="100.00", total="350.00")) == ["tax_rate"]
    today = date(2026, 1, 1)
    assert [
        f.rule
        for f in ir.plausible_dates(view(_inv(invoice_date="1999-12-31", due_date=None)), today)
    ] == ["invoice_date_range"]
    assert [f.rule for f in ir.plausible_dates(view(_inv(due_date="2024-02-01")), today)] == [
        "due_before_invoice"
    ]
    assert [f.rule for f in ir.plausible_dates(view(_inv(due_date="2025-06-01")), today)] == [
        "due_too_late"
    ]


def test_unidentifiable_invoice_is_rejected() -> None:
    blank = _inv(vendor_name=None, invoice_number=None)
    verdict = validate_record(INV, view(blank), SETTINGS)
    assert verdict.status == "rejected"
    assert any("Not enough to identify" in r for r in verdict.reasons)
    no_amount = _inv(total=None, subtotal=None)
    assert validate_record(INV, view(no_amount), SETTINGS).status == "rejected"


# --- contacts ---------------------------------------------------------------------------


def _con(**over: Any) -> dict[str, Any]:
    base = {"first_name": "Dana", "last_name": "Whitfield", "email": "dana@x.example",
            "phone": "+13125550143", "company": "Northgate", "job_title": None, "city": None,
            "country": "US"}  # fmt: skip
    return base | over


def test_clean_contact_is_auto_approved() -> None:
    assert validate_record(CON, view(_con()), SETTINGS).status == "auto_approved"


@pytest.mark.parametrize(
    ("over", "rule"),
    [
        ({"email": "dana.x.example"}, "email_format"),
        ({"email": "dana@x"}, "email_format"),
        ({"phone": "020 7946 0123"}, "phone_format"),
        ({"country": "USA"}, "country_format"),
        ({"last_name": "W."}, "name_abbreviated"),
        ({"first_name": "D4na"}, "name_shape"),
    ],
)
def test_contact_rules(over: dict[str, Any], rule: str) -> None:
    verdict = validate_record(CON, view(_con(**over)), SETTINGS)
    assert verdict.status == "needs_review"
    assert rule in {f.rule for f in verdict.findings}


def test_accented_and_hyphenated_names_are_fine() -> None:
    for first, last in [("Zoë", "Van der Berg"), ("Siobhán", "O'Brien"), ("Jean-Luc", "Côté")]:
        assert cr.name_shape(view(_con(first_name=first, last_name=last))) == []


def test_all_caps_name_is_only_info() -> None:
    verdict = validate_record(CON, view(_con(first_name="DANA", last_name="WHITFIELD")), SETTINGS)
    assert verdict.status == "auto_approved"
    assert {f.rule for f in verdict.findings} == {"name_case"}


def test_contact_with_nothing_to_match_is_rejected() -> None:
    nothing = _con(first_name=None, last_name=None, email=None, phone=None)
    assert validate_record(CON, view(nothing), SETTINGS).status == "rejected"


# --- engine: extraction-quality checks ------------------------------------------------


def test_low_confidence_and_ungrounded_values_go_to_review() -> None:
    values = _con()
    meta = _meta(values)
    meta["email"] = {"final_confidence": 0.5, "grounded": True, "flags": []}
    meta["phone"] = {"final_confidence": 0.9, "grounded": False, "flags": ["grounding_failed"]}
    verdict = validate_record(CON, view(values, meta=meta), SETTINGS)
    assert verdict.status == "needs_review"
    assert "email: low confidence (0.50)." in verdict.reasons
    assert any("phone: the value '+13125550143' was not found" in r for r in verdict.reasons)


def test_missing_required_and_unreadable_values() -> None:
    values = _inv(currency=None, invoice_date=None)
    meta = _meta(values)
    meta["invoice_date"] = {"final_confidence": 0.0, "flags": ["missing_year"]}
    meta["currency"] = {"final_confidence": 0.0, "flags": ["invalid_value: Input should be 'USD'"]}
    reasons = validate_record(INV, view(values, meta=meta), SETTINGS).reasons
    assert "Required field currency is missing." in reasons
    assert "invoice_date: the date has no year in the source." in reasons
    assert any(r.startswith("currency: the source value could not be read") for r in reasons)


def test_threshold_sits_between_escalation_and_approval() -> None:
    assert SETTINGS.confidence_threshold < SETTINGS.review_confidence
    meta = _meta(_con(), conf=0.7)  # good enough not to escalate, not good enough to auto-approve
    assert validate_record(CON, view(_con(), meta=meta), SETTINGS).status == "needs_review"


def test_failed_escalation_is_flagged_and_escalation_is_info() -> None:
    v = view(_con(), escalated=True, extraction_reasons=("escalation failed: error",))
    verdict = validate_record(CON, v, SETTINGS)
    assert verdict.status == "needs_review"
    assert {f.rule for f in verdict.findings} >= {"extraction_degraded", "escalated"}
    ok = validate_record(CON, view(_con(), escalated=True), SETTINGS)
    assert ok.status == "auto_approved"


def test_validator_version_tracks_rules_and_thresholds() -> None:
    a = validator_version(INV, SETTINGS)
    b = validator_version(INV, Settings(_env_file=None, review_confidence=0.9))
    assert a != b and a.startswith(INV.rules_version)
