from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from pipeline.config import get_settings
from pipeline.run import main


def _tree(tmp_path: Path) -> Path:
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "a.csv").write_bytes(b"Vendor,Amount\nAcme,$1.00\n")
    (tmp_path / "data" / "n.md").write_text("# hi\n")
    (tmp_path / "data" / "ground_truth.json").write_text("{}")
    return tmp_path / "data"


def test_ingest_dry_run_summarizes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["ingest", "--pack", "demo", "--dry-run", str(_tree(tmp_path))]) == 0
    out = capsys.readouterr().out
    assert "csv" in out and "md" in out
    assert "ground_truth" not in out
    assert "dry run" in out


def test_missing_path_is_an_error(tmp_path: Path) -> None:
    assert main(["ingest", "--pack", "demo", "--dry-run", str(tmp_path / "nope")]) == 2


def test_ingest_stores_into_database(
    tmp_path: Path, db_url: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DATABASE_URL", db_url)
    get_settings.cache_clear()
    try:
        assert main(["ingest", "--pack", "cli-test", str(_tree(tmp_path))]) == 0
        assert "2 files stored" in capsys.readouterr().out
        engine = create_engine(db_url)
        with engine.begin() as conn:
            n = conn.execute(text("select count(*) from source_files where pack='cli-test'"))
            assert n.scalar() == 2
            conn.execute(text("delete from source_files where pack='cli-test'"))
        engine.dispose()
    finally:
        get_settings.cache_clear()


def test_extract_estimate_and_missing_key(
    db_url: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    get_settings.cache_clear()
    try:
        assert main(["extract", "--pack", "contacts", "--estimate"]) == 0
        assert "estimated" in capsys.readouterr().out
        assert main(["extract", "--pack", "contacts"]) == 2
        assert "ANTHROPIC_API_KEY is not set" in capsys.readouterr().err
    finally:
        get_settings.cache_clear()


def test_validate_command(
    db_url: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DATABASE_URL", db_url)
    get_settings.cache_clear()
    try:
        assert main(["validate", "--pack", "invoices"]) == 0
        assert "validated" in capsys.readouterr().out
    finally:
        get_settings.cache_clear()
