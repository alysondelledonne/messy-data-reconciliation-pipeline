from datetime import date, datetime
from decimal import Decimal

import pytest

from pipeline.ingest.normalize import (
    decode_text,
    excel_serial_to_date,
    normalize_cell,
    parse_amount,
    parse_date,
)


@pytest.mark.parametrize(
    ("data", "text", "encoding"),
    [
        (b"\xef\xbb\xbfa,b", "a,b", "utf-8-sig"),
        ("Côté".encode(), "Côté", "utf-8"),
        ("Côté".encode("latin-1"), "Côté", "cp1252"),
        (b"\x81\x8d", "\x81\x8d", "latin-1"),  # undefined in cp1252
    ],
)
def test_decode_text(data: bytes, text: str, encoding: str) -> None:
    assert decode_text(data) == (text, encoding)


@pytest.mark.parametrize(
    ("text", "value", "flags"),
    [
        ("2024-04-03", date(2024, 4, 3), ()),
        ("2024-04-03T00:00:00", date(2024, 4, 3), ()),
        ("2024-06-12T07:19:00+00:00", date(2024, 6, 12), ()),
        ("2024/04/03", date(2024, 4, 3), ()),
        ("03.04.2024", date(2024, 4, 3), ()),
        ("04/03/2024", date(2024, 4, 3), ("ambiguous_day_month",)),
        ("04/03/24", date(2024, 4, 3), ("two_digit_year", "ambiguous_day_month")),
        ("13/04/2024", date(2024, 4, 13), ("day_first",)),
        ("12/25/2023", date(2023, 12, 25), ()),
        ("05/05/2024", date(2024, 5, 5), ()),
        ("Apr 3, 2024", date(2024, 4, 3), ()),
        ("April 3 2024", date(2024, 4, 3), ()),
        ("03 Apr 2024", date(2024, 4, 3), ()),
        ("4 April 2025", date(2025, 4, 4), ()),
        ("Tue, 21 Mar 2023 16:28:00 +0000", date(2023, 3, 21), ()),
    ],
)
def test_parse_date(text: str, value: date, flags: tuple[str, ...]) -> None:
    parsed = parse_date(text)
    assert parsed is not None
    assert parsed.value == value
    assert parsed.flags == flags


def test_date_without_year_is_partial_not_guessed() -> None:
    parsed = parse_date("April 3")
    assert parsed is not None
    assert parsed.value is None
    assert parsed.partial == "--04-03"
    assert parsed.flags == ("missing_year",)


@pytest.mark.parametrize(
    "text", ["", "Northgate", "T00149", "2023-0419", "312-555-0143", "2023-02-30", "Q3 2024"]
)
def test_non_dates(text: str) -> None:
    assert parse_date(text) is None


def test_excel_serial() -> None:
    assert excel_serial_to_date(45292) == date(2024, 1, 1)
    assert excel_serial_to_date(42) is None


@pytest.mark.parametrize(
    ("text", "value", "currency", "flags"),
    [
        ("$1,234.50", "1234.50", "USD", ("dollar_sign_assumed_usd",)),
        ("CA$12.00", "12.00", "CAD", ()),
        ("EUR 1,234.50", "1234.50", "EUR", ()),
        ("1.234,50 €", "1234.50", "EUR", ()),
        ("1,234.50", "1234.50", None, ()),
        ("1234.5", "1234.5", None, ()),
        ("12,5", "12.5", None, ("decimal_comma",)),
        ("1,234", "1234", None, ("ambiguous_separator",)),
        ("(12.00)", "-12.00", None, ("negative",)),
        ("14496.73 CAD", "14496.73", "CAD", ()),
    ],
)
def test_parse_amount(text: str, value: str, currency: str | None, flags: tuple[str, ...]) -> None:
    parsed = parse_amount(text)
    assert parsed is not None
    assert parsed.value == Decimal(value)
    assert parsed.currency == currency
    assert parsed.flags == flags


@pytest.mark.parametrize("text", ["312.555.0143", "T00149", "abc", "1.2.3", "$"])
def test_non_amounts(text: str) -> None:
    assert parse_amount(text) is None


def test_require_marker_rejects_bare_numbers() -> None:
    assert parse_amount("1234.5", require_marker=True) is None
    assert parse_amount("3125550143", require_marker=True) is None
    assert parse_amount("1,234.50", require_marker=True) is not None  # grouping is a marker


def test_normalize_cell_uses_column_hints() -> None:
    assert normalize_cell("Amount", "1234.5") == {"type": "amount", "value": "1234.5"}
    assert normalize_cell("Invoice #", "1234") is None
    assert normalize_cell("Phone", "312.555.0143") is None
    assert normalize_cell("Doc Date", 45292) == {
        "type": "date",
        "value": "2024-01-01",
        "flags": ["excel_serial_date"],
    }
    assert normalize_cell("Days Past Due", 45292) is None
    assert normalize_cell("x", datetime(2024, 1, 2, 0, 0)) == {
        "type": "date",
        "value": "2024-01-02",
    }
    assert normalize_cell("Inv Date", "April 3") == {
        "type": "date",
        "value": None,
        "partial": "--04-03",
        "flags": ["missing_year"],
    }
