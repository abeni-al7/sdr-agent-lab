"""Deterministic guardrails. They run in code, outside the model, so a prompt cannot switch them off.

1. scan_untrusted: finds prompt-injection text in data fetched from the outside world and removes it
   before the model sees it.
2. check_draft: validates an email before it is accepted (grounding, length, spam words,
   links, injection leakage, recent contact).
"""
import re

INJECTION_PATTERNS = [
    r"ignore (all |any )?(the )?(previous|prior|above) (instructions|prompts|rules)",
    r"disregard (all |any )?(previous|prior|your) (instructions|rules)",
    r"you are now (a|an|the) ",
    r"(reveal|print|show) (your|the) (system )?prompt",
    r"note for (ai|llm|language model)s?( assistants?)?",
    r"(add|insert|include) this (line|text|sentence) to every",
]
_INJ = re.compile("|".join(INJECTION_PATTERNS), re.IGNORECASE)
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_SENTENCE = re.compile(r"[^.!?]*[.!?]")

BANNED_PHRASES = [
    "guarantee", "risk-free", "act now", "100%", "limited time", "i hope this email finds you well",
    "click here", "free money", "bank details", "dear sir",
]
# Everyday words that may appear in both an injected sentence and a normal email.
COMMON_WORDS = {
    "email", "emails", "every", "write", "reply", "please", "thanks", "would", "could", "about", "their",
    "there", "which", "these", "those", "after", "before", "other", "teams", "think", "quick", "people",
    "assistants", "assistant", "instructions", "previous", "ignore", "should", "while", "where",
}
MAX_WORDS = 130
MAX_SUBJECT = 70
_URL = re.compile(r"(https?://\S+|www\.\S+|\b[\w-]+\.(?:com|io|net|org|example|ai)\b)", re.IGNORECASE)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
_WORD = re.compile(r"[a-zA-Z]{5,}")


def scan_untrusted(text: str) -> tuple[str, list[dict]]:
    """Return (clean_text, findings). Hidden HTML comments and sentences that look like
    instructions to an AI are removed and reported."""
    findings, clean = [], text

    for m in _HTML_COMMENT.finditer(text):
        segment = m.group(0)
        findings.append({"type": "hidden_html_comment", "pattern_hit": bool(_INJ.search(segment)), "segment": segment})
        clean = clean.replace(segment, " [removed: hidden comment] ")

    for sentence in _SENTENCE.findall(clean):
        if _INJ.search(sentence):
            findings.append({"type": "instruction_like_sentence", "pattern_hit": True, "segment": sentence.strip()})
            clean = clean.replace(sentence, " [removed: possible prompt injection] ")

    return re.sub(r"\s{2,}", " ", clean).strip(), findings


def _content_words(text: str) -> set[str]:
    return {w.lower() for w in _WORD.findall(text)}


def check_draft(*, subject: str, body: str, facts: list, sources: dict, findings: list,
                seller_domain_words: set[str], days_since_contact, recent_days: int) -> list[dict]:
    """Return a list of failed checks. An empty list means the draft may go to human review."""
    failures: list[dict] = []
    text = f"{subject}\n{body}"
    lower = text.lower()

    words = len(body.split())
    if words > MAX_WORDS:
        failures.append({"check": "length", "detail": f"body has {words} words, limit is {MAX_WORDS}"})
    if len(subject) > MAX_SUBJECT:
        failures.append({"check": "length", "detail": f"subject has {len(subject)} characters, limit is {MAX_SUBJECT}"})

    hits = [p for p in BANNED_PHRASES if p in lower]
    if hits:
        failures.append({"check": "spam_words", "detail": f"banned phrases: {', '.join(hits)}"})

    links = [u for u in _URL.findall(text) if not any(s in u.lower() for s in seller_domain_words)]
    if links or _EMAIL.search(text):
        failures.append({"check": "links", "detail": f"links or emails are not allowed in a first email: {links or 'email address'}"})

    # Grounding 1: every fact cites a source the agent actually read in this run.
    if not facts:
        failures.append({"check": "grounding", "detail": "no facts cited; personalisation must come from sources"})
    for fact in facts or []:
        sid = (fact or {}).get("source_id", "")
        claim = (fact or {}).get("claim", "")
        if sid not in sources:
            failures.append({"check": "grounding", "detail": f"source '{sid}' was never read in this run"})
            continue
        if not (_content_words(claim) & _content_words(sources[sid])):
            failures.append({"check": "grounding", "detail": f"claim '{claim[:60]}' shares no key words with {sid}"})

    # Grounding 2: every number in the email must appear in some source (no invented metrics).
    source_numbers = set()
    for s in sources.values():
        source_numbers.update(n.replace(",", "") for n in _NUMBER.findall(s))
    invented = sorted({n.replace(",", "") for n in _NUMBER.findall(text)} - source_numbers)
    if invented:
        failures.append({"check": "grounding", "detail": f"numbers not found in any source: {', '.join(invented)}"})

    # Injection leakage: distinctive words from removed injected text must not reach the email.
    clean_vocab = set().union(*(_content_words(s) for s in sources.values())) if sources else set()
    for f in findings:
        leaked = sorted((_content_words(f["segment"]) - clean_vocab - COMMON_WORDS) & _content_words(text))
        if len(leaked) >= 2:
            failures.append({"check": "injection_leak", "detail": f"email repeats injected text: {', '.join(leaked[:5])}"})

    if days_since_contact is not None and days_since_contact < recent_days:
        failures.append({"check": "recent_contact", "detail": f"lead was contacted {days_since_contact} days ago"})

    return failures
