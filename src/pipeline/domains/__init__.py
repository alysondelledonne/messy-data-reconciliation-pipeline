"""Domain packs (D-004).

A pack holds everything specific to one kind of data. The engine looks packs up by
name and never imports a pack module directly.
"""

from dataclasses import dataclass

from pydantic import BaseModel

from pipeline.domains.contacts.schema import Contact
from pipeline.domains.invoices.schema import Invoice


@dataclass(frozen=True)
class PackInfo:
    name: str
    schema: type[BaseModel]
    entity: str  # what one record is, for prompts ("vendor invoice")
    guidance: str = ""  # extra extraction guidance specific to this domain


PACKS: dict[str, PackInfo] = {
    "invoices": PackInfo(
        "invoices",
        Invoice,
        "vendor invoice",
        "Extract the invoice as printed, even if its own totals do not add up. "
        "The bill-to party (our Accounts Payable) is not the vendor.",
    ),
    "contacts": PackInfo(
        "contacts",
        Contact,
        "contact person at an external organization",
        "Extract each distinct person mentioned with any contact details. Ignore our own "
        "organization's mailboxes (e.g. the recipient address of an email). Quoted email "
        "replies describe the quoted sender, not the current one.",
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
