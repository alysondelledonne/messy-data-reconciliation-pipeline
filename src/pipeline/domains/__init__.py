"""Domain packs (D-004).

A pack holds everything specific to one kind of data. The engine looks packs up by
name and never imports a pack module directly.
"""

from dataclasses import dataclass

from pydantic import BaseModel

from pipeline.domains.contacts import rules as contact_rules
from pipeline.domains.contacts.schema import Contact
from pipeline.domains.invoices import rules as invoice_rules
from pipeline.domains.invoices.schema import Invoice
from pipeline.validate.types import Rule


@dataclass(frozen=True)
class PackInfo:
    name: str
    schema: type[BaseModel]
    entity: str  # what one record is, for prompts ("vendor invoice")
    guidance: str = ""  # extra extraction guidance specific to this domain
    rules: tuple[Rule, ...] = ()  # validation rules (D-030)
    rules_version: str = "r0"  # bump when rules change meaning


PACKS: dict[str, PackInfo] = {
    "invoices": PackInfo(
        "invoices",
        Invoice,
        "vendor invoice",
        "Extract the invoice as printed, even if its own totals do not add up. "
        "The bill-to party (our Accounts Payable) is not the vendor.",
        rules=invoice_rules.RULES,
        rules_version=invoice_rules.RULES_VERSION,
    ),
    "contacts": PackInfo(
        "contacts",
        Contact,
        "contact person at an external organization",
        "Extract each distinct person mentioned with any contact details. Ignore our own "
        "organization's mailboxes (e.g. the recipient address of an email). Quoted email "
        "replies describe the quoted sender, not the current one.",
        rules=contact_rules.RULES,
        rules_version=contact_rules.RULES_VERSION,
    ),
}

SCHEMAS: dict[str, type[BaseModel]] = {name: p.schema for name, p in PACKS.items()}


def get_pack(pack: str) -> PackInfo:
    try:
        return PACKS[pack]
    except KeyError:
        raise KeyError(f"unknown domain pack {pack!r}; known: {sorted(PACKS)}") from None


def get_schema(pack: str) -> type[BaseModel]:
    return get_pack(pack).schema
