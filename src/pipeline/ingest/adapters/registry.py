"""Map file extensions to adapters."""

from collections.abc import Callable

from pipeline.ingest.adapters.csv_ import ingest_csv
from pipeline.ingest.adapters.docx_ import ingest_docx
from pipeline.ingest.adapters.eml import ingest_eml
from pipeline.ingest.adapters.jsonish import ingest_json, ingest_jsonl, ingest_log
from pipeline.ingest.adapters.pdf import ingest_pdf
from pipeline.ingest.adapters.text import ingest_text
from pipeline.ingest.adapters.xlsx import ingest_xlsx
from pipeline.ingest.models import IngestedFile

Adapter = Callable[[bytes, IngestedFile], None]

ADAPTERS: dict[str, tuple[str, Adapter]] = {
    ".csv": ("csv", ingest_csv),
    ".xlsx": ("xlsx", ingest_xlsx),
    ".pdf": ("pdf", ingest_pdf),
    ".eml": ("eml", ingest_eml),
    ".docx": ("docx", ingest_docx),
    ".txt": ("txt", ingest_text),
    ".md": ("md", ingest_text),
    ".jsonl": ("jsonl", ingest_jsonl),
    ".ndjson": ("jsonl", ingest_jsonl),
    ".json": ("json", ingest_json),
    ".log": ("log", ingest_log),
}
