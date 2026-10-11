"""Persist ingestion results."""

from collections.abc import Iterable
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from pipeline.ingest.models import IngestedFile
from pipeline.store.models import IngestRun, RawRecordRow, SourceFile


def save_ingest(
    session: Session, pack: str, files: Iterable[IngestedFile], inputs: list[str] | None = None
) -> IngestRun:
    """Store files and their records in one transaction.

    - A file already stored with the same pack, path and content is skipped, so
      re-running is safe.
    - The same content under a different path is stored again and noted in
      `meta.identical_to`. Exact copies are a real signal for dedupe later.
    """
    run = IngestRun(
        pack=pack,
        inputs=inputs or [],
        files_seen=0,
        files_ingested=0,
        files_skipped=0,
        files_failed=0,
        records=0,
    )
    session.add(run)
    for f in files:
        run.files_seen += 1
        exists = session.scalar(
            select(SourceFile.id).where(
                SourceFile.pack == pack, SourceFile.path == f.path, SourceFile.sha256 == f.sha256
            )
        )
        if exists is not None:
            run.files_skipped += 1
            continue
        meta = dict(f.meta)
        twins = session.scalars(
            select(SourceFile.path).where(SourceFile.pack == pack, SourceFile.sha256 == f.sha256)
        ).all()
        twins = [*twins, *(s.path for s in run.files if s.sha256 == f.sha256)]
        if twins:
            meta["identical_to"] = sorted(set(twins))
        sf = SourceFile(
            pack=pack,
            path=f.path,
            format=f.format,
            sha256=f.sha256,
            size_bytes=f.size_bytes,
            encoding=f.encoding,
            status=f.status,
            error=f.error,
            meta=meta,
            records=[
                RawRecordRow(
                    kind=r.kind,
                    location=r.location,
                    text=r.text,
                    raw=r.raw,
                    normalized=r.normalized,
                    flags=r.flags,
                    content_hash=r.content_hash,
                )
                for r in f.records
            ],
        )
        run.files.append(sf)
        if f.status == "ok":
            run.files_ingested += 1
        else:
            run.files_failed += 1
        run.records += len(f.records)
    run.finished_at = datetime.now(UTC)
    session.commit()
    return run
