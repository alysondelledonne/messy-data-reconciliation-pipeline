"""Pure extraction logic: interpret model output, check evidence, decide escalation,
apply column mappings. No database or network access here."""

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal, get_args, get_origin

from pydantic import BaseModel

from pipeline.config import Settings
from pipeline.extract.schema import _strip_optional, coerce_field, required_fields
from pipeline.extract.transforms import apply_transform
from pipeline.ingest.adapters.jsonish import flatten

# Raw-record kinds that hold no record to extract.
SKIP_KINDS = frozenset({"total_row", "stray_note", "sheet_text", "heading", "malformed_line"})
# Row-shaped kinds handled by a column mapping (D-023); everything else is a document.
STRUCTURED_KINDS = frozenset({"row", "table_row", "json_object"})


@dataclass
class FieldResult:
    value: Any
    confidence: float
    final_confidence: float
    evidence: str
    grounded: bool | None  # None: can't be checked (scanned PDF)
    flags: list[str] = field(default_factory=list)

    def as_meta(self) -> dict[str, Any]:
        return {
            "confidence": round(self.confidence, 3),
            "final_confidence": round(self.final_confidence, 3),
            "evidence": self.evidence,
            "grounded": self.grounded,
            "flags": self.flags,
        }


@dataclass
class Extracted:
    fields: dict[str, FieldResult]
    flags: list[str] = field(default_factory=list)

    def values(self) -> dict[str, Any]:
        return {k: f.value for k, f in self.fields.items()}

    def meta(self) -> dict[str, Any]:
        return {k: f.as_meta() for k, f in self.fields.items()}

    def has_values(self) -> bool:
        return any(f.value is not None for f in self.fields.values())


# --- evidence checking ------------------------------------------------------------------

_WS = re.compile(r"\s+")


def _norm(s: str) -> str:
    return _WS.sub(" ", s).strip().lower()


def is_grounded(evidence: str, value: Any, source_text: str) -> bool:
    """Is the evidence (or, without evidence, the value itself) present in the source?"""
    src = _norm(source_text)
    if evidence and _norm(evidence) in src:
        return True
    if value is None or isinstance(value, list | dict):
        return bool(evidence) and _norm(evidence) in src
    v = _norm(str(value))
    if len(v) >= 3 and v in src:
        return True
    digits = re.sub(r"\D", "", str(value))
    return len(digits) >= 6 and digits in re.sub(r"\D", "", source_text)


# --- documents --------------------------------------------------------------------------


def interpret_document(
    model: type[BaseModel], data: dict[str, Any], source_text: str | None
) -> list[Extracted]:
    """Model JSON -> validated, evidence-checked records.

    `source_text` None means the source can't be checked (a scanned PDF).
    """
    out: list[Extracted] = []
    for rec in data.get("records") or []:
        fields: dict[str, FieldResult] = {}
        for name in model.model_fields:
            cell = rec.get(name) or {}
            raw_value = cell.get("value")
            conf = float(cell.get("confidence") or 0.0)
            conf = min(max(conf, 0.0), 1.0)
            evidence = str(cell.get("evidence") or "")
            value, error = coerce_field(model, name, raw_value)
            flags = [f"invalid_value: {error}"] if error else []
            if value is None:
                fields[name] = FieldResult(None, 0.0 if error else conf, 0.0, evidence, None, flags)
                continue
            grounded = None if source_text is None else is_grounded(evidence, value, source_text)
            final = conf if grounded in (True, None) else conf * 0.5
            if grounded is False:
                flags.append("grounding_failed")
            fields[name] = FieldResult(value, conf, final, evidence, grounded, flags)
        missing = [
            f"missing_required:{n}" for n in required_fields(model) if fields[n].value is None
        ]
        out.append(Extracted(fields, missing))
    return out


def escalation_reasons(
    model: type[BaseModel], records: list[Extracted], settings: Settings
) -> list[str]:
    """Why a small-model result isn't good enough (empty list = keep it).

    Triggers (D-025): a present required value below the confidence threshold, or
    too many values whose evidence isn't in the source.
    """
    reasons: list[str] = []
    required = required_fields(model)
    for i, rec in enumerate(records):
        checked = 0
        ungrounded = 0
        for name in required:
            fr = rec.fields[name]
            # A missing value is the model saying "not stated": that's for validation to judge
            # (flagged on the record), not a reason to pay for the bigger model.
            if fr.value is not None and fr.final_confidence < settings.confidence_threshold:
                reasons.append(f"record {i}: low confidence {name} ({fr.final_confidence:.2f})")
        for fr in rec.fields.values():
            if fr.value is not None and fr.grounded is not None:
                checked += 1
                ungrounded += fr.grounded is False
        if checked and ungrounded / checked > settings.grounding_fail_ratio:
            reasons.append(f"record {i}: {ungrounded}/{checked} values not found in source")
    return reasons


# --- structured sources: column mappings ------------------------------------------------


def row_columns(kind: str, raw: dict[str, Any]) -> dict[str, Any]:
    """Column -> cell for a structured raw record (JSON objects are flattened)."""
    if kind == "json_object":
        obj = raw.get("object")
        return flatten(obj) if isinstance(obj, dict | list) else {"value": obj}
    return {k: v for k, v in raw.items() if k != "_extra"}


def signature(pack: str, columns: list[str]) -> str:
    return hashlib.sha256(json.dumps([pack, columns]).encode()).hexdigest()[:32]


@dataclass
class FieldMapping:
    field: str
    column: str | None
    transform: str
    value_map: dict[str, str]
    confidence: float
    constant: str | None = None


def parse_mapping(
    model: type[BaseModel], data: dict[str, Any], columns: list[str]
) -> list[FieldMapping]:
    best: dict[str, FieldMapping] = {}
    for m in data.get("mappings") or []:
        name = m.get("field")
        col = m.get("column")
        if name not in model.model_fields or (col is not None and col not in columns):
            continue
        fm = FieldMapping(
            field=name,
            column=col,
            transform=m.get("transform") or "copy",
            value_map={
                str(p["source"]).strip().lower(): p["target"] for p in m.get("value_map") or []
            },
            confidence=min(max(float(m.get("confidence") or 0.0), 0.0), 1.0),
            constant=m.get("constant") if col is None else None,
        )
        if name not in best or fm.confidence > best[name].confidence:
            best[name] = fm
    return list(best.values())


_PENALTIES = (
    ("ambiguous", 0.7),
    ("two_digit_year", 0.95),
    ("assumed", 0.9),
    ("phone_not_e164", 0.6),
    ("unknown_country", 0.5),
    ("excel_serial_date", 0.95),
    ("filled_down", 0.9),
)


def _penalty(flags: list[str]) -> float:
    p = 1.0
    for flag in flags:
        for key, factor in _PENALTIES:
            if key in flag:
                p *= factor
    return p


def _literal_values(model: type[BaseModel], name: str) -> list[str] | None:
    tp, _ = _strip_optional(model.model_fields[name].annotation)
    return [str(a) for a in get_args(tp)] if get_origin(tp) is Literal else None


def apply_mapping(
    model: type[BaseModel],
    mappings: list[FieldMapping],
    columns: dict[str, Any],
    normalized: dict[str, Any],
    record_flags: list[str],
) -> Extracted:
    """Deterministically read one row through a mapping."""
    fields: dict[str, FieldResult] = {}
    by_field = {m.field: m for m in mappings}
    for name in model.model_fields:
        m = by_field.get(name)
        if m is not None and m.column is None and m.constant:
            value, error = coerce_field(
                model, name, m.value_map.get(m.constant.lower(), m.constant)
            )
            flags = [f"invalid_value: {error}"] if error else ["file_level_constant"]
            conf = m.confidence if value is not None else 0.0
            fields[name] = FieldResult(value, conf, conf, "file context", True, flags)
            continue
        if m is None or m.column is None:
            fields[name] = FieldResult(None, 0.0, 0.0, "", None)
            continue
        raw = columns.get(m.column)
        value, flags = apply_transform(m.transform, raw, normalized.get(m.column))
        if value is not None and m.value_map:
            value = m.value_map.get(str(value).strip().lower(), value)
        value, error = coerce_field(model, name, value)
        if error:
            flags.append(f"invalid_value: {error}")
        flags += [f for f in record_flags if f == f"filled_down:{m.column}"]
        conf = m.confidence if value is not None else 0.0
        evidence = f"{m.column}: {raw}" if raw not in (None, "") else ""
        fields[name] = FieldResult(value, conf, conf * _penalty(flags), evidence, True, flags)

    # A blank enum field (e.g. currency) can be filled from a currency-marked amount in the row.
    for name, fr in fields.items():
        allowed = _literal_values(model, name)
        if fr.value is None and allowed:
            for col, norm in normalized.items():
                cur = norm.get("currency") if isinstance(norm, dict) else None
                if cur in allowed:
                    fields[name] = FieldResult(
                        cur,
                        0.8,
                        0.8,
                        f"{col}: {columns.get(col)}",
                        True,
                        ["inferred_from_amount_currency"],
                    )
                    break
    return Extracted(fields)
