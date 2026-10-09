"""Regenerate the messy corpus: `uv run python -m data_gen [--seed 42] [--out data/generated]`."""

import argparse
import json
from collections import Counter
from pathlib import Path

from data_gen.build import MANIFEST, PACKS, build, write


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", type=Path, default=Path("data/generated"))
    parser.add_argument("--packs", nargs="+", choices=sorted(PACKS), default=None)
    args = parser.parse_args(argv)

    files = build(args.seed, args.packs)
    write(files, args.out)

    manifest = json.loads(files[MANIFEST])
    by_dir = Counter(p.rsplit("/", 1)[0] for p in manifest["files"])
    total = sum(f["bytes"] for f in manifest["files"].values())
    print(f"Wrote {len(manifest['files'])} files ({total / 1e6:.1f} MB) to {args.out}/")
    for d, n in sorted(by_dir.items()):
        print(f"  {d:<20} {n:>4} files")
    print(f"Manifest: {args.out / MANIFEST} (seed={args.seed})")


if __name__ == "__main__":
    main()
