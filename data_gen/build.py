"""Build the whole corpus in memory, then write it out with a hash manifest."""

import json
import shutil
from collections.abc import Callable
from pathlib import Path

from data_gen import GENERATOR_VERSION
from data_gen.corpus import PackOutput, sha256
from data_gen.packs.contacts.generate import generate as generate_contacts
from data_gen.packs.invoices.generate import generate as generate_invoices

PACKS: dict[str, Callable[[int], PackOutput]] = {
    "invoices": generate_invoices,
    "contacts": generate_contacts,
}
MANIFEST = "manifest.json"


def build(seed: int, packs: list[str] | None = None) -> dict[str, bytes]:
    """Return every corpus file (including ground truth and manifest) as path -> bytes."""
    files: dict[str, bytes] = {}
    for name in packs or list(PACKS):
        out = PACKS[name](seed)
        assert out.ground_truth is not None
        files.update(out.files)
        files[f"{name}/ground_truth.json"] = (
            out.ground_truth.model_dump_json(indent=2).encode("utf-8") + b"\n"
        )
    manifest = {
        "seed": seed,
        "generator_version": GENERATOR_VERSION,
        "files": {
            path: {"sha256": sha256(data), "bytes": len(data)}
            for path, data in sorted(files.items())
        },
    }
    files[MANIFEST] = (json.dumps(manifest, indent=2) + "\n").encode("utf-8")
    return files


def write(files: dict[str, bytes], out_dir: Path) -> None:
    """Replace `out_dir` with the corpus. Refuses to wipe a directory we didn't create."""
    if out_dir.exists() and any(out_dir.iterdir()) and not (out_dir / MANIFEST).exists():
        raise SystemExit(f"refusing to overwrite {out_dir}: it is not empty and has no {MANIFEST}")
    if out_dir.exists():
        shutil.rmtree(out_dir)
    for path, data in files.items():
        target = out_dir / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
