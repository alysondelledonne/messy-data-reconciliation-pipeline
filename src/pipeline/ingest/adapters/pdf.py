"""PDF: one record per document. Text PDFs keep page-marked text; image-only
PDFs are flagged `needs_vision` and sent to the model as a PDF in Milestone 3."""

import io

import pdfplumber

from pipeline.ingest.models import IngestedFile, Location, RawRecord


def ingest_pdf(data: bytes, f: IngestedFile) -> None:
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        pages = [(p.extract_text() or "") for p in pdf.pages]
        image_pages = sum(1 for p in pdf.pages if p.images)
    n = len(pages)
    f.meta["pages"] = n
    loc: Location = {"pages": n}
    if not any(t.strip() for t in pages):
        f.format = "pdf_scanned"
        f.records.append(
            RawRecord(
                "document",
                loc,
                "",
                raw={"page_count": n, "image_pages": image_pages},
                flags=["needs_vision", "no_text_layer"],
            )
        )
        return
    text = "\n".join(f"--- page {i} of {n} ---\n{t}" for i, t in enumerate(pages, 1))
    flags = ["multi_page"] if n > 1 else []
    f.records.append(RawRecord("document", loc, text, raw={"page_count": n}, flags=flags))
