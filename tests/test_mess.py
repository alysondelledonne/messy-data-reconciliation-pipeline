import random
from datetime import date
from decimal import Decimal

import pytest

from data_gen.mess import (
    ORG_VARIANT_KINDS,
    PHONE_STYLES,
    excel_serial,
    format_amount,
    format_date,
    format_phone,
    org_variant,
    strip_suffix,
    transpose_digits,
    typo,
)

D = date(2024, 4, 3)


@pytest.mark.parametrize(
    ("style", "expected"),
    [
        ("iso", "2024-04-03"),
        ("us_short", "04/03/24"),
        ("us_long", "04/03/2024"),
        ("month_day", "April 3"),
        ("mon_d_yyyy", "Apr 3, 2024"),
        ("d_mon_yyyy", "03 Apr 2024"),
        ("dmy_dots", "03.04.2024"),
        ("iso_time", "2024-04-03T00:00:00"),
        ("slash_ymd", "2024/04/03"),
    ],
)
def test_format_date(style: str, expected: str) -> None:
    assert format_date(D, style) == expected


def test_excel_serial_matches_excel() -> None:
    assert excel_serial(date(2024, 1, 1)) == 45292


@pytest.mark.parametrize(
    ("style", "currency", "expected"),
    [
        ("symbol", "USD", "$1,234.50"),
        ("grouped", "USD", "1,234.50"),
        ("plain", "USD", "1234.5"),
        ("code", "CAD", "CAD 1,234.50"),
        ("eu", "EUR", "1.234,50 €"),
        ("eu_code", "EUR", "1.234,50 EUR"),
    ],
)
def test_format_amount(style: str, currency: str, expected: str) -> None:
    assert format_amount(Decimal("1234.5"), style, currency) == expected


def test_strip_suffix() -> None:
    assert strip_suffix("Ironwood Plumbing & Heating Inc.") == "Ironwood Plumbing & Heating"
    assert strip_suffix("Graystone Paving Co.") == "Graystone Paving"
    assert strip_suffix("Copperfield Architects") == "Copperfield Architects"


@pytest.mark.parametrize("kind", ORG_VARIANT_KINDS)
def test_org_variants_are_deterministic(kind: str) -> None:
    name = "Lakeshore HVAC Services Inc."
    a = org_variant(name, kind, random.Random(1))
    b = org_variant(name, kind, random.Random(1))
    assert a == b
    assert a  # never empty


def test_org_variant_examples() -> None:
    rng = random.Random(0)
    assert org_variant("Kestrel Lock & Key LLC", "ampersand", rng) == "Kestrel Lock and Key LLC"
    assert org_variant("Lakeshore HVAC Services Inc.", "abbreviate", rng) == (
        "Lakeshore HVAC Svcs Inc."
    )
    assert org_variant("Acme Co", "upper", rng) == "ACME CO"


def test_typo_changes_one_edit_and_keeps_first_letter() -> None:
    rng = random.Random(3)
    for _ in range(50):
        out = typo("Northgate", rng)
        assert out[0] == "N"
        assert abs(len(out) - len("Northgate")) <= 1


@pytest.mark.parametrize("style", PHONE_STYLES)
def test_phone_styles_keep_digits(style: str) -> None:
    text = format_phone("+13125550143", style)
    digits = "".join(c for c in text.split(" x")[0] if c.isdigit())
    assert digits.endswith("3125550143")


def test_uk_phone_formats() -> None:
    assert format_phone("+442079460123", "dash") == "020 7946 0123"
    assert format_phone("+441614960123", "intl_space") == "+44 161 4960 123"


def test_transpose_digits_swaps_adjacent_pair() -> None:
    out = transpose_digits("312-555-0143", random.Random(5))
    assert out != "312-555-0143"
    assert sorted(out) == sorted("312-555-0143")
    assert out.startswith("312")
