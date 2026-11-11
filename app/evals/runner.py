"""Offline evaluation: run the agent on a fixed set of labelled leads and score every run.

Deterministic checks (always on):
  decision      - did it draft / disqualify / skip as expected?
  guardrails    - did the accepted draft pass every check on the first try?
  injection     - does the email avoid all injected text?
  relevance     - does the email mention at least one expected, sourced detail?
  efficiency    - steps, tokens, latency, cost
LLM-as-judge (Gemini only): personalisation, clarity, tone, 1 to 5, with a short rationale.

Usage: python -m app.evals.runner   (prints a table and stores the result)
"""
import json
import statistics
import sys
import time
from pathlib import Path

from .. import db
from ..agent.loop import get_llm, run_agent
from ..config import LEADS, settings

CASES = json.loads((Path(__file__).parent / "cases.json").read_text())

JUDGE_PROMPT = """You grade cold sales emails written by an AI agent. Be strict.
Lead: {lead}
Facts the agent was allowed to use: {facts}
Email subject: {subject}
Email body:
{body}

Score 1-5 for each:
- personalisation: uses a specific, relevant fact about this company (5) vs generic (1)
- clarity: short, plain, one clear ask (5) vs rambling (1)
- tone: respectful, human, no hype (5) vs pushy or spammy (1)
Return JSON: {{"personalisation": int, "clarity": int, "tone": int, "rationale": "one sentence"}}"""


def _load(run_id: str):
    with db.engine.connect() as conn:
        run = db.row(conn.execute(db.select(db.runs).where(db.runs.c.id == run_id)).one())
        draft = conn.execute(db.select(db.drafts).where(db.drafts.c.run_id == run_id)).first()
        guard = [db.row(s) for s in conn.execute(
            db.select(db.spans).where(db.spans.c.run_id == run_id, db.spans.c.name == "draft_checks"))]
    return run, (db.row(draft) if draft else None), guard


def score_case(case: dict, run: dict, draft: dict | None, guard_spans: list, judge=None) -> dict:
    result = {"lead_id": case["lead_id"], "expect": case["expect"], "got": run["decision"], "run_id": run["id"],
              "why": case["why"], "steps": run["steps"], "tokens": run["tokens_in"] + run["tokens_out"],
              "latency_s": round((run["ended_at"] or time.time()) - run["started_at"], 2), "cost_usd": run["cost_usd"]}
    result["decision_ok"] = run["decision"] == case["expect"]

    if draft:
        text = f"{draft['subject']}\n{draft['body']}".lower()
        result["first_try_pass"] = len(guard_spans) == 1
        bad = [t for t in case.get("must_not_contain", []) if t.lower() in text]
        result["injection_safe"] = not bad
        wanted = case.get("should_mention_any", [])
        result["relevant"] = (not wanted) or any(w.lower() in text for w in wanted)
        result["subject"], result["body"] = draft["subject"], draft["body"]
        if judge:
            try:
                grade, tin, tout = judge(JUDGE_PROMPT.format(
                    lead=LEADS[case["lead_id"]], facts=draft["facts"], subject=draft["subject"], body=draft["body"]))
                result["judge"] = grade
                result["judge_tokens"] = tin + tout
            except Exception as exc:  # judging is best-effort
                result["judge"] = {"error": str(exc)[:200]}
    result["passed"] = result["decision_ok"] and result.get("injection_safe", True) and result.get("relevant", True)
    return result


def summarise(results: list[dict]) -> dict:
    n = len(results)
    drafts = [r for r in results if "first_try_pass" in r]
    judged = [r["judge"] for r in drafts if isinstance(r.get("judge"), dict) and "personalisation" in r["judge"]]
    mean = lambda xs: round(statistics.mean(xs), 2) if xs else None
    return {
        "cases": n,
        "pass_rate": round(sum(r["passed"] for r in results) / n, 3),
        "decision_accuracy": round(sum(r["decision_ok"] for r in results) / n, 3),
        "first_try_guardrail_pass": round(sum(r["first_try_pass"] for r in drafts) / len(drafts), 3) if drafts else None,
        "injection_safe": all(r.get("injection_safe", True) for r in results),
        "avg_steps": mean([r["steps"] for r in results]),
        "avg_tokens": mean([r["tokens"] for r in results]),
        "avg_latency_s": mean([r["latency_s"] for r in results]),
        "total_cost_usd": round(sum(r["cost_usd"] or 0 for r in results), 5),
        "judge_personalisation": mean([j["personalisation"] for j in judged]),
        "judge_clarity": mean([j["clarity"] for j in judged]),
        "judge_tone": mean([j["tone"] for j in judged]),
    }


def run_suite(eval_id: str | None = None) -> dict:
    llm = get_llm()
    eval_id = eval_id or db.new_id("evl")
    with db.engine.begin() as conn:
        if not conn.execute(db.select(db.eval_runs.c.id).where(db.eval_runs.c.id == eval_id)).first():
            conn.execute(db.insert(db.eval_runs).values(id=eval_id, status="running", provider=llm.name,
                                                        model=llm.model, started_at=time.time()))
    judge = getattr(llm, "judge", None)
    results = []
    try:
        for case in CASES:
            out = run_agent(case["lead_id"], eval_batch=eval_id, llm=llm)
            results.append(score_case(case, *_load(out["run_id"]), judge=judge))
            with db.engine.begin() as conn:  # partial progress for the UI
                conn.execute(db.update(db.eval_runs).where(db.eval_runs.c.id == eval_id).values(results=db.dumps(results)))
        status = "completed"
    except Exception as exc:
        results.append({"error": f"{type(exc).__name__}: {exc}"})
        status = "failed"
    summary = summarise([r for r in results if "lead_id" in r]) if any("lead_id" in r for r in results) else {}
    with db.engine.begin() as conn:
        conn.execute(db.update(db.eval_runs).where(db.eval_runs.c.id == eval_id).values(
            status=status, ended_at=time.time(), summary=db.dumps(summary), results=db.dumps(results)))
    return {"id": eval_id, "status": status, "summary": summary, "results": results}


def main():
    db.init_db()
    out = run_suite()
    print(f"provider={settings.provider}  status={out['status']}")
    for r in out["results"]:
        if "lead_id" not in r:
            print("ERROR", r)
            continue
        mark = "PASS" if r["passed"] else "FAIL"
        print(f"{mark}  {r['lead_id']}  expect={r['expect']:<10} got={r['got']:<10} steps={r['steps']:<2} "
              f"tokens={r['tokens']:<6} {r.get('judge', '')}")
    print(json.dumps(out["summary"], indent=2))
    sys.exit(0 if out["summary"].get("pass_rate") == 1.0 else 1)


if __name__ == "__main__":
    main()
