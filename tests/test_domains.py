import pytest

from pipeline.domains import get_schema
from pipeline.domains.contacts.schema import Contact
from pipeline.domains.invoices.schema import Invoice


def test_registry_resolves_packs() -> None:
    assert get_schema("invoices") is Invoice
    assert get_schema("contacts") is Contact


def test_unknown_pack_lists_known_ones() -> None:
    with pytest.raises(KeyError, match="invoices"):
        get_schema("tickets")


def test_schemas_export_json_schema_for_tool_use() -> None:
    for pack in ("invoices", "contacts"):
        schema = get_schema(pack).model_json_schema()
        assert schema["type"] == "object"
        assert schema["required"]
