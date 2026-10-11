"""Extraction logic that needs neither a database nor the network."""

from types import SimpleNamespace
from typing import Any

import anthropic
import httpx2
import pytest

from pipeline.config import Settings
from pipeline.domains.contacts.schema import Contact
from pipeline.domains.invoices.schema import Invoice
from pipeline.extract.client import AnthropicLLMClient, LLMRequest, LLMResponse, cost_usd
from pipeline.extract.core import (
    FieldMapping,
    apply_mapping,
    escalation_reasons,
    interpret_document,
    is_grounded,
    parse_mapping,
    row_columns,
)
from pipeline.extract.prompts import document_content, numbered
from pipeline.extract.schema import (
    coerce_field,
    extraction_schema,
    mapping_schema,
    required_fields,
)
from pipeline.extract.transforms import apply_transform, split_name, to_e164

SETTINGS = Settings(_env_file=None, anthropic_api_key="sk-test-not-real")


def _walk(node: Any) -> list[dict[str, Any]]:
    out = []
    if isinstance(node, dict):
        if node.get("type") == "object":
            out.append(node)
        for v in node.values():
            out += _walk(v)
    elif isinstance(node, list):
        for v in node:
            out += _walk(v)
    return out


@pytest.mark.parametrize("model", [Invoice, Contact])
def test_schema_obeys_structured_output_rules(model: type) -> None:
    schema = extraction_schema(model)
    objects = _walk(schema)
    assert objects
    for obj in objects:
        assert obj["additionalProperties"] is False
        assert set(obj["required"]) == set(obj["properties"])
    text = str(schema)
    for unsupported in ("minimum", "maximum", "minLength", "maxLength", "multipleOf"):
        assert unsupported not in text


def test_schema_field_types() -> None:
    rec = extraction_schema(Invoice)["properties"]["records"]["items"]["properties"]
    assert set(rec) == set(Invoice.model_fields)
    total = rec["total"]["properties"]["value"]["anyOf"][0]
    assert total["type"] == "string"  # Decimal travels as a string
    currency = rec["currency"]["properties"]["value"]["anyOf"][0]
    assert currency["enum"] == ["USD", "EUR", "CAD"]
    date = rec["invoice_date"]["properties"]["value"]["anyOf"][0]
    assert date == {"type": "string", "format": "date"}
    items = rec["line_items"]["properties"]["value"]["anyOf"][0]
    assert items["type"] == "array"
    from pipeline.domains.invoices.schema import LineItem

    assert set(items["items"]["properties"]) == set(LineItem.model_fields)


def test_mapping_schema_constrains_columns() -> None:
    s = mapping_schema(Contact, ["Name", "Email"])
    entry = s["properties"]["mappings"]["items"]["properties"]
    assert entry["column"]["anyOf"][0]["enum"] == ["Name", "Email"]
    assert "first_name" in entry["field"]["enum"]


def test_required_fields() -> None:
    assert required_fields(Contact) == ["first_name", "last_name"]
    assert set(required_fields(Invoice)) == {
        "invoice_number", "vendor_name", "invoice_date", "currency", "total",
    }  # fmt: skip


def test_coerce_field() -> None:
    assert coerce_field(Invoice, "total", "1234.50") == ("1234.50", None)
    assert coerce_field(Invoice, "invoice_date", "2024-04-03") == ("2024-04-03", None)
    value, err = coerce_field(Invoice, "invoice_date", "April 3")
    assert value is None and err
    assert coerce_field(Invoice, "currency", "GBP")[0] is None
    assert coerce_field(Invoice, "currency", None) == (None, None)
    items, err = coerce_field(
        Invoice, "line_items", [{"description": "x", "quantity": "2", "amount": "3.00"}]
    )
    assert err is None and items[0]["unit_price"] is None


@pytest.mark.parametrize(
    ("full", "first", "last"),
    [
        ("Dana Whitfield", "Dana", "Whitfield"),
        ("Whitfield, Dana", "Dana", "Whitfield"),
        ("WHITFIELD, DANA", "DANA", "WHITFIELD"),
        ("Sofia Van der Berg", "Sofia", "Van der Berg"),
        ("Cher", "Cher", None),
    ],
)
def test_split_name(full: str, first: str, last: str | None) -> None:
    assert split_name(full) == (first, last)


def test_to_e164() -> None:
    assert to_e164("(312) 555-0143 x53") == ("+13125550143", ["assumed_country_code_1"])
    assert to_e164("+44 20 7946 0123") == ("+442079460123", [])
    assert to_e164("020 7946 0123") == ("020 7946 0123", ["phone_not_e164"])


def test_apply_transform_uses_ingest_normalization() -> None:
    norm = {"type": "date", "value": "2024-04-03", "flags": ["ambiguous_day_month"]}
    assert apply_transform("date", "04/03/2024", norm) == ("2024-04-03", ["ambiguous_day_month"])
    partial = {"type": "date", "value": None, "partial": "--04-03", "flags": ["missing_year"]}
    assert apply_transform("date", "April 3", partial) == (None, ["missing_year"])
    assert apply_transform("amount", "$1,234.50", None)[0] == "1234.50"
    assert apply_transform("country_iso2", "United Kingdom", None) == ("GB", [])
    assert apply_transform("copy", "  ", None) == (None, [])


def test_is_grounded() -> None:
    src = "Invoice No.: HES-00115\nTotal Due: $1,234.50\nDana  Whitfield"
    assert is_grounded("HES-00115", "HES-00115", src)
    assert is_grounded("", "dana whitfield", src)  # case and whitespace insensitive
    assert is_grounded("", "1234.50", "Total 1,234.50 and more digits 123450")
    assert not is_grounded("HES-99999", "HES-99999", src)
    assert not is_grounded("", "12", src)  # too short to trust


def test_interpret_document_flags_ungrounded_and_invalid() -> None:
    data = {
        "records": [
            {
                "first_name": {"value": "Dana", "confidence": 0.9, "evidence": "Dana"},
                "last_name": {"value": "Smith", "confidence": 0.9, "evidence": "Smith"},
                "email": {"value": None, "confidence": 0, "evidence": ""},
            }
        ]
    }
    (rec,) = interpret_document(Contact, data, "Dana Whitfield, dana@x.example")
    assert rec.fields["first_name"].grounded is True
    assert rec.fields["last_name"].grounded is False
    assert rec.fields["last_name"].final_confidence == pytest.approx(0.45)
    assert "grounding_failed" in rec.fields["last_name"].flags
    assert rec.fields["phone"].value is None  # absent from response -> null
    (scan,) = interpret_document(Contact, data, None)
    assert scan.fields["last_name"].grounded is None
    assert scan.fields["last_name"].final_confidence == 0.9
    bad = {"records": [{"invoice_date": {"value": "soon", "confidence": 0.9, "evidence": "soon"}}]}
    (inv,) = interpret_document(Invoice, bad, "soon")
    assert inv.fields["invoice_date"].value is None
    assert inv.fields["invoice_date"].flags[0].startswith("invalid_value")


def _contact(first: Any, last: Any, conf: float = 0.9, source: str = "Dana Whitfield") -> Any:
    data = {"records": [{
        "first_name": {"value": first, "confidence": conf, "evidence": first or ""},
        "last_name": {"value": last, "confidence": conf, "evidence": last or ""},
    }]}  # fmt: skip
    return interpret_document(Contact, data, source)


def test_escalation_reasons() -> None:
    assert escalation_reasons(Contact, _contact("Dana", "Whitfield"), SETTINGS) == []
    missing = _contact("Dana", None)
    assert escalation_reasons(Contact, missing, SETTINGS) == []  # absent != unsure
    assert missing[0].flags == ["missing_required:last_name"]
    assert "low confidence" in escalation_reasons(Contact, _contact("Dana", "Whitfield", 0.3),
                                                  SETTINGS)[0]  # fmt: skip
    reasons = escalation_reasons(Contact, _contact("Zed", "Quux"), SETTINGS)
    assert any("not found in source" in r for r in reasons)
    assert escalation_reasons(Contact, [], SETTINGS) == []  # nothing to extract is fine


def test_parse_mapping_rejects_unknown_columns_and_keeps_best() -> None:
    data = {"mappings": [
        {"field": "first_name", "column": "First", "transform": "copy", "value_map": [],
         "confidence": 0.5, "reason": ""},
        {"field": "first_name", "column": "Given", "transform": "copy", "value_map": [],
         "confidence": 0.9, "reason": ""},
        {"field": "email", "column": "Nope", "transform": "copy", "value_map": [],
         "confidence": 0.9, "reason": ""},
    ]}  # fmt: skip
    fms = parse_mapping(Contact, data, ["First", "Given"])
    assert [(m.field, m.column) for m in fms] == [("first_name", "Given")]


def test_apply_mapping_reads_a_messy_row() -> None:
    fms = [
        FieldMapping("vendor_name", "Vendor Name", "copy", {}, 0.95),
        FieldMapping("invoice_date", "Inv Date", "date", {}, 0.95),
        FieldMapping("total", "Amount", "amount", {}, 0.95),
        FieldMapping("currency", "Curr", "copy", {}, 0.95),
        FieldMapping("payment_status", "Status", "copy", {"p": "paid", "o": "unpaid"}, 0.9),
    ]
    row = {"Vendor Name": "ACME", "Inv Date": "04/03/24", "Amount": "$1,234.50", "Curr": "",
           "Status": "P"}  # fmt: skip
    norm = {
        "Inv Date": {"type": "date", "value": "2024-04-03",
                     "flags": ["two_digit_year", "ambiguous_day_month"]},
        "Amount": {"type": "amount", "value": "1234.50", "currency": "USD"},
    }  # fmt: skip
    rec = apply_mapping(Invoice, fms, row, norm, ["filled_down:Vendor Name"])
    v = rec.values()
    assert v["vendor_name"] == "ACME" and v["payment_status"] == "paid"
    assert v["invoice_date"] == "2024-04-03" and v["total"] == "1234.50"
    assert v["currency"] == "USD"
    assert rec.fields["currency"].flags == ["inferred_from_amount_currency"]
    assert rec.fields["invoice_date"].final_confidence < 0.95 * 0.7  # ambiguous + 2-digit year
    assert "filled_down:Vendor Name" in rec.fields["vendor_name"].flags
    assert rec.fields["vendor_name"].evidence == "Vendor Name: ACME"
    assert rec.fields["invoice_number"].value is None


def test_row_columns_flattens_json() -> None:
    raw = {"object": {"contact": {"emailAddress": "a@x.example"}, "ts": 1}}
    assert row_columns("json_object", raw) == {"contact.emailAddress": "a@x.example", "ts": 1}
    assert row_columns("row", {"A": 1, "_extra": ["x"]}) == {"A": 1}


def test_document_content_numbers_lines_and_embeds_pdfs() -> None:
    assert numbered("a\nb") == "L1: a\nL2: b"
    (text,) = document_content("x.txt", "document", {"line_start": 1}, "hello")
    assert "L1: hello" in text["text"]
    blocks = document_content("s.pdf", "document", {"pages": 1}, "", pdf_bytes=b"%PDF-1.4")
    assert blocks[0]["type"] == "document"
    assert blocks[0]["source"]["media_type"] == "application/pdf"


def test_cache_key_changes_with_inputs() -> None:
    base = LLMRequest("m", "sys", [{"type": "text", "text": "x"}], {"type": "object"})
    same = LLMRequest("m", "sys", [{"type": "text", "text": "x"}], {"type": "object"},
                      meta={"anything": 1})  # fmt: skip
    assert base.cache_key("p1") == same.cache_key("p1")  # meta is not part of the key
    assert base.cache_key("p1") != base.cache_key("p2")
    other = LLMRequest("m2", "sys", [{"type": "text", "text": "x"}], {"type": "object"})
    assert base.cache_key("p1") != other.cache_key("p1")


def test_cost_usd() -> None:
    r = LLMResponse("ok", {}, "claude-haiku-5-5", input_tokens=1_000_000, output_tokens=200_000,
                    cache_read_tokens=500_000)  # fmt: skip
    assert float(cost_usd(r, SETTINGS.prices)) == pytest.approx(0.10 + 0.10 + 0.005)
    assert cost_usd(LLMResponse("ok", {}, "unknown-model"), SETTINGS.prices) == 0


# --- the real client, against a stubbed SDK ----------------------------------------------


class _StubMessages:
    def __init__(self, result: Any) -> None:
        self.result = result
        self.kwargs: dict[str, Any] = {}

    def create(self, **kwargs: Any) -> Any:
        self.kwargs = kwargs
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def _msg(text: str | None, stop: str = "end_turn", model: str = "claude-haiku-5-5") -> Any:
    content = [SimpleNamespace(type="thinking", thinking="")]
    if text is not None:
        content.append(SimpleNamespace(type="text", text=text))
    usage = SimpleNamespace(input_tokens=100, output_tokens=50, cache_read_input_tokens=10,
                            cache_creation_input_tokens=0)  # fmt: skip
    return SimpleNamespace(model=model, stop_reason=stop, content=content, usage=usage,
                           stop_details=SimpleNamespace(category="cyber"))  # fmt: skip


def _client(result: Any) -> tuple[AnthropicLLMClient, _StubMessages, _StubMessages]:
    c = AnthropicLLMClient(SETTINGS)
    plain, beta = _StubMessages(result), _StubMessages(result)
    c._client = SimpleNamespace(messages=plain, beta=SimpleNamespace(messages=beta))  # type: ignore[assignment]
    return c, plain, beta


def _req(model: str = "claude-haiku-5-5") -> LLMRequest:
    return LLMRequest(model, "sys", [{"type": "text", "text": "x"}], {"type": "object"})


def test_client_sends_structured_output_and_cache_control() -> None:
    c, plain, beta = _client(_msg('{"records": []}'))
    resp = c.complete(_req())
    assert resp.status == "ok" and resp.data == {"records": []}
    assert (resp.input_tokens, resp.output_tokens, resp.cache_read_tokens) == (100, 50, 10)
    kw = plain.kwargs
    assert kw["output_config"]["format"]["type"] == "json_schema"
    assert kw["output_config"]["effort"] == "medium"
    assert kw["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "tool_choice" not in kw and "thinking" not in kw
    assert beta.kwargs == {}


def test_escalation_model_uses_server_side_fallbacks() -> None:
    c, plain, beta = _client(_msg("{}", model="claude-opus-5-5"))
    c.complete(_req("claude-opus-5-5"))
    assert beta.kwargs["fallbacks"] == "default"
    assert beta.kwargs["betas"] == ["server-side-fallback-2026-07-01"]
    assert plain.kwargs == {}


@pytest.mark.parametrize(
    ("msg", "status"),
    [
        (_msg(None, stop="refusal"), "refusal"),
        (_msg('{"rec', stop="max_tokens"), "max_tokens"),
        (_msg("not json"), "invalid"),
        (_msg(None), "invalid"),
    ],
)
def test_client_statuses(msg: Any, status: str) -> None:
    c, _, _ = _client(msg)
    assert c.complete(_req()).status == status


def test_client_api_errors_become_error_status() -> None:
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    err = anthropic.BadRequestError(
        "bad", response=httpx2.Response(400, request=request), body=None
    )
    c, _, _ = _client(err)
    resp = c.complete(_req())
    assert resp.status == "error" and resp.error and resp.error.startswith("400")


def test_file_level_constant_mapping() -> None:
    data = {"mappings": [
        {"field": "payment_status", "column": None, "constant": "Open", "transform": "copy",
         "value_map": [{"source": "open", "target": "unpaid"}], "confidence": 0.8, "reason": ""},
        {"field": "vendor_name", "column": "Supplier", "constant": "ignored", "transform": "copy",
         "value_map": [], "confidence": 0.9, "reason": ""},
    ]}  # fmt: skip
    fms = parse_mapping(Invoice, data, ["Supplier"])
    rec = apply_mapping(Invoice, fms, {"Supplier": "Acme"}, {}, [])
    assert rec.values()["payment_status"] == "unpaid"
    assert rec.fields["payment_status"].flags == ["file_level_constant"]
    assert rec.values()["vendor_name"] == "Acme"  # a column wins over a constant


def test_workspace_header_only_when_configured() -> None:
    plain = AnthropicLLMClient(SETTINGS)
    assert "anthropic-workspace-id" not in plain._client.default_headers
    scoped = AnthropicLLMClient(
        Settings(_env_file=None, anthropic_api_key="sk-test", anthropic_workspace_id="wrkspc_x")
    )
    assert scoped._client.default_headers["anthropic-workspace-id"] == "wrkspc_x"
