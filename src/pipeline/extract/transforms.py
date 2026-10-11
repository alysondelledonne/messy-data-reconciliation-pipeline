"""Deterministic value transforms used when applying a column mapping (D-023)."""

import re
from typing import Any

from pipeline.ingest.normalize import parse_amount, parse_date

COUNTRIES = {
    "us": "US", "usa": "US", "u.s.": "US", "u.s.a.": "US", "united states": "US",
    "united states of america": "US", "america": "US",
    "ca": "CA", "can": "CA", "canada": "CA",
    "gb": "GB", "uk": "GB", "u.k.": "GB", "united kingdom": "GB", "great britain": "GB",
    "england": "GB", "scotland": "GB", "wales": "GB",
    "de": "DE", "germany": "DE", "deutschland": "DE",
    "fr": "FR", "france": "FR", "pt": "PT", "portugal": "PT",
    "ie": "IE", "ireland": "IE", "mx": "MX", "mexico": "MX",
}  # fmt: skip


def split_name(full: str) -> tuple[str | None, str | None]:
    """'Dana Whitfield', 'Whitfield, Dana' or 'WHITFIELD, DANA' -> (first, last)."""
    s = " ".join(full.split())
    if not s:
        return None, None
    if "," in s:
        last, first = (p.strip() for p in s.split(",", 1))
        return first or None, last or None
    parts = s.split(" ")
    if len(parts) == 1:
        return parts[0], None
    return parts[0], " ".join(parts[1:])


def to_e164(text: str) -> tuple[str | None, list[str]]:
    """Best-effort E.164. National numbers without a country code can't be converted."""
    s = text.split(" x")[0].split(" ext")[0].strip()
    digits = re.sub(r"\D", "", s)
    if not digits:
        return None, []
    if s.startswith("+"):
        return "+" + digits, []
    if len(digits) == 10 and digits[0] in "23456789":  # North American national number
        return "+1" + digits, ["assumed_country_code_1"]
    if len(digits) == 11 and digits[0] == "1":
        return "+" + digits, []
    return s, ["phone_not_e164"]


def apply_transform(
    transform: str, raw: Any, normalized: dict[str, Any] | None
) -> tuple[Any, list[str]]:
    """Turn one cell into a field value. Returns (value, flags)."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None, []
    text = str(raw).strip()
    norm = normalized or {}
    match transform:
        case "date":
            if norm.get("type") == "date":
                if norm.get("value"):
                    return norm["value"], list(norm.get("flags", []))
                return None, list(norm.get("flags", ["unparsed_date"]))
            pd = parse_date(text)
            if pd and pd.value:
                return pd.value.isoformat(), list(pd.flags)
            return None, [*(pd.flags if pd else ()), "unparsed_date"]
        case "amount":
            if norm.get("type") == "amount":
                return norm["value"], list(norm.get("flags", []))
            pa = parse_amount(text)
            if pa:
                return str(pa.value), list(pa.flags)
            return None, ["unparsed_amount"]
        case "split_name_first":
            return split_name(text)[0], []
        case "split_name_last":
            return split_name(text)[1], []
        case "phone_e164":
            return to_e164(text)
        case "country_iso2":
            code = COUNTRIES.get(text.lower())
            return (code, []) if code else (text, ["unknown_country"])
    return text, []
