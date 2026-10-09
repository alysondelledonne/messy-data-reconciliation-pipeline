"""Ground-truth models written alongside the generated corpus.

Each pack produces one `GroundTruth`. It records:
- the true (clean) value of every record;
- every place that record appears in the messy corpus (an *occurrence*), with the
  field values a perfect extractor would read from that exact source, which can
  differ from the truth (typos, aliases, stale values, totals that don't add up);
- the mess planted in each occurrence (`defects`);
- canonical entities and the aliases planted for them;
- duplicate groups: planted duplicates of one record within a single source,
  such as a repeated CSV row, a repeated form submission, or a re-sent or
  re-scanned copy of a PDF.
"""

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, Field

Format = Literal["csv", "xlsx", "pdf", "pdf_scanned", "txt", "md", "eml", "docx", "jsonl", "log"]
Location = dict[str, int | str]


class Occurrence(BaseModel):
    occurrence_id: str = Field(description="Unique id: '<source>#<location>'.")
    source: str = Field(description="Path relative to the corpus root.")
    format: Format
    location: Location = Field(
        description="Where in the file: row, sheet+row, page, line, part, table+row..."
    )
    layout: str | None = None
    expected: dict[str, Any] = Field(
        description="Field values exactly as this source states them (pack schema, JSON form). "
        "Fields the source does not state are omitted."
    )
    defects: list[str] = Field(default_factory=list)
    duplicate_of: str | None = Field(
        default=None, description="occurrence_id of the earlier copy this one duplicates."
    )


class Entity(BaseModel):
    entity_id: str
    kind: str
    canonical_name: str
    aliases: list[str] = Field(
        default_factory=list, description="Every non-canonical spelling planted in the corpus."
    )
    lookalike_of: str | None = Field(
        default=None,
        description="entity_id of a *different* entity with a confusingly similar name.",
    )


class TrueRecord(BaseModel):
    record_id: str
    entity_id: str | None = Field(default=None, description="Canonical entity this belongs to.")
    truth: dict[str, Any] = Field(description="Clean record, valid against the pack schema.")
    occurrences: list[Occurrence] = Field(default_factory=list)


class GroundTruth(BaseModel):
    pack: str
    seed: int
    generator_version: str
    entities: list[Entity]
    records: list[TrueRecord]
    duplicate_groups: list[list[str]] = Field(
        default_factory=list,
        description="Each group lists the occurrence_ids of one planted duplicate set; the "
        "first entry is the original.",
    )


def occurrence_id(source: str, location: Mapping[str, int | str]) -> str:
    loc = ",".join(f"{k}={v}" for k, v in location.items())
    return f"{source}#{loc}"
