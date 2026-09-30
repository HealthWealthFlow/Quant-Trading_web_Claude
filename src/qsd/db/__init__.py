"""Research database: SQLite via SQLAlchemy (Postgres-compatible types)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Engine, create_engine, event, func, inspect, select
from sqlalchemy.orm import Session, sessionmaker

from ..taxonomy import MarketRegime
from .models import SCHEMA_VERSION, Base, Idea, IdeaRegime, SchemaMeta

DB_FILENAME = "qsd.sqlite"


class SchemaVersionError(RuntimeError):
    pass


def make_engine(url_or_path: str | Path) -> Engine:
    url = str(url_or_path)
    if "://" not in url:
        Path(url).parent.mkdir(parents=True, exist_ok=True)
        url = f"sqlite:///{url}"
    engine = create_engine(url, future=True)
    if engine.dialect.name == "sqlite":
        @event.listens_for(engine, "connect")
        def _pragmas(dbapi_conn, _record):  # noqa: ANN001
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.close()
    return engine


def init_db(engine: Engine) -> int:
    """Create missing tables and record the schema version. Refuses to run against a different version."""
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        row = s.get(SchemaMeta, "schema_version")
        if row is None:
            s.add(SchemaMeta(key="schema_version", value=str(SCHEMA_VERSION)))
            s.commit()
        elif int(row.value) != SCHEMA_VERSION:
            raise SchemaVersionError(
                f"database schema v{row.value} != code schema v{SCHEMA_VERSION}; run a migration first"
            )
    return SCHEMA_VERSION


def check_schema(engine: Engine) -> None:
    with Session(engine) as s:
        row = s.get(SchemaMeta, "schema_version")
        if row is None or int(row.value) != SCHEMA_VERSION:
            found = row.value if row else "none"
            raise SchemaVersionError(f"expected schema v{SCHEMA_VERSION}, found {found}; run `qsd db init`")


def table_counts(engine: Engine) -> dict[str, int]:
    out: dict[str, int] = {}
    names = set(inspect(engine).get_table_names())
    with Session(engine) as s:
        for table in Base.metadata.sorted_tables:
            if table.name in names:
                out[table.name] = s.execute(select(func.count()).select_from(table)).scalar_one()
    return out


@contextmanager
def session_scope(engine: Engine) -> Iterator[Session]:
    factory = sessionmaker(engine, expire_on_commit=False)
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def new_idea(**fields: object) -> Idea:
    """Create an Idea with all four market-regime rows present and UNKNOWN (spec §137)."""
    idea = Idea(**fields)
    idea.regimes = [IdeaRegime(regime=r) for r in MarketRegime]
    return idea
