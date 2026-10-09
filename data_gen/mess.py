"""Reusable ways to make clean values messy. Every function is pure given its RNG."""

import random
import re
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

# --- dates -------------------------------------------------------------------

DATE_STYLES = (
    "iso",  # 2024-04-03
    "us_short",  # 04/03/24
    "us_long",  # 04/03/2024
    "month_day",  # April 3            (no year: must be inferred from context)
    "mon_d_yyyy",  # Apr 3, 2024
    "d_mon_yyyy",  # 03 Apr 2024
    "dmy_dots",  # 03.04.2024
    "iso_time",  # 2024-04-03T00:00:00
    "slash_ymd",  # 2024/04/03
)


def format_date(d: date, style: str) -> str:
    match style:
        case "iso":
            return d.isoformat()
        case "us_short":
            return d.strftime("%m/%d/%y")
        case "us_long":
            return d.strftime("%m/%d/%Y")
        case "month_day":
            return f"{d.strftime('%B')} {d.day}"
        case "mon_d_yyyy":
            return f"{d.strftime('%b')} {d.day}, {d.year}"
        case "d_mon_yyyy":
            return d.strftime("%d %b %Y")
        case "dmy_dots":
            return d.strftime("%d.%m.%Y")
        case "iso_time":
            return d.isoformat() + "T00:00:00"
        case "slash_ymd":
            return d.strftime("%Y/%m/%d")
    raise ValueError(f"unknown date style {style!r}")


def excel_serial(d: date) -> int:
    """Excel's 1900 date system serial number (as seen when a date cell loses its format)."""
    return (d - date(1899, 12, 30)).days


# --- money -------------------------------------------------------------------

CENT = Decimal("0.01")


def money(x: Decimal | float | int | str) -> Decimal:
    return Decimal(str(x)).quantize(CENT, rounding=ROUND_HALF_UP)


def group_thousands(amount: Decimal, sep: str = ",", dec: str = ".") -> str:
    q = money(amount)
    sign = "-" if q < 0 else ""
    whole, frac = f"{abs(q):.2f}".split(".")
    grouped = f"{int(whole):,}".replace(",", sep)
    return f"{sign}{grouped}{dec}{frac}"


CURRENCY_SYMBOL = {"USD": "$", "EUR": "€", "CAD": "CA$"}


def format_amount(amount: Decimal, style: str, currency: str = "USD") -> str:
    match style:
        case "symbol":  # $1,234.50
            return CURRENCY_SYMBOL[currency] + group_thousands(amount)
        case "grouped":  # 1,234.50
            return group_thousands(amount)
        case "plain":  # 1234.5 (trailing zero dropped, as a float would print)
            return f"{float(money(amount))}"
        case "code":  # USD 1,234.50
            return f"{currency} {group_thousands(amount)}"
        case "eu":  # 1.234,50 €
            return f"{group_thousands(amount, '.', ',')} {CURRENCY_SYMBOL[currency]}"
        case "eu_code":  # 1.234,50 EUR
            return f"{group_thousands(amount, '.', ',')} {currency}"
    raise ValueError(f"unknown amount style {style!r}")


# --- organization names --------------------------------------------------------

ORG_SUFFIXES = (
    "LLC",
    "L.L.C.",
    "Inc.",
    "Inc",
    "Co.",
    "Company",
    "Ltd.",
    "Ltd",
    "GmbH",
    "SARL",
    "Lda.",
    "Corp.",
    "Group",
)
ABBREVIATIONS = {
    "Services": "Svcs",
    "Service": "Svc",
    "Company": "Co",
    "Mechanical": "Mech",
    "Contractors": "Contr",
    "Electrical": "Elec",
    "Systems": "Sys",
    "International": "Intl",
    "Management": "Mgmt",
    "Properties": "Props",
    "Associates": "Assoc",
    "Supply": "Sup",
    "Protection": "Prot",
}


def strip_suffix(name: str) -> str:
    for suffix in sorted(ORG_SUFFIXES, key=len, reverse=True):
        if name.endswith(" " + suffix):
            return name[: -len(suffix) - 1].rstrip(",")
    return name


def typo(word: str, rng: random.Random) -> str:
    """One keyboard-style slip: swap, drop, or double a letter (never the first)."""
    letters = [i for i, c in enumerate(word) if c.isalpha()]
    if len(letters) < 4:
        return word
    i = rng.choice(letters[1:-1])
    match rng.choice(("swap", "drop", "double")):
        case "swap":
            return word[:i] + word[i + 1] + word[i] + word[i + 2 :]
        case "drop":
            return word[:i] + word[i + 1 :]
        case _:
            return word[:i] + word[i] + word[i:]


ORG_VARIANT_KINDS = (
    "upper",
    "lower",
    "no_suffix",
    "ampersand",
    "abbreviate",
    "typo",
    "suffix_punct",
    "spacing",
)


def org_variant(name: str, kind: str, rng: random.Random) -> str:
    """Return one realistic misspelling of an organization name (may equal input)."""
    match kind:
        case "upper":
            return name.upper()
        case "lower":
            return name.lower()
        case "no_suffix":
            return strip_suffix(name)
        case "ampersand":
            if " & " in name:
                return name.replace(" & ", " and ")
            if " and " in name:
                return name.replace(" and ", " & ")
            return strip_suffix(name)
        case "abbreviate":
            for full, short in ABBREVIATIONS.items():
                if re.search(rf"\b{full}\b", name):
                    return re.sub(rf"\b{full}\b", short, name, count=1)
            return strip_suffix(name)
        case "typo":
            words = name.split(" ")
            idx = [i for i, w in enumerate(words) if len(w) >= 5]
            if not idx:
                return name
            i = rng.choice(idx)
            words[i] = typo(words[i], rng)
            return " ".join(words)
        case "suffix_punct":
            if name.endswith("."):
                return name[:-1]
            if name.endswith((" LLC", " Inc", " Co", " Ltd")):
                return name + "."
            return name.replace(" LLC", ", LLC")
        case "spacing":
            return "  ".join(name.split(" ", 1)) + " "
    raise ValueError(f"unknown variant kind {kind!r}")


def random_org_variant(name: str, rng: random.Random) -> str:
    for _ in range(5):
        v = org_variant(name, rng.choice(ORG_VARIANT_KINDS), rng)
        if v != name:
            return v
    return name.upper()


# --- people --------------------------------------------------------------------

NICKNAMES = {
    "Robert": "Bob",
    "William": "Bill",
    "Katherine": "Kate",
    "Jennifer": "Jen",
    "Michael": "Mike",
    "Elizabeth": "Liz",
    "Daniel": "Dan",
    "Alexander": "Alex",
    "Christopher": "Chris",
    "Margaret": "Maggie",
    "Jonathan": "Jon",
    "Patricia": "Pat",
    "Samuel": "Sam",
    "Rebecca": "Becky",
    "Thomas": "Tom",
    "Victoria": "Vicky",
    "Benjamin": "Ben",
    "Theodore": "Ted",
    "Josephine": "Josie",
    "Nicholas": "Nick",
    "Abigail": "Abby",
    "Matthew": "Matt",
    "Stephanie": "Steph",
    "Anthony": "Tony",
}


# --- phones ----------------------------------------------------------------------

PHONE_STYLES = ("paren", "dash", "dot", "intl_space", "digits", "paren_ext")


def format_phone(e164: str, style: str) -> str:
    """Format a +1 (NANP) or +44 number in a human style."""
    if e164.startswith("+1"):
        d = e164[2:]
        a, b, c = d[:3], d[3:6], d[6:]
        match style:
            case "paren":
                return f"({a}) {b}-{c}"
            case "dash":
                return f"{a}-{b}-{c}"
            case "dot":
                return f"{a}.{b}.{c}"
            case "intl_space":
                return f"+1 {a} {b} {c}"
            case "digits":
                return d
            case "paren_ext":
                return f"({a}) {b}-{c} x{int(c) % 90 + 10}"
    if e164.startswith("+44"):
        d = e164[3:]
        area, rest = (d[:2], d[2:]) if d.startswith("20") else (d[:3], d[3:])
        match style:
            case "intl_space" | "paren_ext":
                return f"+44 {area} {rest[:4]} {rest[4:]}"
            case "digits":
                return f"0{d}"
            case _:
                return f"0{area} {rest[:4]} {rest[4:]}"
    raise ValueError(f"unsupported number {e164}")


def transpose_digits(text: str, rng: random.Random) -> str:
    """Swap two adjacent digits (a classic data-entry error)."""
    pos = [i for i in range(len(text) - 1) if text[i].isdigit() and text[i + 1].isdigit()]
    pos = [i for i in pos if text[i] != text[i + 1]]
    if not pos:
        return text
    i = rng.choice(pos[len(pos) // 2 :])  # keep the area code intact
    return text[:i] + text[i + 1] + text[i] + text[i + 2 :]
