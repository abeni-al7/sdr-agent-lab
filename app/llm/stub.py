"""Deterministic offline model. It follows the same tool-calling protocol as Gemini, so the whole
system (loop, guardrails, tracing, evals, UI) runs and is testable with no API key and no cost.
It is a scripted policy, not a language model, and the UI labels it clearly."""
import os
import re
import time

from ..config import COMPANIES, LEADS, SELLER
from .base import LLMResponse


def _results(history: list[dict]) -> dict:
    """Latest response for each tool name."""
    out = {}
    for h in history:
        if h["role"] == "tool":
            for r in h["results"]:
                out[r["name"]] = r["response"]
    return out


def _tokens(*texts) -> int:
    return max(1, sum(len(str(t)) for t in texts) // 4)


def _banned_terms(lessons: list[str]) -> list[str]:
    terms = []
    for lesson in lessons:
        for m in re.finditer(r"(?:don't|do not|never|avoid) (?:mention|talk about|use)\s+([\w\s-]+)", lesson, re.I):
            terms.extend(w.lower() for w in m.group(1).split() if len(w) > 3)
    return terms


def _compose(lead_id: str, res: dict, short: bool) -> dict:
    lead = LEADS[lead_id]
    first = lead["name"].split()[0]
    company = COMPANIES[lead["domain"]]
    banned = _banned_terms(res.get("recall_memory", {}).get("reviewer_lessons", []))
    signals = [s for s in res.get("search_company_signals", {}).get("signals", [])
               if not any(t in s["signal"].lower() for t in banned)]

    if signals:
        hook = signals[0]["signal"].split(" (")[0]
        hook_text = hook.replace(company["name"] + " ", "", 1)
        opener = f"I saw that {company['name']} {hook_text}."
        fact = {"claim": hook, "source_id": signals[0]["source_id"]}
    else:
        page = res.get("read_company_page", {}).get("untrusted_content", "")
        sentence = next((s.strip() for s in page.split(".") if "outbound" in s or "prospect" in s), page.split(".")[0])
        opener = f"I read that {sentence[0].lower() + sentence[1:]}."
        fact = {"claim": sentence, "source_id": f"page:{lead['domain']}"}

    value = "ListForge turns Sales Navigator searches into clean, verified prospect lists, so reps spend their time selling."
    if not short:
        value += " Teams that use it save about 6 hours per rep each week on list building."
    body = (f"Hi {first},\n\n{opener} {value}\n\n"
            f"Would a 15-minute walkthrough next week be useful?\n\nBest,\n{SELLER['sender']}")
    return {
        "subject": f"Cleaner prospect lists for {company['name']}",
        "body": body,
        "facts": [fact, {"claim": "save about 6 hours per rep each week", "source_id": "seller:profile"}],
    }


class StubLLM:
    name = "stub"
    model = "stub-policy-v1"

    def complete(self, system: str, history: list[dict], tools: list[dict]) -> LLMResponse:
        # Optional fake "thinking time" so demos can show the trace filling in live.
        time.sleep(int(os.getenv("STUB_DELAY_MS", "0")) / 1000)
        lead_id = re.search(r"L-\d+", history[0]["text"]).group(0)
        domain = LEADS[lead_id]["domain"]
        res = _results(history)

        def call(name, **args):
            return LLMResponse(calls=[{"name": name, "args": args}], model=self.model,
                               tokens_in=_tokens(system, history), tokens_out=_tokens(name, args))

        if "recall_memory" not in res:
            return call("recall_memory", lead_id=lead_id)
        if res["recall_memory"].get("recently_contacted"):
            days = res["recall_memory"]["days_since_last_contact"]
            return call("skip_lead", reason=f"Lead was contacted {days} days ago; the rule is 30 days.")
        if "get_lead" not in res:
            return call("get_lead", lead_id=lead_id)
        if "score_icp" not in res:
            return call("score_icp", lead_id=lead_id)
        if not res["score_icp"]["qualified"]:
            misses = [r for r in res["score_icp"]["reasons"] if r.startswith("+0")]
            return call("disqualify_lead", reason=f"ICP score {res['score_icp']['score']} is below "
                                                  f"{res['score_icp']['threshold']}: " + "; ".join(m[3:] for m in misses))
        if "read_company_page" not in res:
            return call("read_company_page", domain=domain)
        if "search_company_signals" not in res:
            return call("search_company_signals", domain=domain)
        retry = "submit_draft" in res and not res["submit_draft"].get("accepted")
        return call("submit_draft", **_compose(lead_id, res, short=retry))
