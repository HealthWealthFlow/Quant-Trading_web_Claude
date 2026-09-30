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


# Forward-only, additive migrations: version N -> N+1. Never drop or rewrite research data.
MIGRATIONS: dict[int, list[str]] = {
    1: ["ALTER TABLE ideas ADD COLUMN score_details JSON NOT NULL DEFAULT '{}'"],
}


def _migrate(engine: Engine, from_version: int) -> int:
    version = from_version
    while version < SCHEMA_VERSION:
        steps = MIGRATIONS.get(version)
        if steps is None:
            raise SchemaVersionError(f"no migration from schema v{version}")
        with engine.begin() as conn:
            for sql in steps:
                conn.exec_driver_sql(sql)
            conn.exec_driver_sql("UPDATE schema_meta SET value = ? WHERE key = 'schema_version'", (str(version + 1),))
        version += 1
    return version


def init_db(engine: Engine) -> int:
    """Create missing tables, apply pending additive migrations, and record the schema version."""
    with Session(engine) as s:
        existing = inspect(engine).has_table("schema_meta") and s.get(SchemaMeta, "schema_version")
        current = int(existing.value) if existing else None
    if current is not None and current > SCHEMA_VERSION:
        raise SchemaVersionError(f"database schema v{current} is newer than code schema v{SCHEMA_VERSION}")
    if current is not None and current < SCHEMA_VERSION:
        _migrate(engine, current)
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        if s.get(SchemaMeta, "schema_version") is None:
            s.add(SchemaMeta(key="schema_version", value=str(SCHEMA_VERSION)))
            s.commit()
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
