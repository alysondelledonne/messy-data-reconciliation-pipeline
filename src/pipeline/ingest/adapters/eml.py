"""EML: one record per part. The sender's own text (`body`), the quoted reply
chain (`quoted`), and each attachment (`attachment:<filename>`) are kept separate,
because they usually describe different people."""

import re
from email import message_from_bytes, policy
from email.message import EmailMessage
from html.parser import HTMLParser

from pipeline.ingest.models import IngestedFile, Location, RawRecord
from pipeline.ingest.normalize import decode_text, parse_date

_REPLY_HEADER = re.compile(r"^(On .+ wrote:|-{2,}\s*Original Message\s*-{2,}|From: .+)$", re.M)


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("p", "br", "div", "tr", "li"):
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def html_to_text(markup: str) -> str:
    p = _TextExtractor()
    p.feed(markup)
    return re.sub(r"\n{3,}", "\n\n", "".join(p.parts)).strip()


def split_quoted(body: str) -> tuple[str, str | None]:
    """Split the sender's own text from a quoted reply chain (if any)."""
    lines = body.splitlines()
    for i, line in enumerate(lines):
        if _REPLY_HEADER.match(line.strip()) and i > 0:
            own = "\n".join(lines[:i]).rstrip()
            quoted = [line.strip()] + [re.sub(r"^>\s?", "", q) for q in lines[i + 1 :]]
            return own, "\n".join(quoted).strip()
    first_quote = next((i for i, ln in enumerate(lines) if ln.startswith(">")), None)
    if first_quote:
        own = "\n".join(lines[:first_quote]).rstrip()
        quoted = [re.sub(r"^>\s?", "", q) for q in lines[first_quote:]]
        return own, "\n".join(quoted).strip()
    return body.strip(), None


def ingest_eml(data: bytes, f: IngestedFile) -> None:
    msg = message_from_bytes(data, policy=policy.default)
    assert isinstance(msg, EmailMessage)
    headers = {k: str(msg[k]) for k in ("From", "To", "Cc", "Subject", "Date") if msg[k]}
    f.meta["headers"] = headers
    normalized: dict[str, object] = {}
    if "Date" in headers and (d := parse_date(headers["Date"])) and d.value:
        normalized["Date"] = {"type": "date", "value": d.value.isoformat()}
    header_text = "\n".join(f"{k}: {v}" for k, v in headers.items())

    body_part = msg.get_body(preferencelist=("plain", "html"))
    body = ""
    flags: list[str] = []
    if body_part is not None:
        content = body_part.get_content()
        if body_part.get_content_subtype() == "html":
            content, flags = html_to_text(content), ["html_only"]
        body = content
    own, quoted = split_quoted(body)
    f.records.append(
        RawRecord("email_body", {"part": "body"}, f"{header_text}\n\n{own}", raw=headers,
                  normalized=normalized, flags=flags)
    )  # fmt: skip
    if quoted:
        f.records.append(RawRecord("email_quoted", {"part": "quoted"}, quoted, flags=["quoted"]))
    for att in msg.iter_attachments():
        name = att.get_filename() or "unnamed"
        ctype = att.get_content_type()
        loc: Location = {"part": f"attachment:{name}"}
        if att.get_content_maintype() == "text":
            # Decode from the bytes: text attachments often omit or misstate their charset.
            payload = att.get_payload(decode=True)
            text, enc = decode_text(payload if isinstance(payload, bytes) else b"")
            f.records.append(
                RawRecord("email_attachment", loc, text,
                          raw={"filename": name, "content_type": ctype, "encoding": enc})
            )  # fmt: skip
        else:
            payload = att.get_payload(decode=True)
            size = len(payload) if isinstance(payload, bytes) else 0
            f.records.append(
                RawRecord("email_attachment", loc, "",
                          raw={"filename": name, "content_type": ctype, "size_bytes": size},
                          flags=["binary_attachment"])
            )  # fmt: skip
