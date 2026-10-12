"""Validation rules for the contacts pack. Each rule is a pure function of one record."""

import re

from pipeline.validate.types import Finding, RecordView, Rule

RULES_VERSION = "con-r1"

EMAIL = re.compile(r"^[A-Za-z0-9._%+'-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}$")
E164 = re.compile(r"^\+[1-9]\d{7,14}$")
ISO2 = re.compile(r"^[A-Z]{2}$")
NAME = re.compile(r"^[^\W\d_]+(?:[ '\-.][^\W\d_]+)*\.?$")


def identifiable(rec: RecordView) -> list[Finding]:
    """A record with no name and no way to reach the person can't be matched to anyone."""
    has_name = bool(rec.get("first_name") or rec.get("last_name"))
    if not has_name and not (rec.get("email") or rec.get("phone")):
        return [
            Finding(
                "unidentifiable",
                "reject",
                "No name, email or phone: this can't be matched to a person.",
                ("first_name", "last_name", "email", "phone"),
            )
        ]
    return []


def email_format(rec: RecordView) -> list[Finding]:
    email = rec.get("email")
    if email and not EMAIL.match(str(email).strip()):
        return [
            Finding(
                "email_format", "review", f"Email {email!r} is not a valid address.", ("email",)
            )
        ]
    return []


def phone_format(rec: RecordView) -> list[Finding]:
    phone = rec.get("phone")
    if phone and not E164.match(str(phone)):
        return [
            Finding(
                "phone_format",
                "review",
                f"Phone {phone!r} is not in international format (+country code).",
                ("phone",),
            )
        ]
    return []


def country_format(rec: RecordView) -> list[Finding]:
    country = rec.get("country")
    if country and not ISO2.match(str(country)):
        return [
            Finding(
                "country_format",
                "review",
                f"Country {country!r} is not a two-letter ISO code.",
                ("country",),
            )
        ]
    return []


def name_shape(rec: RecordView) -> list[Finding]:
    out = []
    for name in ("first_name", "last_name"):
        value = rec.get(name)
        if not value:
            continue
        s = str(value).strip()
        if not NAME.match(s):
            out.append(
                Finding(
                    "name_shape",
                    "review",
                    f"{name} {s!r} contains characters a name shouldn't.",
                    (name,),
                )
            )
        elif len(s.rstrip(".")) == 1:
            out.append(
                Finding("name_abbreviated", "review", f"{name} {s!r} looks abbreviated.", (name,))
            )
        elif s.isupper() and len(s) > 3:
            out.append(Finding("name_case", "info", f"{name} {s!r} is all caps.", (name,)))
    return out


RULES: tuple[Rule, ...] = (identifiable, email_format, phone_format, country_format, name_shape)
