"""The agent loop: model -> tool calls -> results -> model ... until a terminal tool or a budget stops it.

Budgets (steps, tokens, draft attempts) and permissions live here and in the tools, not in the prompt.
Every step is traced.
"""
import time

from .. import db
from ..config import LEADS, SELLER, settings
from ..llm.base import LLMError
from ..observability.tracer import Tracer
from . import memory, tools
from .prompts import SYSTEM_PROMPT, task


def get_llm():
    if settings.provider == "gemini":
        from ..llm.gemini import GeminiLLM
        return GeminiLLM()
    from ..llm.stub import StubLLM
    return StubLLM()


def _cost(tokens_in: int, tokens_out: int, provider: str) -> float:
    if provider != "gemini":
        return 0.0
    return round(tokens_in / 1e6 * settings.price_in_per_m + tokens_out / 1e6 * settings.price_out_per_m, 6)


def create_run(lead_id: str, eval_batch: str | None = None, llm=None) -> str:
    if lead_id not in LEADS:
        raise ValueError(f"unknown lead {lead_id}")
    llm = llm or get_llm()
    run_id = db.new_id("run")
    with db.engine.begin() as conn:
        conn.execute(db.insert(db.runs).values(
            id=run_id, lead_id=lead_id, status="running", provider=llm.name, model=llm.model,
            eval_batch=eval_batch, started_at=time.time(), steps=0, tokens_in=0, tokens_out=0, cost_usd=0.0,
        ))
    return run_id


def execute_run(run_id: str, llm=None) -> dict:
    llm = llm or get_llm()
    with db.engine.connect() as conn:
        run = db.row(conn.execute(db.select(db.runs).where(db.runs.c.id == run_id)).one())
    eval_mode = bool(run["eval_batch"])
    ctx = tools.RunContext(run_id=run_id, lead_id=run["lead_id"], eval_mode=eval_mode)
    ctx.sources["seller:profile"] = SELLER["profile"]
    tracer = Tracer(run_id)
    history = [{"role": "user", "text": task(ctx.lead_id)}]
    declarations = tools.declarations()
    status, error, steps = "failed", None, 0

    try:
        for steps in range(1, settings.max_steps + 1):
            if tracer.tokens_in + tracer.tokens_out > settings.max_tokens_per_run:
                with tracer.span("guard", "token_budget", {"limit": settings.max_tokens_per_run}) as s:
                    s.status, s.output = "blocked", {"used": tracer.tokens_in + tracer.tokens_out}
                error = "token budget exceeded"
                break

            with tracer.span("llm", llm.model, {"step": steps, "last_message": history[-1]}) as s:
                resp = llm.complete(SYSTEM_PROMPT, history, declarations)
                s.tokens_in, s.tokens_out = resp.tokens_in, resp.tokens_out
                s.name = resp.model or llm.model  # the model that actually answered (fallbacks)
                s.output = {"tool_calls": resp.calls, "text": resp.text, "finish_reason": resp.finish_reason}
            history.append({"role": "model", "text": resp.text, "calls": resp.calls, "raw": resp.raw})

            if not resp.calls:
                history.append({"role": "user", "text": "Act only by calling one of the tools."})
                continue

            results = []
            for call in resp.calls:
                name, args = call["name"], call.get("args") or {}
                kind = "memory" if name == "recall_memory" else "tool"
                with tracer.span(kind, name, args) as s:
                    try:
                        out = tools.execute(ctx, name, args)
                    except tools.ToolError as exc:
                        out = {"error": str(exc)}
                        s.status = "blocked"
                        s.flags.append("permission" if "scope" in str(exc) else "bad_call")
                    s.output = out
                    if name == "read_company_page" and out.get("security", {}).get("injections_removed"):
                        s.status = "flagged"
                        s.flags.append("prompt_injection_removed")
                if name == "read_company_page" and ctx.findings:
                    with tracer.span("guard", "injection_scan", {"source": f"page:{ctx.domain}"}) as g:
                        g.status, g.flags = "flagged", ["prompt_injection"]
                        g.output = {"findings": ctx.findings, "action": "removed before the model saw the page"}
                if name == "submit_draft":
                    with tracer.span("guard", "draft_checks", {"attempt": ctx.draft_attempts}) as g:
                        passed = out.get("accepted", False)
                        g.status = "ok" if passed else "blocked"
                        g.output = {"passed": passed, "failures": out.get("failures", [])}
                results.append({"name": name, "response": out})
                if ctx.final:
                    break
            history.append({"role": "tool", "results": results})
            if ctx.final:
                status = "blocked" if ctx.final["decision"] == "blocked" else "completed"
                break
        else:
            error = f"step budget of {settings.max_steps} reached without a decision"
    except LLMError as exc:
        error = str(exc)
    except Exception as exc:  # never leave a run stuck in 'running'
        error = f"{type(exc).__name__}: {exc}"

    # Memory is not written here: a draft becomes "outreach" only when a human approves it.
    final = ctx.final or {"decision": "error", "summary": error or "no decision"}
    with db.engine.begin() as conn:
        conn.execute(db.update(db.runs).where(db.runs.c.id == run_id).values(
            status=status if ctx.final else "failed", decision=final["decision"], summary=final["summary"],
            ended_at=time.time(), steps=steps, tokens_in=tracer.tokens_in, tokens_out=tracer.tokens_out,
            cost_usd=_cost(tracer.tokens_in, tracer.tokens_out, llm.name), error=error,
        ))
    return {"run_id": run_id, **final}


def run_agent(lead_id: str, eval_batch: str | None = None, llm=None) -> dict:
    llm = llm or get_llm()
    return execute_run(create_run(lead_id, eval_batch, llm), llm)


def review_draft(draft_id: str, approve: bool, note: str = "", apply_globally: bool = False) -> dict:
    """Human-in-the-loop. Approving records outreach memory; rejecting with a note records a lesson."""
    with db.engine.begin() as conn:
        d = db.row(conn.execute(db.select(db.drafts).where(db.drafts.c.id == draft_id)).one())
        if d["status"] != "pending":
            raise ValueError(f"draft is already {d['status']}")
        conn.execute(db.update(db.drafts).where(db.drafts.c.id == draft_id).values(
            status="approved" if approve else "rejected", reviewer_note=note, reviewed_at=time.time()))
    lead = LEADS[d["lead_id"]]
    if approve:
        memory.remember(f"lead:{lead['id']}", "outreach",
                        {"summary": f"Approved email: {d['subject']}", "contacted_at": time.time(), "draft_id": draft_id},
                        source=d["run_id"])
    elif note.strip():
        scope = "global" if apply_globally else f"company:{lead['domain']}"
        memory.remember(scope, "lesson", {"text": note.strip(), "from_draft": draft_id}, source="review")
    return {"ok": True}
