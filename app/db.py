"""Storage: SQLite locally, Postgres (Neon) in production, through SQLAlchemy Core."""
import json
import time
import uuid

from sqlalchemy import (
    Column, Float, Integer, MetaData, String, Table, Text, create_engine, delete, insert, select, update,
)

from .config import settings


def _normalize(url: str) -> str:
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


_url = _normalize(settings.database_url)
_kwargs = {"pool_pre_ping": True}
if _url.startswith("sqlite"):
    _kwargs["connect_args"] = {"check_same_thread": False}
else:
    _kwargs.update(pool_size=5, max_overflow=5, pool_recycle=300)
engine = create_engine(_url, **_kwargs)
meta = MetaData()

runs = Table(
    "runs", meta,
    Column("id", String(40), primary_key=True),
    Column("lead_id", String(20), index=True),
    Column("status", String(20)),          # running | completed | blocked | failed
    Column("decision", String(20)),        # draft | disqualify | skip | blocked | error
    Column("summary", Text),
    Column("provider", String(40)),
    Column("model", String(80)),
    Column("eval_batch", String(40), index=True),
    Column("started_at", Float),
    Column("ended_at", Float),
    Column("steps", Integer, default=0),
    Column("tokens_in", Integer, default=0),
    Column("tokens_out", Integer, default=0),
    Column("cost_usd", Float, default=0.0),
    Column("error", Text),
)

spans = Table(
    "spans", meta,
    Column("id", String(40), primary_key=True),
    Column("run_id", String(40), index=True),
    Column("seq", Integer),
    Column("kind", String(20)),            # llm | tool | guard | memory
    Column("name", String(80)),
    Column("status", String(20)),          # ok | blocked | error | flagged
    Column("input", Text),
    Column("output", Text),
    Column("flags", Text),
    Column("started_at", Float),
    Column("duration_ms", Float),
    Column("tokens_in", Integer, default=0),
    Column("tokens_out", Integer, default=0),
)

drafts = Table(
    "drafts", meta,
    Column("id", String(40), primary_key=True),
    Column("run_id", String(40), index=True),
    Column("lead_id", String(20), index=True),
    Column("subject", Text),
    Column("body", Text),
    Column("facts", Text),
    Column("checks", Text),
    Column("status", String(20)),          # pending | approved | rejected | eval
    Column("reviewer_note", Text),
    Column("created_at", Float),
    Column("reviewed_at", Float),
)

memories = Table(
    "memories", meta,
    Column("id", String(40), primary_key=True),
    Column("scope", String(80), index=True),   # lead:L-01 | company:x.example | global
    Column("kind", String(20)),                # outreach | lesson
    Column("content", Text),
    Column("source", String(40)),              # seed | run id | review
    Column("created_at", Float),
)

eval_runs = Table(
    "eval_runs", meta,
    Column("id", String(40), primary_key=True),
    Column("status", String(20)),
    Column("provider", String(40)),
    Column("model", String(80)),
    Column("started_at", Float),
    Column("ended_at", Float),
    Column("summary", Text),
    Column("results", Text),
)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def loads(value):
    if value in (None, ""):
        return None
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return value


def row(r) -> dict:
    d = dict(r._mapping)
    for key in ("input", "output", "flags", "facts", "checks", "summary", "results"):
        if key in d and isinstance(d[key], str):
            d[key] = loads(d[key])
    return d


def init_db() -> None:
    meta.create_all(engine)
    seed_memories()


def seed_memories() -> None:
    """Demo state: Sofia Rossi (L-07) was already emailed ten days ago."""
    with engine.begin() as conn:
        exists = conn.execute(select(memories.c.id).where(memories.c.source == "seed")).first()
        if exists:
            return
        conn.execute(insert(memories).values(
            id=new_id("mem"), scope="lead:L-07", kind="outreach",
            content=dumps({"summary": "Sent intro email about faster list building for her new SDR team.",
                           "contacted_at": time.time() - 10 * 86400}),
            source="seed", created_at=time.time() - 10 * 86400,
        ))


def reset_demo() -> None:
    with engine.begin() as conn:
        for table in (spans, drafts, runs, eval_runs):
            conn.execute(delete(table))
        conn.execute(delete(memories).where(memories.c.source != "seed"))


__all__ = [
    "engine", "runs", "spans", "drafts", "memories", "eval_runs", "new_id", "dumps", "loads", "row",
    "init_db", "reset_demo", "select", "insert", "update", "delete",
]
