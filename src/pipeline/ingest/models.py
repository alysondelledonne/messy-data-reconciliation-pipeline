"""In-memory results of ingestion, before they are stored."""

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

Location = dict[str, int | str]


@dataclass
class RawRecord:
    """One ingestible unit: a row, a line, a document, an email part, a paragraph...

    - `location` says exactly where in the file it came from (D-016).
    - `text` is what later stages read.
    - `raw` keeps the source values verbatim (decoded to Unicode, never cleaned).
    - `normalized` holds best-effort typed readings of individual values.
    """

    kind: str
    location: Location
    text: str
    raw: dict[str, Any] = field(default_factory=dict)
    normalized: dict[str, Any] = field(default_factory=dict)
    flags: list[str] = field(default_factory=list)

    @property
    def content_hash(self) -> str:
        payload = json.dumps(
            {"kind": self.kind, "text": self.text, "raw": self.raw}, sort_keys=True, default=str
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass
class IngestedFile:
    path: str  # relative to the ingest root
    format: str
    sha256: str
    size_bytes: int
    records: list[RawRecord] = field(default_factory=list)
    encoding: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)
    status: str = "ok"  # ok | unsupported | error
    error: str | None = None


def location_covers(record: Mapping[str, int | str], target: Mapping[str, int | str]) -> bool:
    """Does a record's location contain a finer-grained target location?

    Keys shared by both must be equal; a `line_start`/`line_end` span contains a
    `line`. Extra keys on the target (e.g. `mention` within a paragraph) are finer
    detail and don't block a match.
    """
    for key, value in record.items():
        if key == "line_start":
            line = target.get("line")
            if not isinstance(line, int) or not (int(value) <= line <= int(record["line_end"])):
                return False
        elif key == "line_end":
            continue
        elif key not in target or target[key] != value:
            return False
    return True
