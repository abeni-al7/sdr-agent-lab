"""Long-term memory: what we already sent (outreach) and what reviewers taught the agent (lessons).

Scopes: `lead:<id>` (one person), `company:<domain>` (everyone at a company), `global` (all leads).
Eval runs read only seed memories and never write, so evaluations stay reproducible.
"""
import time

from .. import db


def recall(lead_id: str, domain: str, seed_only: bool = False) -> dict:
    scopes = [f"lead:{lead_id}", f"company:{domain}", "global"]
    query = db.select(db.memories).where(db.memories.c.scope.in_(scopes)).order_by(db.memories.c.created_at)
    if seed_only:
        query = query.where(db.memories.c.source == "seed")
    with db.engine.connect() as conn:
        rows = [db.row(r) for r in conn.execute(query)]

    outreach, lessons = [], []
    for r in rows:
        content = db.loads(r["content"]) or {}
        item = {"scope": r["scope"], "created_at": r["created_at"], **(content if isinstance(content, dict) else {"text": content})}
        (outreach if r["kind"] == "outreach" else lessons).append(item)
    return {"outreach": outreach, "lessons": lessons}


def days_since_last_contact(memory: dict, lead_id: str):
    times = [o.get("contacted_at") for o in memory["outreach"] if o["scope"] == f"lead:{lead_id}" and o.get("contacted_at")]
    if not times:
        return None
    return round((time.time() - max(times)) / 86400, 1)


def remember(scope: str, kind: str, content: dict, source: str) -> None:
    with db.engine.begin() as conn:
        conn.execute(db.insert(db.memories).values(
            id=db.new_id("mem"), scope=scope, kind=kind, content=db.dumps(content),
            source=source, created_at=time.time(),
        ))


def list_all() -> list:
    with db.engine.connect() as conn:
        rows = conn.execute(db.select(db.memories).order_by(db.memories.c.created_at.desc()))
        return [{**db.row(r), "content": db.loads(r.content)} for r in rows]
