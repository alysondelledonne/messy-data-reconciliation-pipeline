"""What the review app shows on the left: the source a golden record's values came from.
Pure data, no Streamlit, so it can be tested."""

import io
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from pipeline.extract.core import STRUCTURED_KINDS, row_columns
from pipeline.store.models import RawRecordRow, SourceFile

MAX_PAGES = 4


@dataclass
class Preview:
    kind: str  # pages | email | table | text | missing
    title: str
    pages: list[bytes] = field(default_factory=list)  # PNG images
    headers: dict[str, str] = field(default_factory=dict)
    text: str = ""
    columns: list[str] = field(default_factory=list)
    rows: list[list[Any]] = field(default_factory=list)
    highlight_row: int | None = None  # index into rows
    lines: list[tuple[int, str, bool]] = field(default_factory=list)  # (number, text, evidence)
    note: str = ""


def resolve_path(sf: SourceFile, files_root: str | None) -> Path | None:
    candidates = []
    if files_root:
        candidates += [Path(files_root) / sf.pack / sf.path, Path(files_root) / sf.path]
    if root := sf.meta.get("root"):
        candidates.append(Path(root) / sf.path)
    return next((p for p in candidates if p.is_file()), None)


def render_pdf(path: Path, pages: int, scale: float = 1.4) -> list[bytes]:
    import pypdfium2 as pdfium

    out = []
    pdf = pdfium.PdfDocument(str(path))
    try:
        for i in range(min(pages or len(pdf), len(pdf), MAX_PAGES)):
            image = pdf[i].render(scale=scale).to_pil()
            buf = io.BytesIO()
            image.save(buf, format="PNG")
            out.append(buf.getvalue())
    finally:
        pdf.close()
    return out


def mark_lines(text: str, evidence: list[str], first: int = 1) -> list[tuple[int, str, bool]]:
    """Number the lines and mark those containing any evidence snippet."""
    needles = [e.strip().casefold() for e in evidence if e and len(e.strip()) >= 2]
    out = []
    for i, line in enumerate(text.splitlines()):
        folded = line.casefold()
        out.append((first + i, line, any(n in folded for n in needles)))
    return out


def _neighbours(
    session: Session, raw: RawRecordRow, around: int = 3
) -> tuple[list[str], list[list[Any]], int | None]:
    """The row with a few rows either side, from the same file (and sheet or table)."""
    loc = raw.location
    row = int(loc.get("row") or loc.get("line") or 0)
    key = "row" if "row" in loc else "line"
    same = [
        r
        for r in session.scalars(
            select(RawRecordRow)
            .where(
                RawRecordRow.source_file_id == raw.source_file_id,
                RawRecordRow.kind.in_(STRUCTURED_KINDS),
            )
            .order_by(RawRecordRow.id)
        )
        if all(r.location.get(k) == v for k, v in loc.items() if k not in ("row", "line"))
        and abs(int(r.location.get(key) or 0) - row) <= around
    ]
    columns: list[str] = []
    cells = []
    for r in same:
        cols = row_columns(r.kind, r.raw)
        # JSONB doesn't keep key order; the record text lists columns as in the file.
        text = r.text or ""
        ordered = sorted(cols, key=lambda c: (text.find(f"{c}:") % (len(text) + 1), c))
        columns += [c for c in ordered if c not in columns]
        cells.append((r, cols))
    rows = [[r.location.get(key)] + [cols.get(c) for c in columns] for r, cols in cells]
    hit = next((i for i, (r, _) in enumerate(cells) if r.id == raw.id), None)
    return [key, *columns], rows, hit


def build_preview(
    session: Session, raw: RawRecordRow, sf: SourceFile, evidence: list[str], files_root: str | None
) -> Preview:
    title = f"{sf.path} · {', '.join(f'{k} {v}' for k, v in raw.location.items())}"
    if sf.format in ("pdf", "pdf_scanned"):
        path = resolve_path(sf, files_root)
        if path is None:
            return Preview(
                "missing",
                title,
                text=raw.text or "",
                lines=mark_lines(raw.text or "", evidence),
                note="Source file not found: set FILES_ROOT to the corpus folder.",
            )
        pages = int(raw.location.get("pages") or raw.location.get("page") or 0)
        lines = mark_lines(raw.text or "", evidence) if sf.format == "pdf" else []
        return Preview("pages", title, pages=render_pdf(path, pages), lines=lines)
    if sf.format == "eml":
        headers = {k: str(v) for k, v in (sf.meta.get("headers") or {}).items()}
        return Preview(
            "email",
            title,
            headers=headers,
            text=raw.text or "",
            lines=mark_lines(raw.text or "", evidence),
        )
    if raw.kind in STRUCTURED_KINDS:
        columns, rows, hit = _neighbours(session, raw)
        return Preview("table", title, columns=columns, rows=rows, highlight_row=hit)
    first = int(raw.location.get("line_start") or raw.location.get("line") or 1)
    return Preview(
        "text", title, text=raw.text or "", lines=mark_lines(raw.text or "", evidence, first)
    )


def evidence_of(field_meta: dict[str, Any]) -> list[str]:
    out = []
    for meta in field_meta.values():
        ev = (meta or {}).get("evidence")
        if isinstance(ev, str) and ev.strip():
            out += [p for p in re.split(r"\s*\|\s*", ev) if p]
    return out
