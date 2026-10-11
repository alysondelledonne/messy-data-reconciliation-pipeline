"""Build the structured-output schemas the model must follow, from a pack's Pydantic model.

Every top-level field becomes `{value, confidence, evidence}` (D-024), and the whole
response is `{records: [...]}`, so one source can yield zero or more records (D-016).
The schema uses only JSON Schema features that structured outputs support:
every object has `additionalProperties: false`, and every property is required
but may be null.
"""

import hashlib
import json
import types
import typing
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal, get_args, get_origin

from pydantic import BaseModel, TypeAdapter, ValidationError

JSON = dict[str, Any]


def _strip_optional(tp: Any) -> tuple[Any, bool]:
    origin = get_origin(tp)
    if origin in (typing.Union, types.UnionType):
        args = [a for a in get_args(tp) if a is not type(None)]
        if len(args) < len(get_args(tp)) and len(args) == 1:
            return args[0], True
    return tp, False


def _type_schema(tp: Any) -> JSON:
    """JSON schema for a value type, as the model should write it."""
    tp, _ = _strip_optional(tp)
    origin = get_origin(tp)
    if origin is Literal:
        return {"type": "string", "enum": [str(a) for a in get_args(tp)]}
    if origin is list:
        (item,) = get_args(tp)
        return {"type": "array", "items": _type_schema(item)}
    if isinstance(tp, type) and issubclass(tp, BaseModel):
        return _object_schema(
            {
                name: _nullable(_type_schema(f.annotation), f.description)
                for name, f in tp.model_fields.items()
            }
        )
    if tp is Decimal:
        return {"type": "string", "description": "Plain decimal number, e.g. 1234.50"}
    if tp is date:
        return {"type": "string", "format": "date"}
    if tp is datetime:
        return {"type": "string", "format": "date-time"}
    if tp is int:
        return {"type": "integer"}
    if tp is float:
        return {"type": "number"}
    if tp is bool:
        return {"type": "boolean"}
    return {"type": "string"}


def _nullable(schema: JSON, description: str | None = None) -> JSON:
    out: JSON = {"anyOf": [schema, {"type": "null"}]}
    if description:
        out["description"] = description
    return out


def _object_schema(props: dict[str, JSON]) -> JSON:
    return {
        "type": "object",
        "properties": props,
        "required": list(props),
        "additionalProperties": False,
    }


def record_schema(model: type[BaseModel]) -> JSON:
    """Schema for one extracted record: every field wrapped with confidence and evidence."""
    props: dict[str, JSON] = {}
    for name, f in model.model_fields.items():
        props[name] = _object_schema(
            {
                "value": _nullable(_type_schema(f.annotation), f.description),
                "confidence": {
                    "type": "number",
                    "description": "0-1: how sure you are that value is what the source states",
                },
                "evidence": {
                    "type": "string",
                    "description": "Shortest exact quote from the source supporting the value; "
                    "empty string if value is null",
                },
            }
        )
    return _object_schema(props)


def extraction_schema(model: type[BaseModel]) -> JSON:
    return _object_schema({"records": {"type": "array", "items": record_schema(model)}})


MAPPING_TRANSFORMS = (
    "copy",
    "date",
    "amount",
    "split_name_first",
    "split_name_last",
    "phone_e164",
    "country_iso2",
)


def mapping_schema(model: type[BaseModel], columns: list[str]) -> JSON:
    """Schema for a column-to-field mapping of one structured source (D-023)."""
    entry = _object_schema(
        {
            "field": {"type": "string", "enum": list(model.model_fields)},
            "column": _nullable({"type": "string", "enum": columns}),
            "constant": _nullable(
                {
                    "type": "string",
                    "description": "Value for every row when the file as a whole states the "
                    "field and no column holds it; null otherwise",
                }
            ),
            "transform": {"type": "string", "enum": list(MAPPING_TRANSFORMS)},
            "value_map": {
                "type": "array",
                "description": "For enum fields: source cell values and the allowed value each "
                "means. Empty otherwise.",
                "items": _object_schema(
                    {"source": {"type": "string"}, "target": {"type": "string"}}
                ),
            },
            "confidence": {"type": "number"},
            "reason": {"type": "string"},
        }
    )
    return _object_schema({"mappings": {"type": "array", "items": entry}})


def schema_hash(schema: JSON) -> str:
    return hashlib.sha256(json.dumps(schema, sort_keys=True).encode()).hexdigest()[:16]


def required_fields(model: type[BaseModel]) -> list[str]:
    return [n for n, f in model.model_fields.items() if f.is_required()]


def coerce_field(model: type[BaseModel], field: str, value: Any) -> tuple[Any, str | None]:
    """Validate one field value against the pack schema.

    Returns (JSON-ready value, error). An invalid value comes back as (None, message).
    """
    if value is None:
        return None, None
    annotation = model.model_fields[field].annotation
    assert annotation is not None
    try:
        parsed = TypeAdapter(annotation).validate_python(value)
    except ValidationError as e:
        return None, e.errors()[0]["msg"]
    return TypeAdapter(annotation).dump_python(parsed, mode="json"), None
