# Product: SDR Agent Lab

**What it is:** a small, honest reference implementation of an agentic SDR (sales development) workflow.
For one lead at a time, an LLM agent recalls memory, qualifies the lead against an ideal customer profile,
researches the company with scoped tools, and writes a short first email that a human must approve.

**Who uses it:** an engineer or founder who wants to see, step by step, what the agent did, why, what it cost,
and whether its output is safe and good. Primary scene: a screen-shared walkthrough on a laptop, desktop width.

**Core jobs (Operate mode):**
1. Start a run on a lead and watch the trace fill in live.
2. Review drafts: approve (writes outreach memory) or reject with a lesson (writes lesson memory).
3. Run the evaluation suite and read the scorecard.
4. Inspect memory and the architecture.

**Truth constraints:** all leads and companies are fictional (`.example` domains). Nothing is ever sent.
The offline stub model is labelled as a scripted policy, never as an AI.

_Assumptions (inferred from the brief, not interviewed): desktop-first; English only; single operator, no auth._
