import pytest

from data_gen.build import build
from data_gen.ground_truth import GroundTruth

SEED = 42


@pytest.fixture(scope="session")
def corpus() -> dict[str, bytes]:
    """The full default corpus, built once per test session (in memory)."""
    return build(SEED)


def load_gt(corpus: dict[str, bytes], pack: str) -> GroundTruth:
    return GroundTruth.model_validate_json(corpus[f"{pack}/ground_truth.json"])


@pytest.fixture(scope="session")
def invoices_gt(corpus: dict[str, bytes]) -> GroundTruth:
    return load_gt(corpus, "invoices")


@pytest.fixture(scope="session")
def contacts_gt(corpus: dict[str, bytes]) -> GroundTruth:
    return load_gt(corpus, "contacts")


# --- Postgres (D-017) --------------------------------------------------------------
# DB tests use a separate `<db>_test` database, migrated fresh once per session.
# Without a reachable Postgres they skip locally; with REQUIRE_DB=1 (CI) they fail.


def _test_db_url() -> str:
    import os

    from sqlalchemy.engine import make_url

    from pipeline.config import get_settings

    if url := os.environ.get("TEST_DATABASE_URL"):
        return url
    base = make_url(get_settings().database_url)
    return base.set(database=f"{base.database}_test").render_as_string(hide_password=False)


@pytest.fixture(scope="session")
def db_url() -> str:
    import os

    from alembic import command
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url
    from sqlalchemy.exc import OperationalError

    from pipeline.run import alembic_config

    url = _test_db_url()
    name = make_url(url).database
    admin = make_url(url).set(database="postgres")
    try:
        engine = create_engine(
            admin, isolation_level="AUTOCOMMIT", connect_args={"connect_timeout": 3}
        )
        with engine.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
            conn.execute(text(f'CREATE DATABASE "{name}"'))
        engine.dispose()
    except OperationalError as e:
        if os.environ.get("REQUIRE_DB"):
            raise
        pytest.skip(
            f"Postgres not reachable ({e.__class__.__name__}); start it with docker compose"
        )
    cfg = alembic_config(url)
    cfg.attributes["configure_logger"] = False
    command.upgrade(cfg, "head")
    return url


@pytest.fixture
def db_session(db_url: str):  # type: ignore[no-untyped-def]
    """A session whose work is rolled back after each test (commits become savepoints)."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    engine = create_engine(db_url)
    conn = engine.connect()
    outer = conn.begin()
    session = Session(bind=conn, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        outer.rollback()
        conn.close()
        engine.dispose()
