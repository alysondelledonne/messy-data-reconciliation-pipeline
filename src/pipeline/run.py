"""Pipeline CLI.

uv run python -m pipeline.run db upgrade
uv run python -m pipeline.run ingest --pack invoices data/generated/invoices [--dry-run]
uv run python -m pipeline.run extract --pack invoices [--limit N] [--estimate]
"""

import argparse
import sys
from collections import Counter
from pathlib import Path

from alembic import command
from alembic.config import Config

from pipeline.ingest.ingest import DEFAULT_EXCLUDE, ingest_paths
from pipeline.ingest.models import IngestedFile

ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"


def alembic_config(url: str | None = None) -> Config:
    cfg = Config(str(ALEMBIC_INI))
    if url:
        cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def summarize(files: list[IngestedFile]) -> str:
    lines = []
    by_format: Counter[str] = Counter()
    records: Counter[str] = Counter()
    for f in files:
        by_format[f.format] += 1
        records[f.format] += len(f.records)
    lines.append(f"{'format':<12} {'files':>6} {'records':>8}")
    for fmt in sorted(by_format):
        lines.append(f"{fmt:<12} {by_format[fmt]:>6} {records[fmt]:>8}")
    flags = Counter(fl.split(":")[0] for f in files for r in f.records for fl in r.flags)
    if flags:
        lines.append("flags: " + ", ".join(f"{k}={v}" for k, v in flags.most_common()))
    for f in files:
        if f.status != "ok":
            lines.append(f"  {f.status.upper()}: {f.path}: {f.error}")
    return "\n".join(lines)


def cmd_db(args: argparse.Namespace) -> int:
    cfg = alembic_config()
    if args.action == "upgrade":
        command.upgrade(cfg, args.revision or "head")
    elif args.action == "downgrade":
        command.downgrade(cfg, args.revision or "-1")
    else:
        command.current(cfg, verbose=True)
    return 0


def cmd_ingest(args: argparse.Namespace) -> int:
    missing = [p for p in args.paths if not p.exists()]
    if missing:
        print(f"not found: {', '.join(map(str, missing))}", file=sys.stderr)
        return 2
    files = ingest_paths(args.paths, exclude=[*DEFAULT_EXCLUDE, *args.exclude])
    print(summarize(files))
    if args.dry_run:
        print("(dry run: nothing stored)")
        return 0
    from pipeline.store.db import make_engine, make_session
    from pipeline.store.ingest_repo import save_ingest

    with make_session(make_engine()) as session:
        run = save_ingest(session, args.pack, files, inputs=[str(p) for p in args.paths])
        print(
            f"ingest run {run.id}: {run.files_ingested} files stored, "
            f"{run.files_skipped} unchanged (skipped), {run.files_failed} failed or unsupported, "
            f"{run.records} records"
        )
    return 1 if any(f.status == "error" for f in files) else 0


def cmd_extract(args: argparse.Namespace) -> int:
    from pipeline.config import get_settings
    from pipeline.extract.runner import Extractor, estimate
    from pipeline.store.db import make_engine, make_session

    settings = get_settings()
    with make_session(make_engine()) as session:
        if args.estimate:
            print(estimate(session, args.pack, settings, args.limit))
            return 0
        if settings.anthropic_api_key is None:
            print(
                "ANTHROPIC_API_KEY is not set (add it to .env). "
                "Use --estimate to preview without calling the API.",
                file=sys.stderr,
            )
            return 2
        from pipeline.extract.client import AnthropicLLMClient

        client = AnthropicLLMClient(settings)
        extractor = Extractor(session, client, args.pack, settings, files_root=args.files_root)
        print(extractor.run(limit=args.limit).summary())
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="pipeline", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    db = sub.add_parser("db", help="database migrations")
    db.add_argument("action", choices=["upgrade", "downgrade", "current"])
    db.add_argument("revision", nargs="?")
    db.set_defaults(func=cmd_db)

    ing = sub.add_parser("ingest", help="load files into raw_records")
    ing.add_argument("--pack", required=True, help="domain pack these files belong to")
    ing.add_argument("paths", nargs="+", type=Path, help="files or directories")
    ing.add_argument("--exclude", action="append", default=[], help="extra filename glob to skip")
    ing.add_argument("--dry-run", action="store_true", help="parse and summarize; store nothing")
    ing.set_defaults(func=cmd_ingest)

    ext = sub.add_parser("extract", help="extract schema records from raw_records with Claude")
    ext.add_argument("--pack", required=True)
    ext.add_argument("--limit", type=int, help="process at most N documents (mapped rows always)")
    ext.add_argument("--estimate", action="store_true", help="estimate calls and cost; no API")
    ext.add_argument("--files-root", type=Path, help="where source files live, for scanned PDFs")
    ext.set_defaults(func=cmd_extract)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
