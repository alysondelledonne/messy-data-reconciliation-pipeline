"""Validation rules for the invoices pack. Each rule is a pure function of one record."""

from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from pipeline.validate.types import Finding, RecordView, Rule

RULES_VERSION = "inv-r1"
CENT = Decimal("0.01")
EARLIEST = date(2000, 1, 1)


def _dec(v: Any) -> Decimal | None:
    if v is None:
        return None
    try:
        return Decimal(str(v))
    except InvalidOperation:
        return None


def _date(v: Any) -> date | None:
    try:
        return date.fromisoformat(str(v)) if v else None
    except ValueError:
        return None


def money(d: Decimal) -> str:
    return f"{d:,.2f}"


def identifiable(rec: RecordView) -> list[Finding]:
    """With no vendor, no number and no amount there is nothing to reconcile."""
    if not any(rec.get(f) for f in ("vendor_name", "invoice_number")) or not any(
        rec.get(f) is not None for f in ("total", "subtotal")
    ):
        return [
            Finding(
                "unidentifiable",
                "reject",
                "Not enough to identify this invoice: it needs a vendor or an invoice "
                "number, plus an amount.",
                ("vendor_name", "invoice_number", "total"),
            )
        ]
    return []


def line_items_sum(rec: RecordView) -> list[Finding]:
    items = rec.get("line_items") or []
    subtotal = _dec(rec.get("subtotal"))
    amounts = [_dec(i.get("amount")) for i in items]
    if not items or subtotal is None or any(a is None for a in amounts):
        return []
    total = sum((a for a in amounts if a is not None), Decimal(0))
    if abs(total - subtotal) > CENT:
        return [
            Finding(
                "line_items_sum",
                "review",
                f"Line items add up to {money(total)} but the subtotal says "
                f"{money(subtotal)} (off by {money(abs(subtotal - total))}).",
                ("line_items", "subtotal"),
            )
        ]
    return []


def line_amounts(rec: RecordView) -> list[Finding]:
    out = []
    for i, item in enumerate(rec.get("line_items") or [], 1):
        qty, price, amount = (_dec(item.get(k)) for k in ("quantity", "unit_price", "amount"))
        if qty is None or price is None or amount is None:
            continue
        if abs(qty * price - amount) > max(CENT, abs(amount) * Decimal("0.005")):
            out.append(
                Finding(
                    "line_amount",
                    "review",
                    f"Line {i}: {qty} x {money(price)} = {money(qty * price)}, but the "
                    f"line says {money(amount)}.",
                    ("line_items",),
                )
            )
    return out


def _line_sum(rec: RecordView) -> Decimal | None:
    amounts = [_dec(i.get("amount")) for i in rec.get("line_items") or []]
    if not amounts or any(a is None for a in amounts):
        return None
    return sum((a for a in amounts if a is not None), Decimal(0))


def totals_add_up(rec: RecordView) -> list[Finding]:
    """Subtotal + tax = total. Layouts without a subtotal are checked from the line items."""
    subtotal, tax, total = (_dec(rec.get(k)) for k in ("subtotal", "tax", "total"))
    base, label, fields = subtotal, "Subtotal", ("subtotal", "tax", "total")
    if base is None:
        base, label, fields = _line_sum(rec), "Line items", ("line_items", "tax", "total")
    if base is None or total is None:
        return []
    expected = base + (tax or Decimal(0))
    if abs(expected - total) > CENT:
        tax_part = f" + tax {money(tax)}" if tax is not None else ""
        return [
            Finding(
                "totals_add_up",
                "review",
                f"{label} {money(base)}{tax_part} = {money(expected)}, but the total "
                f"says {money(total)} (off by {money(abs(total - expected))}).",
                fields,
            )
        ]
    return []


def plausible_amounts(rec: RecordView) -> list[Finding]:
    out = []
    total, subtotal, tax = (_dec(rec.get(k)) for k in ("total", "subtotal", "tax"))
    if total is not None and total <= 0:
        out.append(
            Finding(
                "non_positive_total",
                "review",
                f"Total is {money(total)}; expected a positive amount (credit note?).",
                ("total",),
            )
        )
    if subtotal and tax is not None and subtotal > 0:
        rate = tax / subtotal
        if rate < 0 or rate > Decimal("0.30"):
            out.append(
                Finding(
                    "tax_rate",
                    "review",
                    f"Tax is {float(rate) * 100:.1f}% of the subtotal; expected 0-30%.",
                    ("tax", "subtotal"),
                )
            )
    return out


def plausible_dates(rec: RecordView, today: date | None = None) -> list[Finding]:
    today = today or date.today()
    out = []
    inv, due = _date(rec.get("invoice_date")), _date(rec.get("due_date"))
    if inv and not (EARLIEST <= inv <= today + timedelta(days=30)):
        out.append(
            Finding(
                "invoice_date_range",
                "review",
                f"Invoice date {inv} is implausible (before {EARLIEST} or in the future).",
                ("invoice_date",),
            )
        )
    if inv and due:
        if due < inv:
            out.append(
                Finding(
                    "due_before_invoice",
                    "review",
                    f"Due date {due} is before the invoice date {inv}.",
                    ("due_date", "invoice_date"),
                )
            )
        elif (due - inv).days > 365:
            out.append(
                Finding(
                    "due_too_late",
                    "review",
                    f"Due date {due} is more than a year after the invoice date.",
                    ("due_date",),
                )
            )
    return out


RULES: tuple[Rule, ...] = (
    identifiable,
    line_items_sum,
    line_amounts,
    totals_add_up,
    plausible_amounts,
    plausible_dates,
)
