"""Walk input paths and run the right adapter on each file. No database access here."""

import fnmatch
import hashlib
from collections.abc import Iterable, Iterator
from pathlib import Path

from pipeline.ingest.adapters.registry import ADAPTERS
from pipeline.ingest.models import IngestedFile

# Generator artifacts that sit next to the corpus but are not customer data.
DEFAULT_EXCLUDE = ("ground_truth.json", "manifest.json", ".*")


def ingest_bytes(path: str, data: bytes) -> IngestedFile:
    suffix = Path(path).suffix.lower()
    fmt, adapter = ADAPTERS.get(suffix, (suffix.lstrip(".") or "unknown", None))
    f = IngestedFile(
        path=path, format=fmt, sha256=hashlib.sha256(data).hexdigest(), size_bytes=len(data)
    )
    if adapter is None:
        f.status, f.error = "unsupported", f"no adapter for {suffix or 'files without extension'}"
        return f
    try:
        adapter(data, f)
    except Exception as e:  # one bad file must not stop the run
        f.status, f.error, f.records = "error", f"{type(e).__name__}: {e}", []
    return f


def iter_files(
    inputs: Iterable[Path], exclude: Iterable[str] = DEFAULT_EXCLUDE
) -> Iterator[tuple[Path, Path]]:
    """Yield (root, file) pairs in a stable order. `root` makes stored paths relative."""
    patterns = tuple(exclude)
    for inp in inputs:
        root = inp if inp.is_dir() else inp.parent
        files = sorted(inp.rglob("*")) if inp.is_dir() else [inp]
        for p in files:
            if p.is_file() and not any(fnmatch.fnmatch(p.name, pat) for pat in patterns):
                yield root, p


def ingest_paths(
    inputs: Iterable[Path], exclude: Iterable[str] = DEFAULT_EXCLUDE
) -> list[IngestedFile]:
    out = []
    for root, p in iter_files(inputs, exclude):
        out.append(ingest_bytes(p.relative_to(root).as_posix(), p.read_bytes()))
    return out
