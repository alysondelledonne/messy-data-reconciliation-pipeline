"""How invoices are reconciled: keys, survivorship policies and cluster checks (D-035)."""

import fnmatch
import re
from datetime import date
from decimal import Decimal, InvalidOperation

from pipeline.reconcile.types import Candidate, ReconcileSpec
from pipeline.validate.types import Finding

# Which sources are the system of record for payment status. Deployment config: a real
# customer names their AP system's export here.
SYSTEM_OF_RECORD = ("*ap_register*", "*register*.csv")


def norm_number(v: object) -> str | None:
    s = re.sub(r"[^A-Za-z0-9]", "", str(v or "")).upper()
    return s or None


def _prefix(num: str) -> str | None:
    m = re.match(r"[A-Z]{2,}", num)
    return m[0] if m else None


def evidence(c: Candidate) -> tuple[set[str], set[str]]:
    num = norm_number(c.values.get("invoice_number"))
    if not num:
        return set(), set()
    prefix = _prefix(num)
    return {f"num:{num}"}, ({f"prefix:{prefix}"} if prefix else set())


def strong_keys(c: Candidate) -> list[str]:
    keys = []
    if num := norm_number(c.values.get("invoice_number")):
        keys.append(f"num:{c.entity}:{num}")
    if po := norm_number(c.values.get("po_number")):
        keys.append(f"po:{c.entity}:{po}")
    return keys


def _dec(v: object) -> Decimal | None:
    try:
        return Decimal(str(v)) if v is not None else None
    except InvalidOperation:
        return None


def soft_key(c: Candidate) -> str | None:
    total = _dec(c.values.get("total"))
    return f"{c.entity}:{total:.2f}" if total is not None and c.entity is not None else None


def compatible(a: Candidate, b: Candidate) -> bool:
    """Two different invoice numbers are two invoices."""
    na, nb = (norm_number(c.values.get("invoice_number")) for c in (a, b))
    return not (na and nb and na != nb)


def soft_match(a: Candidate, b: Candidate) -> bool:
    """Same vendor and total, dates within 3 days: used when an invoice number is missing."""
    na, nb = (
        norm_number(a.values.get("invoice_number")),
        norm_number(b.values.get("invoice_number")),
    )
    if na and nb and na != nb:
        return False
    da, db = a.values.get("invoice_date"), b.values.get("invoice_date")
    if not da or not db:
        return False
    return abs((date.fromisoformat(da) - date.fromisoformat(db)).days) <= 3


def same_number(a: object, b: object) -> bool:
    """'LSC-3177' = 'LSC3177' = 'lsc 3177'."""
    return norm_number(a) == norm_number(b)


def display_rank(name: str, value: object) -> int:
    """Show an identifier as the vendor writes it: exports often strip the separators."""
    return len(str(value or "")) if name in ("invoice_number", "po_number") else 0


def is_system_of_record(c: Candidate) -> bool:
    return any(fnmatch.fnmatch(c.source, pat) for pat in SYSTEM_OF_RECORD)


def trust(c: Candidate) -> int:
    if c.format in ("pdf", "pdf_scanned"):
        return 3  # the vendor's own document
    if is_system_of_record(c):
        return 3
    return 2 if c.format == "csv" else 1


def cluster_findings(cs: list[Candidate]) -> list[Finding]:
    out = []
    pdfs = sorted({c.source for c in cs if c.format in ("pdf", "pdf_scanned")})
    if pdfs and not any(is_system_of_record(c) for c in cs):
        out.append(Finding("not_in_ap_register", "review",
                           "Vendor invoice not found in the AP register: possibly an unrecorded "
                           f"bill ({', '.join(pdfs)}).", ()))  # fmt: skip
    if not pdfs and any(is_system_of_record(c) for c in cs):
        out.append(Finding("no_document", "info",
                           "In the AP register but no vendor document was found."))  # fmt: skip
    if len(pdfs) > 1:
        out.append(Finding("duplicate_document", "review",
                           f"The same invoice arrived as {len(pdfs)} documents "
                           f"({', '.join(pdfs)}): check it isn't paid twice.", ()))  # fmt: skip
    rows: dict[str, int] = {}
    for c in cs:
        if c.format not in ("pdf", "pdf_scanned"):
            rows[c.source] = rows.get(c.source, 0) + 1
    for source, n in sorted(rows.items()):
        if n > 1:
            out.append(Finding("duplicate_row", "info",
                               f"Appears {n} times in {source}.", ()))  # fmt: skip
    return out


SPEC = ReconcileSpec(
    entity_field="vendor_name",
    entity_kind="vendor",
    evidence=evidence,
    strong_keys=strong_keys,
    soft_key=soft_key,
    soft_match=soft_match,
    compatible=compatible,
    policies={
        "invoice_number": "majority",
        "vendor_name": "entity",
        "invoice_date": "majority",
        "due_date": "majority",
        "currency": "majority",
        "po_number": "majority",
        "line_items": "richest",
        "subtotal": "amount",
        "tax": "amount",
        "total": "amount",
        "payment_status": "system_of_record",
    },
    equivalent={"invoice_number": same_number, "po_number": same_number},
    display_rank=display_rank,
    trust=trust,
    material=frozenset({"total", "subtotal", "tax", "payment_status", "invoice_date", "currency"}),
    system_of_record=is_system_of_record,
    cluster_findings=cluster_findings,
)
