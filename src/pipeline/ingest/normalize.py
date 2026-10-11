"""Value-level normalization: text encodings, dates and amounts.

These functions know nothing about any domain pack. They never guess silently: an
ambiguous or partial value comes back with flags explaining why.
"""

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from email.utils import parsedate_to_datetime

# --- encodings -------------------------------------------------------------------


def decode_text(data: bytes) -> tuple[str, str]:
    """Decode bytes to text. Returns (text, encoding).

    Tries UTF-8 with a BOM, then strict UTF-8, then cp1252 (Windows Latin), then
    Latin-1, which never fails.
    """
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8"), "utf-8-sig"
    for enc in ("utf-8", "cp1252"):
        try:
            return data.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1"), "latin-1"


# --- dates -----------------------------------------------------------------------

MONTHS = {
    m: i
    for i, names in enumerate(
        [
            ("jan", "january"),
            ("feb", "february"),
            ("mar", "march"),
            ("apr", "april"),
            ("may",),
            ("jun", "june"),
            ("jul", "july"),
            ("aug", "august"),
            ("sep", "sept", "september"),
            ("oct", "october"),
            ("nov", "november"),
            ("dec", "december"),
        ],
        1,
    )
    for m in names
}
_MON = r"([A-Za-z]{3,9})\.?"

_ISO = re.compile(
    r"^(\d{4})[-/](\d{1,2})[-/](\d{1,2})"  # date
    r"(?:[T ]\d{2}:\d{2}(?::\d{2}(?:[.,]\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?$"  # optional time
)
_DOTS = re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{2}|\d{4})$")
_SLASH = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{2}|\d{4})$")
_MON_D_Y = re.compile(rf"^{_MON}\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s+(\d{{4}}))?$")
_D_MON_Y = re.compile(rf"^(\d{{1,2}})(?:st|nd|rd|th)?[\s-]+{_MON}(?:[\s-]+(\d{{2}}|\d{{4}}))?$")


@dataclass(frozen=True)
class ParsedDate:
    value: date | None
    partial: str | None = None  # ISO 8601 partial, e.g. "--04-03" when the year is missing
    flags: tuple[str, ...] = ()


def _year(y: str) -> int:
    n = int(y)
    return n + 2000 if n < 100 else n  # two-digit years are this century


def _safe(y: int, m: int, d: int, flags: tuple[str, ...] = ()) -> ParsedDate | None:
    try:
        return ParsedDate(date(y, m, d), flags=flags)
    except ValueError:
        return None


def parse_date(text: str) -> ParsedDate | None:
    """Parse a human-written date. Returns None if the text is not a date.

    Conventions:
    - Slashes are read month-first (US); if both parts are 12 or less, the result
      is flagged `ambiguous_day_month`.
    - Dots are read day-first (European).
    - Dates with no year come back as a partial (`--MM-DD`) flagged `missing_year`.
    - Two-digit years are flagged `two_digit_year`.
    """
    s = text.strip()
    if not s or len(s) > 40:
        return None
    flags: tuple[str, ...] = ()
    if m := _ISO.match(s):
        return _safe(int(m[1]), int(m[2]), int(m[3]))
    if m := _DOTS.match(s):
        flags = ("two_digit_year",) if len(m[3]) == 2 else ()
        return _safe(_year(m[3]), int(m[2]), int(m[1]), flags)
    if m := _SLASH.match(s):
        a, b = int(m[1]), int(m[2])
        if len(m[3]) == 2:
            flags += ("two_digit_year",)
        if a > 12 and b <= 12:  # can only be day-first
            return _safe(_year(m[3]), b, a, (*flags, "day_first"))
        if a <= 12 and b <= 12 and a != b:
            flags += ("ambiguous_day_month",)
        return _safe(_year(m[3]), a, b, flags)
    for rx, mon_i, day_i, year_i in ((_MON_D_Y, 1, 2, 3), (_D_MON_Y, 2, 1, 3)):
        if (m := rx.match(s)) and m[mon_i].lower() in MONTHS:
            month, day = MONTHS[m[mon_i].lower()], int(m[day_i])
            if m[year_i] is None:
                if not (1 <= day <= 31):
                    return None
                return ParsedDate(None, partial=f"--{month:02d}-{day:02d}", flags=("missing_year",))
            flags = ("two_digit_year",) if len(m[year_i]) == 2 else ()
            return _safe(_year(m[year_i]), month, day, flags)
    if "," in s and ":" in s:  # RFC 2822, as in email Date headers
        try:
            return ParsedDate(parsedate_to_datetime(s).date())
        except (TypeError, ValueError):
            return None
    return None


EXCEL_EPOCH = date(1899, 12, 30)


def excel_serial_to_date(n: float) -> date | None:
    """Excel 1900-system serial → date, for plausible values (1954..2119)."""
    if not 20000 <= n <= 80000:
        return None
    return date.fromordinal(EXCEL_EPOCH.toordinal() + int(n))


def to_iso(value: date | datetime) -> str:
    return (value.date() if isinstance(value, datetime) else value).isoformat()


# --- amounts -----------------------------------------------------------------------

_CURRENCY_MARKS = [
    ("CA$", "CAD"),
    ("C$", "CAD"),
    ("US$", "USD"),
    ("$", "USD"),
    ("€", "EUR"),
    ("£", "GBP"),
]
_CODES = ("USD", "EUR", "CAD", "GBP")
_US_GROUPED = re.compile(r"^\d{1,3}(,\d{3})+(\.\d+)?$")
_EU_GROUPED = re.compile(r"^\d{1,3}(\.\d{3})+(,\d+)?$")
_DECIMAL_COMMA = re.compile(r"^\d+,\d{1,2}$")
_PLAIN = re.compile(r"^\d+(\.\d+)?$")


@dataclass(frozen=True)
class ParsedAmount:
    value: Decimal
    currency: str | None = None
    flags: tuple[str, ...] = field(default=())


def parse_amount(text: str, *, require_marker: bool = False) -> ParsedAmount | None:
    """Parse a money amount such as `$1,234.50`, `1.234,50 €`, `EUR 1,234.50` or `(12.00)`.

    With `require_marker`, plain numbers are rejected unless they carry a currency
    symbol or code, or use thousands grouping. That stops IDs and quantities being
    read as money.
    """
    s = text.strip()
    if not s or len(s) > 40:
        return None
    currency = None
    flags: list[str] = []
    upper = s.upper()
    for code in _CODES:
        if upper.startswith(code) or upper.endswith(code):
            currency = code
            s = s[3:] if upper.startswith(code) else s[:-3]
            break
    else:
        for mark, code in _CURRENCY_MARKS:
            if mark in s:
                currency = code
                s = s.replace(mark, "")
                if mark == "$":
                    flags.append("dollar_sign_assumed_usd")
                break
    s = s.strip().replace("\u00a0", "")
    negative = (s.startswith("(") and s.endswith(")")) or s.startswith("-")
    s = s.strip("()-").strip()
    grouped = True
    if _US_GROUPED.match(s):
        if "." not in s and s.count(",") == 1:
            flags.append("ambiguous_separator")  # 1,234 could be EU 1.234
        number = s.replace(",", "")
    elif _EU_GROUPED.match(s):
        if "," not in s and s.count(".") == 1:
            flags.append("ambiguous_separator")  # 1.234 could be US 1.234
        number = s.replace(".", "").replace(",", ".")
    else:
        grouped = False
        if _DECIMAL_COMMA.match(s):
            number = s.replace(",", ".")
            flags.append("decimal_comma")
        elif _PLAIN.match(s):
            number = s
            if "." in s and len(s.rsplit(".", 1)[1]) == 3:
                flags.append("ambiguous_separator")  # 1.234 could be EU 1234
        else:
            return None
    if require_marker and currency is None and not grouped:
        return None
    value = Decimal(number)
    if negative:
        value = -value
        flags.append("negative")
    return ParsedAmount(value, currency, tuple(flags))


# --- per-cell normalization for structured sources ------------------------------------

_DATE_HINT = re.compile(r"(date|_dt$|\bdt\b|_at$|\bat$|\btime|\bdue\b|issued|period|\bts\b)", re.I)
_COUNT_HINT = re.compile(r"\b(days?|count|qty|quantity|units?|hours?|hrs)\b", re.I)
_AMOUNT_HINT = re.compile(r"(amount|amt|total|net|gross|tax|price|cost|sum|balance|charge)", re.I)


def _date_column(column: str) -> bool:
    return bool(_DATE_HINT.search(column)) and not _COUNT_HINT.search(column)


def normalize_cell(column: str, value: object) -> dict[str, object] | None:
    """Best-effort typed reading of one structured cell, or None if nothing applies.

    Returns {"type": "date"|"amount", "value": ..., ...} with flags when relevant.
    Date-looking text is always parsed. Amounts are parsed when the text carries a
    currency marker, or when the column name suggests money.
    """
    if isinstance(value, datetime | date):
        return {"type": "date", "value": to_iso(value)}
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        if _date_column(column) and (d := excel_serial_to_date(value)):
            return {"type": "date", "value": d.isoformat(), "flags": ["excel_serial_date"]}
        if _AMOUNT_HINT.search(column):
            return {"type": "amount", "value": str(Decimal(str(value)))}
        return None
    if not isinstance(value, str) or not value.strip():
        return None
    if pd := parse_date(value):
        out: dict[str, object] = {
            "type": "date",
            "value": pd.value.isoformat() if pd.value else None,
        }
        if pd.partial:
            out["partial"] = pd.partial
        if pd.flags:
            out["flags"] = list(pd.flags)
        return out
    if (
        _date_column(column)
        and value.strip().isdigit()
        and (d := excel_serial_to_date(int(value))) is not None
    ):
        return {"type": "date", "value": d.isoformat(), "flags": ["excel_serial_date"]}
    if pa := parse_amount(value, require_marker=not _AMOUNT_HINT.search(column)):
        out = {"type": "amount", "value": str(pa.value)}
        if pa.currency:
            out["currency"] = pa.currency
        if pa.flags:
            out["flags"] = list(pa.flags)
        return out
    return None


def normalize_row(values: dict[str, object]) -> dict[str, dict[str, object]]:
    out = {}
    for col, v in values.items():
        if (n := normalize_cell(col, v)) is not None:
            out[col] = n
    return out
