"""HTTP API + static console. Runs execute in background threads; the UI polls the trace."""
import threading
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import db
from .agent import memory
from .agent.loop import create_run, execute_run, get_llm, review_draft
from .config import BASE_DIR, COMPANIES, LEADS, SELLER, settings
from .evals.runner import CASES, run_suite


@asynccontextmanager
async def lifespan(_app):
    db.init_db()
    yield


app = FastAPI(title="SDR Agent Lab", lifespan=lifespan)
_hits: dict[str, deque] = defaultdict(deque)
_eval_lock = threading.Lock()


def _limit(key: str, limit: int, window: int = 3600):
    now, q = time.time(), _hits[key]
    while q and q[0] < now - window:
        q.popleft()
    if len(q) >= limit:
        raise HTTPException(429, f"Rate limit: {limit} per hour. This demo protects its free LLM quota.")
    q.append(now)


def _ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    return fwd.split(",")[0].strip() or (request.client.host if request.client else "local")


def _background(fn, *args):
    threading.Thread(target=fn, args=args, daemon=True).start()


# ---------------------------------------------------------------- meta

@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/meta")
def meta():
    llm = get_llm()
    return {"provider": llm.name, "model": llm.model, "seller": SELLER["name"], "icp": SELLER["icp"],
            "max_steps": settings.max_steps, "max_tokens_per_run": settings.max_tokens_per_run,
            "recent_contact_days": settings.recent_contact_days, "cases": len(CASES)}


# ---------------------------------------------------------------- leads and runs

@app.get("/api/leads")
def leads():
    with db.engine.connect() as conn:
        latest = {}
        for r in conn.execute(db.select(db.runs).where(db.runs.c.eval_batch.is_(None)).order_by(db.runs.c.started_at)):
            latest[r.lead_id] = {"run_id": r.id, "status": r.status, "decision": r.decision}
    out = []
    for lead in LEADS.values():
        c = COMPANIES[lead["domain"]]
        out.append({**lead, "industry": c["industry"], "employees": c["employees"], "hq": c["hq"],
                    "latest": latest.get(lead["id"])})
    return out


class RunIn(BaseModel):
    lead_id: str


@app.post("/api/runs")
def start_run(body: RunIn, request: Request):
    if body.lead_id not in LEADS:
        raise HTTPException(404, "unknown lead")
    _limit(f"run:{_ip(request)}", settings.runs_per_hour_per_ip)
    llm = get_llm()
    run_id = create_run(body.lead_id, llm=llm)
    _background(execute_run, run_id, llm)
    return {"run_id": run_id}


@app.get("/api/runs")
def list_runs(limit: int = 40, include_evals: bool = False):
    q = db.select(db.runs).order_by(db.runs.c.started_at.desc()).limit(min(limit, 200))
    if not include_evals:
        q = q.where(db.runs.c.eval_batch.is_(None))
    with db.engine.connect() as conn:
        return [{**db.row(r), "lead": LEADS[r.lead_id]} for r in conn.execute(q)]


@app.get("/api/runs/{run_id}")
def get_run(run_id: str):
    with db.engine.connect() as conn:
        r = conn.execute(db.select(db.runs).where(db.runs.c.id == run_id)).first()
        if not r:
            raise HTTPException(404, "run not found")
        spans = [db.row(s) for s in conn.execute(
            db.select(db.spans).where(db.spans.c.run_id == run_id).order_by(db.spans.c.seq))]
        d = conn.execute(db.select(db.drafts).where(db.drafts.c.run_id == run_id)).first()
    return {"run": db.row(r), "lead": LEADS[r.lead_id], "company": COMPANIES[LEADS[r.lead_id]["domain"]],
            "spans": spans, "draft": db.row(d) if d else None}


# ---------------------------------------------------------------- human review

@app.get("/api/drafts")
def list_drafts(status: str = "pending"):
    q = db.select(db.drafts).order_by(db.drafts.c.created_at.desc())
    if status != "all":
        q = q.where(db.drafts.c.status == status)
    else:
        q = q.where(db.drafts.c.status != "eval")
    with db.engine.connect() as conn:
        return [{**db.row(d), "lead": LEADS[d.lead_id]} for d in conn.execute(q)]


class ReviewIn(BaseModel):
    approve: bool
    note: str = ""
    apply_globally: bool = False


@app.post("/api/drafts/{draft_id}/review")
def review(draft_id: str, body: ReviewIn):
    try:
        return review_draft(draft_id, body.approve, body.note, body.apply_globally)
    except ValueError as exc:
        raise HTTPException(409, str(exc))


class CheckIn(BaseModel):
    lead_id: str
    subject: str
    body: str
    facts: list[dict] = []


@app.post("/api/guardrails/check")
def check_guardrails(body: CheckIn):
    """Red-team panel: run the real draft checks on any email, using the sources a run would have read."""
    from .agent import guardrails, tools as t

    if body.lead_id not in LEADS:
        raise HTTPException(404, "unknown lead")
    ctx = t.RunContext(run_id="redteam", lead_id=body.lead_id, eval_mode=True)
    ctx.sources["seller:profile"] = SELLER["profile"]
    t.recall_memory(ctx, body.lead_id)
    t.get_lead(ctx, body.lead_id)
    t.read_company_page(ctx, ctx.domain)
    t.search_company_signals(ctx, ctx.domain)
    failures = guardrails.check_draft(
        subject=body.subject, body=body.body, facts=body.facts, sources=ctx.sources, findings=ctx.findings,
        seller_domain_words={SELLER["name"].lower()}, days_since_contact=memory.days_since_last_contact(ctx.memory, body.lead_id),
        recent_days=settings.recent_contact_days)
    return {"passed": not failures, "failures": failures, "sources": sorted(ctx.sources), "injections_found": len(ctx.findings)}


@app.get("/api/memory")
def get_memory():
    return memory.list_all()


# ---------------------------------------------------------------- evals

@app.post("/api/evals")
def start_eval(request: Request):
    _limit("evals", settings.evals_per_hour)
    if not _eval_lock.acquire(blocking=False):
        raise HTTPException(409, "An evaluation is already running.")
    eval_id = db.new_id("evl")
    llm = get_llm()
    with db.engine.begin() as conn:
        conn.execute(db.insert(db.eval_runs).values(id=eval_id, status="running", provider=llm.name,
                                                    model=llm.model, started_at=time.time()))

    def work():
        try:
            run_suite(eval_id)
        finally:
            _eval_lock.release()

    _background(work)
    return {"eval_id": eval_id}


@app.get("/api/evals")
def list_evals():
    with db.engine.connect() as conn:
        rows = conn.execute(db.select(db.eval_runs).order_by(db.eval_runs.c.started_at.desc()).limit(20))
        return [db.row(r) for r in rows]


@app.get("/api/evals/{eval_id}")
def get_eval(eval_id: str):
    with db.engine.connect() as conn:
        r = conn.execute(db.select(db.eval_runs).where(db.eval_runs.c.id == eval_id)).first()
    if not r:
        raise HTTPException(404, "eval not found")
    return {**db.row(r), "cases": CASES}


@app.post("/api/reset")
def reset(request: Request):
    if settings.admin_token and request.headers.get("x-admin-token") != settings.admin_token:
        raise HTTPException(403, "admin token required")
    db.reset_demo()
    return {"ok": True}


# ---------------------------------------------------------------- console

app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")


@app.get("/")
def index():
    return FileResponse(BASE_DIR / "static" / "index.html")
