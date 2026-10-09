"""Render invoices as text PDFs in seven vendor layouts.

The layouts differ in label wording, date and money formats, fonts, header style,
table columns and page size. Long invoices run across pages, with a "continued"
header on each extra page.
"""

import io
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from reportlab.lib.pagesizes import A4, LETTER
from reportlab.pdfgen import canvas

from data_gen.mess import format_amount, format_date


@dataclass(frozen=True)
class PrintedLine:
    description: str
    quantity: Decimal
    unit_price: Decimal
    amount: Decimal


@dataclass
class PrintedInvoice:
    """Exactly what gets printed. It can disagree with the truth (planted defects)."""

    vendor_name: str
    vendor_address: tuple[str, str]
    invoice_number: str
    invoice_date: date
    due_date: date
    terms_days: int
    po_number: str | None
    currency: str
    tax_rate: Decimal
    lines: list[PrintedLine]
    subtotal: Decimal
    tax: Decimal
    total: Decimal
    stamp: str | None = None  # e.g. "COPY": drawn diagonally on a re-issued duplicate


@dataclass(frozen=True)
class LayoutSpec:
    name: str
    font: str
    bold: str
    title: str
    labels: dict[str, str]
    date_style: str
    money_style: str
    header: str  # band | plain | boxed | centered
    show_unit_price: bool = True
    grid: bool = False
    print_due_date: bool = True  # False: prints "Net 30" instead of a date
    print_subtotal: bool = True
    a4: bool = False
    row_h: float = 15.0
    currency_note: bool = False
    extra: dict[str, str] = field(default_factory=dict)


L = {
    "classic": LayoutSpec(
        "classic",
        "Helvetica",
        "Helvetica-Bold",
        "INVOICE",
        {
            "number": "Invoice No.",
            "date": "Invoice Date",
            "due": "Due Date",
            "po": "PO Number",
            "bill_to": "Bill To",
            "subtotal": "Subtotal",
            "tax": "Sales Tax",
            "total": "Total Due",
            "desc": "Description",
            "qty": "Qty",
            "unit": "Unit Price",
            "amount": "Amount",
        },
        "us_long",
        "symbol",
        "plain",
        grid=True,
    ),
    "modern": LayoutSpec(
        "modern",
        "Helvetica",
        "Helvetica-Bold",
        "Invoice",
        {
            "number": "INV #",
            "date": "Date",
            "due": "Due",
            "po": "P.O.",
            "bill_to": "Billed To",
            "subtotal": "Sub-total",
            "tax": "Tax",
            "total": "TOTAL",
            "desc": "Item",
            "qty": "Hrs/Qty",
            "unit": "Rate",
            "amount": "Line Total",
        },
        "mon_d_yyyy",
        "grouped",
        "band",
        currency_note=True,
    ),
    "two_column": LayoutSpec(
        "two_column",
        "Times-Roman",
        "Times-Bold",
        "Tax Invoice",
        {
            "number": "Ref",
            "date": "Issued",
            "due": "Payment Due",
            "po": "Your Order",
            "bill_to": "Customer",
            "subtotal": "Net",
            "tax": "Tax",
            "total": "Amount Payable",
            "desc": "Particulars",
            "qty": "Quantity",
            "unit": "Each",
            "amount": "Amount",
        },
        "d_mon_yyyy",
        "symbol",
        "boxed",
        row_h=14.0,
    ),
    "compact": LayoutSpec(
        "compact",
        "Courier",
        "Courier-Bold",
        "INVOICE",
        {
            "number": "Invoice Number",
            "date": "Date",
            "due": "Terms",
            "po": "PO#",
            "bill_to": "Sold To",
            "subtotal": "SUBTOTAL",
            "tax": "TAX",
            "total": "TOTAL",
            "desc": "DESCRIPTION",
            "qty": "QTY",
            "unit": "PRICE",
            "amount": "EXT",
        },
        "us_short",
        "grouped",
        "plain",
        print_due_date=False,
        row_h=13.0,
    ),
    "statement": LayoutSpec(
        "statement",
        "Helvetica",
        "Helvetica-Bold",
        "STATEMENT OF SERVICES",
        {
            "number": "Document No.",
            "date": "Statement Date",
            "due": "Due By",
            "po": "Purchase Order",
            "bill_to": "Account",
            "subtotal": "Charges",
            "tax": "Tax",
            "total": "Amount Due",
            "desc": "Service",
            "qty": "Units",
            "unit": "",
            "amount": "Charge",
        },
        "iso",
        "symbol",
        "centered",
        show_unit_price=False,
        print_subtotal=False,
    ),
    "european": LayoutSpec(
        "european",
        "Helvetica",
        "Helvetica-Bold",
        "RECHNUNG / INVOICE",
        {
            "number": "Rechnung Nr. / Invoice No.",
            "date": "Datum / Date",
            "due": "Fällig / Due",
            "po": "Bestellnr. / PO",
            "bill_to": "Rechnungsempfänger / Bill To",
            "subtotal": "Netto / Net",
            "tax": "MwSt. / VAT",
            "total": "Gesamt / Total",
            "desc": "Beschreibung / Description",
            "qty": "Menge",
            "unit": "Einzelpreis",
            "amount": "Betrag",
        },
        "dmy_dots",
        "eu",
        "plain",
        grid=True,
        a4=True,
    ),
    "bilingual": LayoutSpec(
        "bilingual",
        "Helvetica",
        "Helvetica-Bold",
        "FACTURE / INVOICE",
        {
            "number": "N° de facture / Invoice No.",
            "date": "Date",
            "due": "Échéance / Due",
            "po": "Bon de commande / PO",
            "bill_to": "Facturer à / Bill To",
            "subtotal": "Sous-total / Subtotal",
            "tax": "Taxes (TPS/TVQ/TVH)",
            "total": "Total",
            "desc": "Description",
            "qty": "Qté / Qty",
            "unit": "Prix / Price",
            "amount": "Montant / Amount",
        },
        "iso",
        "grouped",
        "band",
        currency_note=True,
    ),
}

BILL_TO = ("Accounts Payable", "100 Example Way, Suite 400", "Springfield, ST 00000")
BOTTOM = 72.0
TOTALS_H = 80.0


def _fmt_qty(q: Decimal) -> str:
    return f"{q.normalize():f}" if q != q.to_integral() else f"{int(q)}"


def _page_plan(n_lines: int, spec: LayoutSpec, height: float) -> list[tuple[int, int]]:
    """Split line items into (start, end) chunks per page; the last page must fit totals."""
    first_top, cont_top = height - 300.0, height - 130.0
    cap_first = int((first_top - BOTTOM) // spec.row_h)
    cap_cont = int((cont_top - BOTTOM) // spec.row_h)
    chunks: list[tuple[int, int]] = []
    start, cap = 0, cap_first
    while True:
        end = min(n_lines, start + cap)
        chunks.append((start, end))
        if end >= n_lines:
            used = (end - start) * spec.row_h
            top = first_top if len(chunks) == 1 else cont_top
            if top - used - BOTTOM < TOTALS_H:
                chunks.append((end, end))  # totals alone on a final page
            return chunks
        start, cap = end, cap_cont


def render_invoice(inv: PrintedInvoice, spec: LayoutSpec) -> tuple[bytes, int]:
    """Render to PDF bytes. Returns (pdf, page_count)."""
    size = A4 if spec.a4 else LETTER
    width, height = size
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=size, invariant=True)
    c.setTitle(f"{spec.title} {inv.invoice_number}")
    c.setAuthor(inv.vendor_name)

    def money(x: Decimal) -> str:
        return format_amount(x, spec.money_style, inv.currency)

    lab = spec.labels
    plan = _page_plan(len(inv.lines), spec, height)
    n_pages = len(plan)
    left, right = 54.0, width - 54.0

    for page_no, (start, end) in enumerate(plan, 1):
        if page_no == 1:
            y = _draw_header(c, inv, spec, width, height)
        else:
            c.setFont(spec.bold, 11)
            c.drawString(left, height - 60, inv.vendor_name)
            c.setFont(spec.font, 9)
            c.drawString(left, height - 76, f"{lab['number']}: {inv.invoice_number} (continued)")
            y = height - 110.0

        # Table header
        cols = _columns(spec, left, right)
        c.setFont(spec.bold, 9)
        for key, x, align in cols:
            text = lab[key]
            if align == "r":
                c.drawRightString(x, y, text)
            else:
                c.drawString(x, y, text)
        c.line(left, y - 4, right, y - 4)
        y -= spec.row_h + 2

        c.setFont(spec.font, 9)
        for line in inv.lines[start:end]:
            values = {
                "desc": line.description,
                "qty": _fmt_qty(line.quantity),
                "unit": money(line.unit_price),
                "amount": money(line.amount),
            }
            for key, x, align in cols:
                if align == "r":
                    c.drawRightString(x, y, values[key])
                else:
                    c.drawString(x, y, values[key])
            if spec.grid:
                c.setLineWidth(0.25)
                c.line(left, y - 4, right, y - 4)
                c.setLineWidth(1)
            y -= spec.row_h

        if page_no == n_pages:
            _draw_totals(c, inv, spec, right, y - 10, money)

        # Footer
        c.setFont(spec.font, 7.5)
        c.drawString(left, 40, f"Remit to: {inv.vendor_name}, {', '.join(inv.vendor_address)}")
        c.drawRightString(right, 40, f"Page {page_no} of {n_pages}")
        if inv.stamp:
            c.saveState()
            c.setFont(spec.bold, 64)
            c.setFillGray(0.82)
            c.translate(width / 2, height / 2)
            c.rotate(35)
            c.drawCentredString(0, 0, inv.stamp)
            c.restoreState()
        c.showPage()
    c.save()
    return buf.getvalue(), n_pages


def _columns(spec: LayoutSpec, left: float, right: float) -> list[tuple[str, float, str]]:
    cols = [("desc", left, "l"), ("qty", right - 200, "r")]
    if spec.show_unit_price:
        cols.append(("unit", right - 100, "r"))
    cols.append(("amount", right, "r"))
    return cols


def _draw_header(
    c: canvas.Canvas, inv: PrintedInvoice, spec: LayoutSpec, width: float, height: float
) -> float:
    lab = spec.labels
    left, right = 54.0, width - 54.0
    top = height - 54.0

    if spec.header == "band":
        c.setFillGray(0.2)
        c.rect(0, height - 100, width, 100, stroke=0, fill=1)
        c.setFillGray(1)
        c.setFont(spec.bold, 18)
        c.drawString(left, height - 55, inv.vendor_name)
        c.setFont(spec.font, 9)
        c.drawString(left, height - 72, "  ·  ".join(inv.vendor_address))
        c.setFont(spec.bold, 14)
        c.drawRightString(right, height - 55, spec.title)
        c.setFillGray(0)
        top = height - 125
    elif spec.header == "centered":
        c.setFont(spec.bold, 16)
        c.drawCentredString(width / 2, top, inv.vendor_name)
        c.setFont(spec.font, 9)
        c.drawCentredString(width / 2, top - 14, ", ".join(inv.vendor_address))
        c.setFont(spec.bold, 12)
        c.drawCentredString(width / 2, top - 36, spec.title)
        top -= 60
    else:
        c.setFont(spec.bold, 16)
        c.drawString(left, top, inv.vendor_name)
        c.setFont(spec.font, 9)
        c.drawString(left, top - 14, inv.vendor_address[0])
        c.drawString(left, top - 26, inv.vendor_address[1])
        c.setFont(spec.bold, 20 if spec.header == "plain" else 14)
        c.drawRightString(right, top, spec.title)
        top -= 50

    meta: list[tuple[str, str]] = [
        (lab["number"], inv.invoice_number),
        (lab["date"], format_date(inv.invoice_date, spec.date_style)),
        (
            lab["due"],
            format_date(inv.due_date, spec.date_style)
            if spec.print_due_date
            else f"Net {inv.terms_days}",
        ),
    ]
    if inv.po_number:
        meta.append((lab["po"], inv.po_number))

    if spec.header == "boxed":
        c.rect(right - 230, top - 16 * len(meta) - 6, 230, 16 * len(meta) + 14, stroke=1, fill=0)
    y = top
    for label, value in meta:
        c.setFont(spec.bold, 9)
        c.drawString(right - 220, y, f"{label}:")
        c.setFont(spec.font, 9)
        c.drawRightString(right - 6, y, value)
        y -= 16

    c.setFont(spec.bold, 9)
    c.drawString(left, top, f"{lab['bill_to']}:")
    c.setFont(spec.font, 9)
    for i, line in enumerate(BILL_TO):
        c.drawString(left, top - 14 * (i + 1), line)
    if spec.currency_note:
        c.setFont(spec.font, 8)
        c.drawString(left, top - 70, f"All amounts in {inv.currency}")
    return height - 300.0 + 14


def _draw_totals(
    c: canvas.Canvas,
    inv: PrintedInvoice,
    spec: LayoutSpec,
    right: float,
    y: float,
    money: Callable[[Decimal], str],
) -> None:
    lab = spec.labels
    rate = f" ({float(inv.tax_rate * 100):g}%)" if inv.tax_rate else ""
    rows: list[tuple[str, str, bool]] = []
    if spec.print_subtotal:
        rows.append((lab["subtotal"], money(inv.subtotal), False))
    rows.append((lab["tax"] + rate, money(inv.tax), False))
    rows.append((lab["total"], money(inv.total), True))
    c.line(right - 230, y + 10, right, y + 10)
    for label, value, bold in rows:
        c.setFont(spec.bold if bold else spec.font, 10 if bold else 9)
        c.drawString(right - 230, y, label)
        c.drawRightString(right, y, value)
        y -= 16
