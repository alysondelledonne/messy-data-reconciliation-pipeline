"""A fake LLM client for tests (D-027). It answers from ground truth, never the network.

- Document requests return the ground-truth `expected` values of every occurrence
  the raw record covers, with high confidence and evidence copied from the source.
- Mapping requests are answered by a small column-name rulebook.
- `overrides` let a test script failures: refusals, invalid output, low confidence.
"""

import contextlib
import json
import threading
from collections.abc import Callable
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from data_gen.ground_truth import GroundTruth
from data_gen.mess import DATE_STYLES, PHONE_STYLES, format_amount, format_date, format_phone
from pipeline.extract.client import LLMRequest, LLMResponse
from pipeline.ingest.models import location_covers

Override = Callable[[LLMRequest], LLMResponse | None]

# exact (lower-cased, last dotted segment) column name -> list of (field, transform, value_map)
MAPPING_RULES: dict[str, dict[str, list[tuple[str, str, dict[str, str]]]]] = {
    "invoices": {
        "vendor name": [("vendor_name", "copy", {})],
        "supplier": [("vendor_name", "copy", {})],
        "invoice #": [("invoice_number", "copy", {})],
        "supplier_invoice_ref": [("invoice_number", "copy", {})],
        "document": [("invoice_number", "copy", {})],
        "inv date": [("invoice_date", "date", {})],
        "invoice_dt": [("invoice_date", "date", {})],
        "doc date": [("invoice_date", "date", {})],
        "due date": [("due_date", "date", {})],
        "amount": [("total", "amount", {})],
        "gross_amount": [("total", "amount", {})],
        "net_amount": [("subtotal", "amount", {})],
        "tax_amount": [("tax", "amount", {})],
        "curr": [("currency", "copy", {})],
        "cur": [("currency", "copy", {})],
        "currency_code": [("currency", "copy", {})],
        "po_id": [("po_number", "copy", {})],
        "status": [
            (
                "payment_status",
                "copy",
                {"paid": "paid", "p": "paid", "open": "unpaid", "o": "unpaid"},
            ),
        ],
    },
    "contacts": {
        "first name": [("first_name", "copy", {})],
        "firstname": [("first_name", "copy", {})],
        "first": [("first_name", "copy", {})],
        "last name": [("last_name", "copy", {})],
        "lastname": [("last_name", "copy", {})],
        "last": [("last_name", "copy", {})],
        "name": [("first_name", "split_name_first", {}), ("last_name", "split_name_last", {})],
        "full_name": [("first_name", "split_name_first", {}), ("last_name", "split_name_last", {})],
        "email": [("email", "copy", {})],
        "emailaddress": [("email", "copy", {})],
        "mail": [("email", "copy", {})],
        "phone": [("phone", "phone_e164", {})],
        "tel": [("phone", "phone_e164", {})],
        "phonenumber": [("phone", "phone_e164", {})],
        "account name": [("company", "copy", {})],
        "org": [("company", "copy", {})],
        "company": [("company", "copy", {})],
        "company_name": [("company", "copy", {})],
        "title": [("job_title", "copy", {})],
        "role": [("job_title", "copy", {})],
        "mailing city": [("city", "copy", {})],
        "mailing country": [("country", "country_iso2", {})],
        "country": [("country", "country_iso2", {})],
    },
}


def printed_forms(value: Any) -> list[str]:
    """Ways a value may be printed in a source, so the fake can quote real evidence."""
    s = str(value)
    forms = [s]
    try:
        d = date.fromisoformat(s)
        forms += [format_date(d, style) for style in DATE_STYLES]
    except ValueError:
        pass
    try:
        amount = Decimal(s)
        forms += [format_amount(amount, st, "USD") for st in ("grouped", "symbol", "plain")]
        forms += [format_amount(amount, st, "EUR") for st in ("eu", "eu_code")]
    except (InvalidOperation, ValueError):
        pass
    forms += {"USD": ["$"], "EUR": ["€", "EUR"], "CAD": ["CAD", "CA$", "$"]}.get(s, [])
    if s.startswith("+"):
        with contextlib.suppress(ValueError):
            forms += [format_phone(s, st) for st in PHONE_STYLES]
    return forms


class FakeLLM:
    def __init__(self, ground_truths: dict[str, GroundTruth], pack: str) -> None:
        self.pack = pack
        self.gt = ground_truths[pack]
        self.by_source: dict[str, list[Any]] = {}
        for r in self.gt.records:
            for o in r.occurrences:
                self.by_source.setdefault(o.source, []).append(o)
        self.overrides: list[Override] = []
        self.requests: list[LLMRequest] = []
        self._lock = threading.Lock()

    def complete(self, request: LLMRequest) -> LLMResponse:
        with self._lock:
            self.requests.append(request)
        for hook in self.overrides:
            if (resp := hook(request)) is not None:
                return resp
        text = json.dumps(request.content)
        data = self._mapping(request) if request.purpose == "column_mapping" else self._doc(request)
        return LLMResponse(
            "ok", data, request.model, "end_turn",
            input_tokens=len(text) // 4, output_tokens=len(json.dumps(data)) // 4, latency_ms=1,
        )  # fmt: skip

    def _mapping(self, request: LLMRequest) -> dict[str, Any]:
        rules = MAPPING_RULES[self.pack]
        entries = []
        for col in request.meta["columns"]:
            for fld, transform, vmap in rules.get(col.split(".")[-1].lower(), []):
                entries.append({
                    "field": fld, "column": col, "transform": transform, "confidence": 0.95,
                    "value_map": [{"source": k, "target": v} for k, v in vmap.items()],
                    "reason": "fake rulebook",
                })  # fmt: skip
        if "Days Past Due" in request.meta["columns"]:  # an aging report lists open items
            entries.append({
                "field": "payment_status", "column": None, "constant": "unpaid",
                "transform": "copy", "confidence": 0.9, "value_map": [],
                "reason": "report of open items",
            })  # fmt: skip
        return {"mappings": entries}

    def _doc(self, request: LLMRequest) -> dict[str, Any]:
        source = request.meta["source"]
        text = "".join(b.get("text", "") for b in request.content if b.get("type") == "text")
        scanned = any(b.get("type") == "document" for b in request.content)
        records = []
        for occ in self.by_source.get(source, []):
            if not location_covers(request.meta["location"], occ.location):
                continue
            rec = {}
            for name, value in occ.expected.items():
                if isinstance(value, list):
                    forms = [str(value[0].get("description", ""))] if value else []
                else:
                    forms = printed_forms(value)
                evidence = next((f for f in forms if f and (scanned or f in text)), "")
                rec[name] = {"value": value, "confidence": 0.95, "evidence": evidence}
            records.append(rec)
        return {"records": records}


def refuse_for(model: str, source_contains: str) -> Override:
    def hook(req: LLMRequest) -> LLMResponse | None:
        if req.model == model and source_contains in str(req.meta.get("source", "")):
            return LLMResponse("refusal", None, model, "refusal", input_tokens=10,
                               error="refusal: test")  # fmt: skip
        return None

    return hook
