"""How contacts are reconciled: keys, survivorship policies (D-035)."""

import re
from collections.abc import Mapping
from typing import Any

from rapidfuzz.distance import OSA

from pipeline.reconcile.names import fold
from pipeline.reconcile.types import Candidate, ReconcileSpec

# Common English nicknames -> formal names, for name matching. Domain knowledge, not data.
NICKNAMES = {
    "bob": "robert", "rob": "robert", "bill": "william", "will": "william", "kate": "katherine",
    "katie": "katherine", "jen": "jennifer", "jenny": "jennifer", "mike": "michael",
    "liz": "elizabeth", "beth": "elizabeth", "dan": "daniel", "danny": "daniel",
    "alex": "alexander", "chris": "christopher", "maggie": "margaret", "meg": "margaret",
    "jon": "jonathan", "pat": "patricia", "sam": "samuel", "becky": "rebecca",
    "tom": "thomas", "vicky": "victoria", "ben": "benjamin", "ted": "theodore",
    "josie": "josephine", "nick": "nicholas", "abby": "abigail", "matt": "matthew",
    "steph": "stephanie", "tony": "anthony",
}  # fmt: skip
E164 = re.compile(r"^\+[1-9]\d{7,14}$")


def _email(c: Candidate) -> str | None:
    e = str(c.values.get("email") or "").strip().lower()
    return e or None


def _domain(c: Candidate) -> str | None:
    e = _email(c)
    return e.split("@", 1)[1] if e and "@" in e else None


def _phone(c: Candidate) -> str | None:
    p = str(c.values.get("phone") or "")
    return p if E164.match(p) else None


def first_key(name: object) -> str:
    f = fold(str(name or "")).strip()
    return NICKNAMES.get(f, f)


def evidence(c: Candidate) -> tuple[set[str], set[str]]:
    d = _domain(c)
    return ({f"domain:{d}"}, {f"domain:{d}"}) if d else (set(), set())


def strong_keys(c: Candidate) -> list[str]:
    keys = []
    if e := _email(c):
        keys.append(f"email:{e}")
    if p := _phone(c):
        keys.append(f"phone:{p}")
    return keys


def soft_key(c: Candidate) -> str | None:
    last = fold(str(c.values.get("last_name") or "")).strip()
    if not last or c.entity is None or len(last.rstrip(".")) < 2:
        return None
    return f"{c.entity}:{first_key(c.values.get('first_name'))}:{last}"


def fallback_key(c: Candidate) -> str | None:
    last = fold(str(c.values.get("last_name") or "")).strip()
    if len(last.rstrip(".")) < 2 or not c.values.get("first_name"):
        return None
    return f"{first_key(c.values.get('first_name'))}:{last}"


def email_typo(a: str, b: str) -> bool:
    """One slip of the keyboard apart ('oashworht@' for 'oashworth@', 'keettlecreek' for
    'kettlecreek'). A changed digit is not a typo: 'jsmith2@' is how a namesake at the same
    company gets an address."""
    a, b = a.lower(), b.lower()
    return re.sub(r"\D", "", a) == re.sub(r"\D", "", b) and OSA.distance(a, b) <= 1


def _close(x: str, y: str) -> bool:
    return x == y or (min(len(x), len(y)) >= 4 and OSA.distance(x, y) <= 1)


def names_agree(a: Candidate, b: Candidate) -> bool:
    """Last names equal up to a typo; first names equal up to a typo, a nickname or an
    initial. A missing name agrees with anything."""
    la, lb = (fold(str(c.values.get("last_name") or "")).strip() for c in (a, b))
    if la and lb and not _close(la, lb):
        return False
    fa, fb = (first_key(c.values.get("first_name")) for c in (a, b))
    if not fa or not fb or _close(fa, fb):
        return True
    return (len(fa.rstrip(".")) == 1 and fb.startswith(fa[0])) or (
        len(fb.rstrip(".")) == 1 and fa.startswith(fb[0])
    )


def compatible(a: Candidate, b: Candidate) -> bool:
    """Could be one person: names agree, and email addresses don't differ (namesakes have
    their own address). Phones are no proof either way: old sources carry old numbers, and
    a number can pass to someone else."""
    if not names_agree(a, b):
        return False
    ea, eb = _email(a), _email(b)
    return not (ea and eb and ea != eb and not email_typo(ea, eb))


def _digits(v: object) -> str:
    return re.sub(r"\D", "", str(v or ""))[-9:]  # national number, whatever the prefix


def same_phone(a: object, b: object) -> bool:
    """'+441174960320' = '0117 4960 320'; two digits swapped is a typo of the same number."""
    da, db = _digits(a), _digits(b)
    return len(da) == 9 and OSA.distance(da, db) <= 1 and sorted(da) == sorted(db)


def same_email(a: object, b: object) -> bool:
    ea, eb = str(a).strip().lower(), str(b).strip().lower()
    return ea == eb or email_typo(ea, eb)


def same_first_name(a: object, b: object) -> bool:
    return first_key(a) == first_key(b)


def display_rank(name: str, value: object) -> int:
    s = str(value or "")
    if name == "phone":
        return int(bool(E164.match(s)))
    if name == "email":
        return int(s == s.lower())
    if name == "first_name":  # the formal name over a nickname
        return int(fold(s).strip() not in NICKNAMES)
    return 0


DIAL_CODES = {"US": "1", "CA": "1", "GB": "44", "IE": "353", "AU": "61"}


def finalize(fields: Mapping[str, Any]) -> dict[str, Any]:
    """A national phone number ('0117 4960 320') plus the record's country -> E.164."""
    phone, code = str(fields.get("phone") or ""), DIAL_CODES.get(str(fields.get("country")))
    if not phone or E164.match(phone) or code is None:
        return {}
    digits = re.sub(r"\D", "", phone)
    if code == "1" and len(digits) == 10:
        return {"phone": f"+1{digits}"}
    if code != "1" and digits.startswith("0") and 9 <= len(digits) <= 11:
        return {"phone": f"+{code}{digits[1:]}"}
    return {}


def trust(c: Candidate) -> int:
    if c.format == "csv":
        return 3  # the CRM export
    if c.kind == "email_attachment":
        return 3  # a vCard the person sent
    if c.format in ("eml", "docx"):
        return 2
    return 1


SPEC = ReconcileSpec(
    entity_field="company",
    entity_kind="company",
    evidence=evidence,
    strong_keys=strong_keys,
    soft_key=soft_key,
    soft_match=lambda a, b: compatible(a, b),
    compatible=compatible,
    fallback_key=fallback_key,
    policies={
        "first_name": "majority",
        "last_name": "majority",
        "email": "recent",
        "phone": "recent",
        "company": "recent_entity",
        "job_title": "recent",
        "city": "majority",
        "country": "majority",
    },
    equivalent={"phone": same_phone, "email": same_email, "first_name": same_first_name},
    display_rank=display_rank,
    finalize=finalize,
    trust=trust,
    material=frozenset({"last_name"}),
    undated_is_current=lambda c: c.format == "csv",  # the CRM export is a current snapshot
)
