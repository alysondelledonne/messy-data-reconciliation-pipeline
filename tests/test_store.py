"""Store layer and migrations, against a real Postgres 16 (skipped if unreachable)."""

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, func, inspect, select
from sqlalchemy.orm import Session

from pipeline.ingest.ingest import ingest_bytes
from pipeline.ingest.models import IngestedFile
from pipeline.run import alembic_config
from pipeline.store.ingest_repo import save_ingest
from pipeline.store.models import Base, RawRecordRow, SourceFile

CSV = b"Vendor,Amount\nAcme,$1.00\nBolt,$2.00\n"


def _files() -> list[IngestedFile]:
    return [
        ingest_bytes("a.csv", CSV),
        ingest_bytes("copy/a.csv", CSV),
        ingest_bytes("notes.txt", "Côté\n".encode()),
        ingest_bytes("x.bin", b"\x00"),
    ]


def test_migration_matches_models(db_url: str) -> None:
    engine = create_engine(db_url)
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    engine.dispose()
    assert diff == [], "models and migrations disagree; write a new Alembic revision"


def test_save_ingest_stores_files_records_and_json(db_session: Session) -> None:
    run = save_ingest(db_session, "demo", _files(), inputs=["somewhere"])
    assert (run.files_seen, run.files_ingested, run.files_failed) == (4, 3, 1)
    assert run.records == 5
    rows = db_session.scalars(
        select(RawRecordRow).join(SourceFile).where(SourceFile.path == "a.csv")
    ).all()
    assert [r.location for r in rows] == [{"row": 2}, {"row": 3}]
    assert rows[0].raw == {"Vendor": "Acme", "Amount": "$1.00"}
    assert rows[0].normalized["Amount"]["value"] == "1.00"
    # JSONB is queryable: find the record whose normalized amount is 2.00
    hit = db_session.scalar(
        select(RawRecordRow.raw["Vendor"].astext).where(
            RawRecordRow.normalized["Amount"]["value"].astext == "2.00"
        )
    )
    assert hit == "Bolt"
    unsupported = db_session.scalar(select(SourceFile).where(SourceFile.path == "x.bin"))
    assert unsupported is not None and unsupported.status == "unsupported"


def test_reingest_is_idempotent_and_flags_identical_files(db_session: Session) -> None:
    save_ingest(db_session, "demo", _files())
    again = save_ingest(db_session, "demo", _files())
    assert (again.files_skipped, again.files_ingested, again.records) == (4, 0, 0)
    assert db_session.scalar(select(func.count()).select_from(SourceFile)) == 4
    copy = db_session.scalar(select(SourceFile).where(SourceFile.path == "copy/a.csv"))
    assert copy is not None and copy.meta["identical_to"] == ["a.csv"]
    # Same path, new content: a new version is stored alongside the old one.
    changed = save_ingest(db_session, "demo", [ingest_bytes("a.csv", CSV + b"Cog,$3\n")])
    assert changed.files_ingested == 1
    # Packs are separate namespaces.
    assert save_ingest(db_session, "other", _files()).files_ingested == 3


def test_deleting_a_file_cascades_to_its_records(db_session: Session) -> None:
    save_ingest(db_session, "demo", _files())
    sf = db_session.scalar(select(SourceFile).where(SourceFile.path == "a.csv"))
    db_session.delete(sf)
    db_session.flush()
    assert db_session.scalar(select(func.count()).select_from(RawRecordRow)) == 3


def test_downgrade_and_upgrade_round_trip(db_url: str) -> None:
    cfg = alembic_config(db_url)
    cfg.attributes["configure_logger"] = False
    command.downgrade(cfg, "base")
    engine = create_engine(db_url)
    assert "raw_records" not in inspect(engine).get_table_names()
    command.upgrade(cfg, "head")
    assert {"ingest_runs", "source_files", "raw_records"} <= set(inspect(engine).get_table_names())
    engine.dispose()
