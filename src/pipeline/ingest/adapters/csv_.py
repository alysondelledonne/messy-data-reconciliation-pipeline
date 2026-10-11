"""CSV: detect the encoding, skip preambles, find the header, one record per data row."""

import csv
import io

from pipeline.ingest.adapters.tabular import find_header, header_names, rows_to_records
from pipeline.ingest.models import IngestedFile, Location
from pipeline.ingest.normalize import decode_text


def ingest_csv(data: bytes, f: IngestedFile) -> None:
    text, f.encoding = decode_text(data)
    if f.encoding not in ("utf-8", "utf-8-sig"):
        f.meta["non_utf8_encoding"] = True
    reader = csv.reader(io.StringIO(text, newline=""))
    rows: list[tuple[int, list[str]]] = []  # (physical line number where the row starts, cells)
    start = 1
    for row in reader:
        rows.append((start, row))
        start = reader.line_num + 1
    h = find_header([r for _, r in rows])
    if h is None:
        f.status, f.error = "error", "no header row found"
        return
    header = header_names(rows[h][1])
    f.meta["header_line"] = rows[h][0]
    f.meta["preamble"] = [",".join(r).strip(",") for _, r in rows[:h] if any(r)]
    data_rows: list[tuple[Location, list[str]]] = [({"row": n}, r) for n, r in rows[h + 1 :]]
    f.meta["blank_rows"] = sum(1 for _, r in data_rows if not any(c.strip() for c in r))
    f.records = rows_to_records(header, data_rows)
