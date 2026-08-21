from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from . import config

connect_args = (
    {"check_same_thread": False} if config.DATABASE_URL.startswith("sqlite") else {}
)
engine = create_engine(
    config.DATABASE_URL, connect_args=connect_args, pool_pre_ping=True
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


if config.DATABASE_URL.startswith("sqlite"):
    # SQLite ignores foreign keys unless asked. Turning this on means a
    # constraint bug fails on your laptop instead of on Postgres in production.
    from sqlalchemy import event

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_conn, _record):  # pragma: no cover
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    from . import models  # noqa: F401  (registers tables)

    Base.metadata.create_all(bind=engine)
