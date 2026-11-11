"""Provider-neutral message format used by the agent loop.

history entries:
  {"role": "user",  "text": str}
  {"role": "model", "text": str | None, "calls": [{"name", "args"}], "raw": provider payload}
  {"role": "tool",  "results": [{"name", "response": dict}]}
"""
from dataclasses import dataclass, field


@dataclass
class LLMResponse:
    calls: list = field(default_factory=list)   # [{"name": str, "args": dict}]
    text: str | None = None
    raw: dict | None = None                     # provider content to replay verbatim next turn
    tokens_in: int = 0
    tokens_out: int = 0
    model: str = ""
    finish_reason: str | None = None


class LLMError(Exception):
    pass
