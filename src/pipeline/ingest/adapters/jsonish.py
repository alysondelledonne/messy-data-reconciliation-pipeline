"""JSON, JSONL and log lines. Nested and flat shapes are both flattened to dotted
keys; malformed lines and unparsed log lines are kept and flagged, never dropped."""

import json
import re
from typing import Any

from pipeline.ingest.adapters.tabular import render
from pipeline.ingest.models import IngestedFile, RawRecord
from pipeline.ingest.normalize import decode_text, normalize_row


def flatten(obj: Any, prefix: str = "") -> dict[str, Any]:
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            out.update(flatten(v, f"{prefix}{k}."))
        return out
    if isinstance(obj, list):
        out = {}
        for i, v in enumerate(obj):
            out.update(flatten(v, f"{prefix}{i}."))
        return out
    return {prefix[:-1]: obj}


def _object_record(obj: Any, loc: dict[str, int | str]) -> RawRecord:
    flat = flatten(obj) if isinstance(obj, dict | list) else {"value": obj}
    present = {k: v for k, v in flat.items() if v is not None}
    flags = ["has_nulls"] if len(present) < len(flat) else []
    return RawRecord("json_object", loc, render(present), raw={"object": obj},
                     normalized=normalize_row(present), flags=flags)  # fmt: skip


def ingest_jsonl(data: bytes, f: IngestedFile) -> None:
    text, f.encoding = decode_text(data)
    for n, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as e:
            f.records.append(
                RawRecord("malformed_line", {"line": n}, line, raw={"line": line, "error": str(e)},
                          flags=["malformed_json"])
            )  # fmt: skip
            continue
        f.records.append(_object_record(obj, {"line": n}))


def ingest_json(data: bytes, f: IngestedFile) -> None:
    """A top-level array becomes one record per element; anything else is one record."""
    text, f.encoding = decode_text(data)
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as e:
        f.status, f.error = "error", f"invalid JSON: {e}"
        return
    if isinstance(obj, list):
        f.records = [_object_record(item, {"index": i}) for i, item in enumerate(obj)]
    else:
        f.records = [_object_record(obj, {"index": 0})]


_LOG_PREFIX = re.compile(
    r"^\[?(?P<ts>\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?Z?)\]?\s+"
    r"(?P<level>TRACE|DEBUG|INFO|WARN(?:ING)?|ERROR|CRITICAL|FATAL)\s+"
    r"(?:\[(?P<logger>[\w.-]+)\]\s*|(?P<logger2>[\w.-]+):\s+)?(?P<msg>.*)$"
)
_KV = re.compile(r'(\w+)=("([^"]*)"|\{.*\}|\S+)')


def parse_log_line(line: str) -> tuple[dict[str, Any], list[str]]:
    m = _LOG_PREFIX.match(line)
    if not m:
        return {"message": line}, ["unparsed_log_line"]
    fields: dict[str, Any] = {
        "timestamp": m["ts"],
        "level": m["level"],
        "logger": m["logger"] or m["logger2"],
        "message": m["msg"],
    }
    for km in _KV.finditer(m["msg"]):
        key, value = km[1], km[3] if km[3] is not None else km[2]
        if value.startswith("{"):
            try:
                fields.update(flatten(json.loads(value), f"{key}."))
                continue
            except json.JSONDecodeError:
                pass
        fields[key] = value
    return fields, []


def ingest_log(data: bytes, f: IngestedFile) -> None:
    text, f.encoding = decode_text(data)
    for n, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        fields, flags = parse_log_line(line)
        norm = normalize_row({k: v for k, v in fields.items() if k != "message"})
        f.records.append(RawRecord("log_line", {"line": n}, line, raw=fields, normalized=norm,
                                   flags=flags))  # fmt: skip
