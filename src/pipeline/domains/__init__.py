"""Domain packs (D-004).

A pack holds everything specific to one kind of data. The engine looks packs up by
name and never imports a pack module directly.
"""

from pydantic import BaseModel

from pipeline.domains.contacts.schema import Contact
from pipeline.domains.invoices.schema import Invoice

SCHEMAS: dict[str, type[BaseModel]] = {
    "invoices": Invoice,
    "contacts": Contact,
}


def get_schema(pack: str) -> type[BaseModel]:
    try:
        return SCHEMAS[pack]
    except KeyError:
        raise KeyError(f"unknown domain pack {pack!r}; known: {sorted(SCHEMAS)}") from None
