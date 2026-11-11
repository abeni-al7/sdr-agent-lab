"""A small tracer: every LLM call, tool call, guardrail check and memory read becomes a span row.

Spans are written as soon as they finish, so the UI can show a run live while it is still going.
The same span model maps 1:1 onto Langfuse / OpenTelemetry (trace = run, observation = span),
so an exporter can be added later without touching the agent.
"""
import time
import traceback
from contextlib import contextmanager

from .. import db

MAX_FIELD = 20_000  # keep traces readable and the database small


def _clip(value):
    text = db.dumps(value)
    if len(text) <= MAX_FIELD:
        return text
    return db.dumps({"truncated": True, "preview": text[:MAX_FIELD]})


class Span:
    def __init__(self, kind: str, name: str, input_):
        self.kind, self.name, self.input = kind, name, input_
        self.output = None
        self.status = "ok"
        self.flags: list = []
        self.tokens_in = 0
        self.tokens_out = 0


class Tracer:
    def __init__(self, run_id: str):
        self.run_id = run_id
        self.seq = 0
        self.tokens_in = 0
        self.tokens_out = 0

    @contextmanager
    def span(self, kind: str, name: str, input_=None):
        span = Span(kind, name, input_)
        started = time.time()
        try:
            yield span
        except Exception as exc:  # recorded, then re-raised to the agent loop
            span.status = "error"
            span.output = {"error": f"{type(exc).__name__}: {exc}", "trace": traceback.format_exc(limit=3)}
            raise
        finally:
            self.seq += 1
            self.tokens_in += span.tokens_in
            self.tokens_out += span.tokens_out
            with db.engine.begin() as conn:
                conn.execute(db.insert(db.spans).values(
                    id=db.new_id("spn"), run_id=self.run_id, seq=self.seq, kind=kind, name=span.name,
                    status=span.status, input=_clip(span.input), output=_clip(span.output),
                    flags=db.dumps(span.flags), started_at=started,
                    duration_ms=round((time.time() - started) * 1000, 1),
                    tokens_in=span.tokens_in, tokens_out=span.tokens_out,
                ))
