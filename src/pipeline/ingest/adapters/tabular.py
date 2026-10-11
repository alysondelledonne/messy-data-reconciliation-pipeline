"""Shared logic for row-shaped sources: header detection, total rows, fill-down."""

import re
from collections import Counter
from collections.abc import Sequence
from typing import Any

from pipeline.ingest.models import Location, RawRecord
from pipeline.ingest.normalize import normalize_row

_TOTAL_ROW = re.compile(r"^\s*(grand\s+)?(sub\s*-?\s*)?totals?\b", re.I)


def _filled(row: Sequence[Any]) -> int:
    return sum(1 for v in row if v not in (None, "") and str(v).strip())


def _is_texty(v: Any) -> bool:
    return isinstance(v, str) and bool(v.strip()) and not v.strip().replace(".", "").isdigit()


def find_header(rows: Sequence[Sequence[Any]], scan: int = 25) -> int | None:
    """Index of the header row, skipping report titles and preambles.

    The header is the first row that (a) fills most of the table's typical width,
    (b) is all text, and (c) has a row of similar width within the next few rows.
    """
    widths = [_filled(r) for r in rows[: scan * 4]]
    candidates = [w for w in widths if w >= 2]
    if not candidates:
        return None
    typical = Counter(candidates).most_common(1)[0][0]
    for i, row in enumerate(rows[:scan]):
        filled = widths[i]
        if filled < max(2, int(typical * 0.6)):
            continue
        cells = [v for v in row if v not in (None, "")]
        if not all(_is_texty(v) for v in cells):
            continue
        # Look a few rows ahead: blank spacer rows and stray notes often follow a header.
        if any(w >= max(2, int(filled * 0.5)) for w in widths[i + 1 : i + 6]):
            return i
    return None


def header_names(row: Sequence[Any]) -> list[str]:
    """Clean, unique column names; blanks become col_<n>."""
    names: list[str] = []
    seen: Counter[str] = Counter()
    for i, v in enumerate(row):
        name = str(v).strip() if v not in (None, "") else f"col_{i + 1}"
        seen[name] += 1
        names.append(name if seen[name] == 1 else f"{name}_{seen[name]}")
    return names


def is_total_row(values: Sequence[Any]) -> bool:
    first = next((v for v in values if v not in (None, "")), None)
    return isinstance(first, str) and bool(_TOTAL_ROW.match(first))


def render(values: dict[str, Any]) -> str:
    return "; ".join(f"{k}: {v}" for k, v in values.items() if v not in (None, ""))


def rows_to_records(
    header: list[str],
    rows: Sequence[tuple[Location, Sequence[Any]]],
    *,
    fill_down_first_column: bool = False,
) -> list[RawRecord]:
    """Turn data rows into records.

    Blank rows are dropped. Total and subtotal rows are kept but given their own
    kind. Cells beyond the header width go to `_extra` and are flagged
    `stray_cell`. With `fill_down_first_column`, a blank leading cell inherits the
    value above it, within a group, and is flagged `filled_down:<column>` (D-018).
    """
    out: list[RawRecord] = []
    carry: Any = None
    first = header[0] if header else None
    for loc, row in rows:
        if not _filled(row):
            continue
        values: dict[str, Any] = {}
        flags: list[str] = []
        for i, v in enumerate(row):
            if i < len(header):
                values[header[i]] = v
            elif v not in (None, ""):
                values.setdefault("_extra", []).append(v)
                flags.append("stray_cell")
        if len(row) < len(header):
            flags.append("short_row")
        if "_extra" in values and not any(
            v not in (None, "") for k, v in values.items() if k != "_extra"
        ):
            note = " ".join(str(v) for v in values["_extra"])
            out.append(RawRecord("stray_note", loc, note, raw=values, flags=["stray_cell"]))
            continue
        if is_total_row(row):
            out.append(RawRecord("total_row", loc, render(values), raw=values, flags=["total_row"]))
            carry = None
            continue
        if fill_down_first_column and first is not None:
            if values.get(first) in (None, "") and carry is not None:
                values[first] = carry
                flags.append(f"filled_down:{first}")
            elif values.get(first) not in (None, ""):
                carry = values[first]
        out.append(
            RawRecord(
                "row",
                loc,
                render(values),
                raw=values,
                normalized=normalize_row({k: v for k, v in values.items() if k != "_extra"}),
                flags=sorted(set(flags)),
            )
        )
    return out
