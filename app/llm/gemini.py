"""Minimal Gemini client (REST, function calling). No SDK, so every byte on the wire is visible in traces.

Resilience: a fallback chain of models (GEMINI_MODELS). On 429 quota, 5xx overload or a timeout the next
model is tried, up to two passes over the chain. Low thinking keeps latency and cost down for tool routing.
"""
import json
import time

import httpx

from ..config import settings
from .base import LLMError, LLMResponse

API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
RETRYABLE = {408, 429, 500, 502, 503, 504}


def _to_contents(history: list[dict]) -> list[dict]:
    contents = []
    for h in history:
        if h["role"] == "user":
            contents.append({"role": "user", "parts": [{"text": h["text"]}]})
        elif h["role"] == "model":
            # Replay the model turn exactly (it may carry thought signatures Gemini needs back).
            contents.append(h["raw"] or {"role": "model", "parts": [{"text": h.get("text") or ""}]})
        elif h["role"] == "tool":
            contents.append({"role": "user", "parts": [
                {"functionResponse": {"name": r["name"], "response": r["response"]}} for r in h["results"]
            ]})
    return contents


def _post(payload: dict, models: list[str]) -> tuple[dict, str, list[str]]:
    """Returns (response json, model used, notes about fallbacks)."""
    notes = []
    for attempt in range(2):
        for model in models:
            try:
                r = httpx.post(API.format(model=model), params={"key": settings.gemini_api_key},
                               json=payload, timeout=httpx.Timeout(45, connect=10))
            except httpx.TimeoutException:
                notes.append(f"{model}: timeout")
                continue
            except httpx.HTTPError as exc:
                notes.append(f"{model}: network error {exc}")
                continue
            if r.status_code == 200:
                return r.json(), model, notes
            notes.append(f"{model}: HTTP {r.status_code}")
            if r.status_code not in RETRYABLE and r.status_code != 404:
                raise LLMError(f"Gemini {model} HTTP {r.status_code}: {r.text[:300]}")
        time.sleep(2 * (attempt + 1))
    raise LLMError("all Gemini models failed: " + "; ".join(notes[-6:]))


class GeminiLLM:
    name = "gemini"

    def __init__(self):
        if not settings.gemini_api_key:
            raise LLMError("GEMINI_API_KEY is not set")
        self.models = settings.models
        self.model = self.models[0]

    def _config(self, **extra) -> dict:
        cfg = {"temperature": 0.3, **extra}
        if settings.gemini_thinking:
            cfg["thinkingConfig"] = {"thinkingLevel": settings.gemini_thinking}
        return cfg

    def complete(self, system: str, history: list[dict], tools: list[dict]) -> LLMResponse:
        payload = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": _to_contents(history),
            "tools": [{"functionDeclarations": tools}],
            # The agent must always act through a tool; ending the run is also a tool call.
            "toolConfig": {"functionCallingConfig": {"mode": "ANY"}},
            "generationConfig": self._config(),
        }
        data, used, notes = _post(payload, self.models)
        cands = data.get("candidates") or []
        if not cands:
            raise LLMError(f"no candidates returned: {json.dumps(data.get('promptFeedback', data))[:300]}")
        content = cands[0].get("content") or {"role": "model", "parts": []}
        content.setdefault("role", "model")
        parts = content.get("parts", [])
        calls = [{"name": p["functionCall"]["name"], "args": p["functionCall"].get("args", {})}
                 for p in parts if "functionCall" in p]
        text = "".join(p.get("text", "") for p in parts if "text" in p and not p.get("thought")) or None
        usage = data.get("usageMetadata", {})
        return LLMResponse(
            calls=calls, text=text, raw=content, model=data.get("modelVersion", used),
            tokens_in=usage.get("promptTokenCount", 0),
            tokens_out=usage.get("candidatesTokenCount", 0) + usage.get("thoughtsTokenCount", 0),
            finish_reason=cands[0].get("finishReason") + (f" (fallbacks: {'; '.join(notes)})" if notes else "")
            if cands[0].get("finishReason") else None,
        )

    def judge(self, prompt: str) -> tuple[dict, int, int]:
        """Single JSON-mode call used by the LLM-as-judge evaluator."""
        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": self._config(temperature=0, responseMimeType="application/json"),
        }
        data, _, _ = _post(payload, self.models)
        text = "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"] if not p.get("thought"))
        usage = data.get("usageMetadata", {})
        return json.loads(text), usage.get("promptTokenCount", 0), usage.get("candidatesTokenCount", 0)
