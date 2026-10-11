"""Engine and session helpers."""

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from pipeline.config import get_settings


def make_engine(url: str | None = None) -> Engine:
    return create_engine(url or get_settings().database_url, pool_pre_ping=True)


def make_session(engine: Engine) -> Session:
    return sessionmaker(engine, expire_on_commit=False)()
