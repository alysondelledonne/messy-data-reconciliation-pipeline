"""Prompt text. Bump PROMPT_VERSION whenever any prompt or schema changes meaning:
it is part of every cache key and of the extractor version stored with each result."""

import base64
import json
from typing import Any, Literal, get_args, get_origin

from pydantic import BaseModel

from pipeline.domains import PackInfo
from pipeline.extract.schema import _strip_optional

PROMPT_VERSION = "p1"

DOCUMENT_SYSTEM = """\
You extract structured records from messy business documents for a data reconciliation pipeline.

Rules:
- Extract every distinct {entity} the source describes. If it describes none, return an empty \
"records" list. Do not invent records.
- Report values exactly as the source states them. Do not fix typos, fill gaps from general \
knowledge, or reconcile against other documents; a later stage does that. Only reformat values \
where a field description asks for a format (ISO dates, plain decimals, E.164 phones, \
ISO country codes, listed enum values).
- If a field is not stated, set value to null, confidence to 0 and evidence to "".
- evidence: the shortest exact snippet copied from the source that supports the value \
(without any "L12:" line prefix). It will be checked against the source text.
- confidence: your honest probability (0-1) that the value is what the source states. Lower it \
for ambiguous, partially legible, or inferred values.
{guidance}"""

MAPPING_SYSTEM = """\
You map the columns of a structured data export onto a target schema for a data reconciliation \
pipeline. You see the column names, sample rows, and the distinct values of low-cardinality \
columns.

Return one mapping entry per target field:
- column: the source column holding that field, or null if no column does.
- transform: how to read the cell. copy (verbatim text), date (a date in any format), amount \
(money), split_name_first / split_name_last (take the first or last name from a full-name \
column such as "Dana Whitfield" or "Whitfield, Dana"), phone_e164, country_iso2.
- value_map: for fields with allowed values, map every distinct source value you see to the \
allowed value it means (e.g. "P" -> "paid"). Empty for other fields.
- constant: only when no column holds the field but the file as a whole states it (for \
example a report titled "Open Items" means every row is unpaid): set column to null and \
constant to that value. Otherwise null.
- confidence: 0-1 that this column (or constant) really holds this field.
The data is about: {entity}."""


def _field_lines(model: type[BaseModel]) -> str:
    lines = []
    for name, f in model.model_fields.items():
        tp, _ = _strip_optional(f.annotation)
        if get_origin(tp) is Literal:
            kind = "one of " + ", ".join(map(str, get_args(tp)))
        else:
            kind = getattr(tp, "__name__", str(tp))
        req = "required" if f.is_required() else "optional"
        desc = f" - {f.description}" if f.description else ""
        lines.append(f"- {name} ({kind}, {req}){desc}")
    return "\n".join(lines)


def document_system(pack: PackInfo) -> str:
    guidance = f"\nDomain guidance: {pack.guidance}" if pack.guidance else ""
    return DOCUMENT_SYSTEM.format(entity=pack.entity, guidance=guidance)


def mapping_system(pack: PackInfo) -> str:
    return MAPPING_SYSTEM.format(entity=pack.entity)


def numbered(text: str) -> str:
    return "\n".join(f"L{i}: {line}" for i, line in enumerate(text.splitlines(), 1))


def document_content(
    source: str, kind: str, location: dict[str, Any], text: str, pdf_bytes: bytes | None = None
) -> list[dict[str, Any]]:
    header = f"Source file: {source}\nPart: {kind} {json.dumps(location, sort_keys=True)}"
    if pdf_bytes is not None:
        return [
            {
                "type": "document",
                "source": {
                    "type": "base64",
                    "media_type": "application/pdf",
                    "data": base64.standard_b64encode(pdf_bytes).decode("ascii"),
                },
            },
            {
                "type": "text",
                "text": f"{header}\nThis is a scanned, image-only PDF. Read it visually.",
            },
        ]
    return [{"type": "text", "text": f"{header}\n\n{numbered(text)}"}]


def mapping_content(
    model: type[BaseModel],
    columns: list[str],
    samples: list[dict[str, Any]],
    distinct: dict[str, list[str]],
    context: list[str] | None = None,
) -> list[dict[str, Any]]:
    parts = []
    if context:
        parts += ["File context (titles, sheet names):", *context, ""]
    parts += [
        "Target fields:",
        _field_lines(model),
        "",
        "Source columns: " + json.dumps(columns, ensure_ascii=False),
        "",
        "Sample rows:",
        *(json.dumps(s, ensure_ascii=False, default=str) for s in samples),
    ]
    if distinct:
        parts += ["", "Distinct values of low-cardinality columns:"]
        parts += [f"- {c}: {json.dumps(v, ensure_ascii=False)}" for c, v in distinct.items()]
    return [{"type": "text", "text": "\n".join(parts)}]
