"""TXT / MD: one record per file covering every line; the extractor cites line numbers."""

from pipeline.ingest.models import IngestedFile, RawRecord
from pipeline.ingest.normalize import decode_text


def ingest_text(data: bytes, f: IngestedFile) -> None:
    text, f.encoding = decode_text(data)
    lines = text.splitlines()
    n = max(len(lines), 1)
    flags = ["markdown"] if f.format == "md" else []
    f.records.append(
        RawRecord("document", {"line_start": 1, "line_end": n}, text, raw={"line_count": n},
                  flags=flags)
    )  # fmt: skip
