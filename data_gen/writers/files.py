"""Deterministic writers for container formats (CSV, XLSX, DOCX, EML).

XLSX and DOCX are zip archives whose entries carry the wall-clock time they were
written, and their metadata stores created and modified timestamps. Both would
make every run's hashes differ, so we pin the metadata and rewrite each archive
with fixed entry times.
"""

import csv
import io
import re
import zipfile
from datetime import datetime
from email.message import EmailMessage
from email.policy import SMTP
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from docx.document import Document as DocxDocument
    from openpyxl import Workbook

FIXED_TIMESTAMP = datetime(2026, 1, 1, 0, 0, 0)
_ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)


_MODIFIED = re.compile(rb"(<dcterms:modified[^>]*>)[^<]*(</dcterms:modified>)")


def normalize_zip(data: bytes) -> bytes:
    """Rewrite a zip archive with fixed timestamps and attributes.

    Also pins `dcterms:modified` in docProps/core.xml: openpyxl stamps the current
    time there on save, whatever the workbook properties say.
    """
    src = zipfile.ZipFile(io.BytesIO(data))
    out = io.BytesIO()
    stamp = FIXED_TIMESTAMP.strftime("%Y-%m-%dT%H:%M:%SZ").encode()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as dst:
        for info in src.infolist():
            content = src.read(info.filename)
            if info.filename == "docProps/core.xml":
                content = _MODIFIED.sub(rb"\g<1>" + stamp + rb"\g<2>", content)
            fixed = zipfile.ZipInfo(info.filename, date_time=_ZIP_EPOCH)
            fixed.compress_type = zipfile.ZIP_DEFLATED
            fixed.external_attr = 0o600 << 16
            dst.writestr(fixed, content)
    return out.getvalue()


def csv_bytes(
    rows: list[list[str]],
    *,
    encoding: str = "utf-8",
    preamble: list[str] | None = None,
    trailing_blank_rows: int = 0,
    bom: bool = False,
    line_terminator: str = "\r\n",
) -> bytes:
    buf = io.StringIO()
    for line in preamble or []:
        buf.write(line + line_terminator)
    writer = csv.writer(buf, lineterminator=line_terminator)
    writer.writerows(rows)
    width = len(rows[0]) if rows else 1
    for _ in range(trailing_blank_rows):
        buf.write("," * (width - 1) + line_terminator)
    data = buf.getvalue().encode(encoding)
    return (b"\xef\xbb\xbf" + data) if bom else data


def xlsx_bytes(wb: "Workbook") -> bytes:
    wb.properties.created = FIXED_TIMESTAMP
    wb.properties.modified = FIXED_TIMESTAMP
    wb.properties.creator = "data_gen"
    wb.properties.lastModifiedBy = "data_gen"
    buf = io.BytesIO()
    wb.save(buf)
    return normalize_zip(buf.getvalue())


def docx_bytes(doc: "DocxDocument") -> bytes:
    props = doc.core_properties
    props.created = FIXED_TIMESTAMP
    props.modified = FIXED_TIMESTAMP
    props.last_printed = FIXED_TIMESTAMP
    props.author = "data_gen"
    props.last_modified_by = "data_gen"
    props.revision = 1
    buf = io.BytesIO()
    doc.save(buf)
    return normalize_zip(buf.getvalue())


def eml_bytes(msg: EmailMessage, boundary_key: str) -> bytes:
    """Serialize with fixed MIME boundaries (the stdlib default is random)."""
    for i, part in enumerate(msg.walk()):
        if part.is_multipart():
            part.set_boundary(f"=_part{i}_{boundary_key}")
    return msg.as_bytes(policy=SMTP)
