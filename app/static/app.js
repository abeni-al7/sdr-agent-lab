// SDR Agent Lab console. Plain ES modules, no build step. Every server string is escaped before render.
const view = document.getElementById("view");
const state = { meta: null, poll: null };

// ------------------------------------------------------------------ helpers
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtMs = (ms) => (ms == null ? "" : ms >= 1000 ? `${(ms / 1000).toFixed(2)}s` : `${Math.round(ms)}ms`);
const fmtTok = (n) => (n ? n.toLocaleString("en-US") : "0");
const fmtUsd = (n) => (n ? `$${n.toFixed(5)}` : "$0");
const ago = (t) => {
  const s = Math.max(0, Date.now() / 1000 - t);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} days ago`;
};

const ICON = {
  play: '<svg viewBox="0 0 16 16"><path d="M5 3.5v9l7-4.5z"/></svg>',
  check: '<svg viewBox="0 0 16 16"><path d="M3.5 8.5l3 3 6-7"/></svg>',
  x: '<svg viewBox="0 0 16 16"><path d="M4 4l8 8M12 4l-8 8"/></svg>',
  flag: '<svg viewBox="0 0 16 16"><path d="M4 14V2.5M4 3h7l-1.5 2.5L11 8H4"/></svg>',
  back: '<svg viewBox="0 0 16 16"><path d="M10 3L5 8l5 5"/></svg>',
  beaker: '<svg viewBox="0 0 16 16"><path d="M6 2h4M6.5 2v4.5L3 13h10L9.5 6.5V2"/></svg>',
};

async function api(path, opts = {}) {
  const res = await fetch(path, { headers: { "content-type": "application/json" }, ...opts });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail || `Request failed (${res.status})`);
  return body;
}

function toast(msg, err = false) {
  const t = document.createElement("div");
  t.className = `toast${err ? " err" : ""}`;
  t.setAttribute("role", "status");
  t.textContent = msg;
  document.body.append(t);
  setTimeout(() => t.remove(), 3600);
}

function mark(kind, label = kind) {
  const icon = { draft: ICON.check, approved: ICON.check, pass: ICON.check, ok: ICON.check, blocked: ICON.x, rejected: ICON.x, fail: ICON.x, error: ICON.x, failed: ICON.x, flagged: ICON.flag }[kind] || "";
  if (kind === "running") return `<span class="mark running"><span class="live-dot"></span>running</span>`;
  return `<span class="mark ${esc(kind)}">${icon}${esc(label)}</span>`;
}

function json(value) {
  const text = esc(JSON.stringify(value, null, 2));
  return text
    .replace(/(&quot;[^&]*?&quot;)(\s*:)/g, '<span class="k">$1</span>$2')
    .replace(/:\s(&quot;.*?&quot;)(,?)$/gm, ': <span class="s">$1</span>$2')
    .replace(/:\s(-?\d+\.?\d*)(,?)$/gm, ': <span class="n">$1</span>$2');
}

function stopPoll() {
  if (state.poll) clearTimeout(state.poll);
  state.poll = null;
}

// ------------------------------------------------------------------ header
async function loadMeta() {
  state.meta = await api("/api/meta");
  const el = document.getElementById("model");
  const stub = state.meta.provider === "stub";
  el.className = `model${stub ? " stub" : ""}`;
  el.innerHTML = `<span class="dot"></span><span class="full">${stub ? "offline stub policy (no LLM)" : "model"}</span> ${esc(state.meta.model)}`;
  el.title = stub ? "No GEMINI_API_KEY set: a scripted policy drives the same tools and guardrails." : "Live LLM";
}

async function refreshPending() {
  try {
    const pending = await api("/api/drafts?status=pending");
    const c = document.getElementById("pending-count");
    c.hidden = pending.length === 0;
    c.textContent = pending.length;
  } catch { /* header badge is best-effort */ }
}

// ------------------------------------------------------------------ leads
async function leadsView() {
  const leads = await api("/api/leads");
  view.innerHTML = `
    <div class="page-head">
      <div>
        <h1>Leads</h1>
        <p>Ten fictional prospects from Sales Navigator-style searches. Run the agent on one and watch every step it takes.
        It qualifies the lead, researches the company with scoped tools, and drafts an email only a human can approve.</p>
      </div>
    </div>
    <table class="ledger">
      <thead><tr><th>Lead</th><th>Company</th><th class="num hide-sm">Size</th><th>Last decision</th><th></th></tr></thead>
      <tbody>
        ${leads.map((l) => `
          <tr>
            <td class="who"><strong>${esc(l.name)}</strong><span>${esc(l.title)} · <span class="data">${esc(l.id)}</span></span></td>
            <td class="who"><strong>${esc(l.company)}</strong><span>${esc(l.industry)} · ${esc(l.hq)}</span></td>
            <td class="num hide-sm">${esc(l.employees.toLocaleString("en-US"))}</td>
            <td>${l.latest ? `<a href="#/run/${esc(l.latest.run_id)}">${mark(l.latest.status === "running" ? "running" : l.latest.decision || "error")}</a>` : mark("none", "not run")}</td>
            <td><button class="btn" data-run="${esc(l.id)}" aria-label="Run the agent on ${esc(l.name)}">${ICON.play}Run agent</button></td>
          </tr>`).join("")}
      </tbody>
    </table>`;
  view.querySelectorAll("[data-run]").forEach((b) => b.addEventListener("click", () => startRun(b.dataset.run, b)));
}

async function startRun(leadId, btn) {
  btn.disabled = true;
  try {
    const { run_id } = await api("/api/runs", { method: "POST", body: JSON.stringify({ lead_id: leadId }) });
    location.hash = `#/run/${run_id}`;
  } catch (e) {
    toast(e.message, true);
    btn.disabled = false;
  }
}

// ------------------------------------------------------------------ run trace
const open = new Set();

async function runView(id) {
  stopPoll();
  const data = await api(`/api/runs/${id}`);
  const { run, lead, company, spans, draft } = data;
  const live = run.status === "running";
  const maxDur = Math.max(1, ...spans.map((s) => s.duration_ms || 0));
  const latency = ((run.ended_at || Date.now() / 1000) - run.started_at) * 1000;

  view.innerHTML = `
    <a class="btn ghost" href="#/runs">${ICON.back}All runs</a>
    <div class="page-head" style="margin-top:18px">
      <div>
        <h1>${esc(lead.name)}, ${esc(company.name)}</h1>
        <p>${esc(lead.title)} · ${esc(company.industry)} · ${esc(company.employees)} people · <span class="data">${esc(run.id)}</span></p>
      </div>
      <div>${live ? mark("running") : mark(run.decision || "error")}</div>
    </div>
    <div class="run-meta" aria-label="Run totals">
      <span>steps <b>${run.steps || spans.filter((s) => s.kind === "llm").length}</b></span>
      <span>tokens <b>${fmtTok(run.tokens_in + run.tokens_out || spans.reduce((a, s) => a + s.tokens_in + s.tokens_out, 0))}</b></span>
      <span>cost <b>${fmtUsd(run.cost_usd)}</b></span>
      <span>wall time <b>${fmtMs(latency)}</b></span>
      <span>model <b>${esc(run.model)}</b></span>
    </div>
    <div class="run-grid">
      <section aria-label="Trace">
        <div class="section-label">Trace: every model call, tool call and guardrail, in order</div>
        <div class="trace">
          ${spans.map((s) => traceRow(s, maxDur)).join("")}
          ${live ? `<div class="skeleton"><span class="live-dot" style="display:inline-block;margin-right:8px"></span>agent is thinking…</div>` : ""}
          ${!spans.length && !live ? `<div class="skeleton">No spans recorded.</div>` : ""}
        </div>
      </section>
      <aside class="outcome" aria-label="Outcome">${outcome(run, draft, spans)}</aside>
    </div>`;

  view.querySelectorAll(".trace-row > button").forEach((b) => b.addEventListener("click", () => {
    const sid = b.dataset.span;
    open.has(sid) ? open.delete(sid) : open.add(sid);
    b.setAttribute("aria-expanded", open.has(sid));
    b.parentElement.querySelector(".trace-detail").hidden = !open.has(sid);
  }));
  if (live) state.poll = setTimeout(() => route(), 700);
  else refreshPending();
}

function traceRow(s, maxDur) {
  const isOpen = open.has(s.id);
  const flags = (s.flags || []).map((f) => `<span class="flag">${ICON.flag}${esc(f.replaceAll("_", " "))}</span>`).join("");
  const status = s.status !== "ok" ? mark(s.status) : "";
  const tag = s.kind === "llm" ? callSummary(s.output) : s.kind === "guard" ? guardSummary(s) : "";
  const pct = Math.max(1.5, ((s.duration_ms || 0) / maxDur) * 100);
  return `
    <div class="trace-row">
      <button type="button" data-span="${esc(s.id)}" aria-expanded="${isOpen}">
        <span class="seq">${s.seq}</span>
        <span class="kind ${esc(s.kind)}">${esc(s.kind)}</span>
        <span class="span-name">${esc(s.name)} ${status} ${flags} <span class="tag">${tag}</span></span>
        <span class="bar-track" aria-hidden="true"><span class="bar ${s.kind === "llm" ? "llm" : ""}" style="width:${pct}%;display:block"></span></span>
        <span class="dur">${fmtMs(s.duration_ms)}</span>
      </button>
      <div class="trace-detail" ${isOpen ? "" : "hidden"}>
        ${s.tokens_in || s.tokens_out ? `<div class="data muted">tokens in ${fmtTok(s.tokens_in)} · out ${fmtTok(s.tokens_out)}</div>` : ""}
        <h4>Input</h4><pre class="json">${json(s.input)}</pre>
        <h4>Output</h4><pre class="json">${json(s.output)}</pre>
      </div>
    </div>`;
}

const callSummary = (out) => esc((out?.tool_calls || []).map((c) => `→ ${c.name}`).join("  ") || (out?.text ? "text reply" : ""));
const guardSummary = (s) => {
  if (s.name === "draft_checks") return s.output?.passed ? "all checks passed" : `${(s.output?.failures || []).length} failed`;
  if (s.name === "injection_scan") return `${(s.output?.findings || []).length} removed`;
  return "";
};

function outcome(run, draft, spans) {
  if (run.status === "running") return `<div class="verdict"><h2>Working…</h2><p>The trace updates live as each step finishes.</p></div>`;
  const guards = spans.filter((s) => s.name === "draft_checks");
  const tries = guards.length ? `<p class="data muted">guardrail attempts: ${guards.map((g) => (g.output?.passed ? "pass" : "fail")).join(" → ")}</p>` : "";
  if (draft) {
    return `
      <div class="verdict"><h2>Draft waiting for review</h2>${tries}</div>
      <article class="letter" aria-label="Draft email">
        <div class="to">To ${esc(draft.lead_id)} · ${mark(draft.status)}</div>
        <div class="subject">${esc(draft.subject)}</div>
        <div class="body">${esc(draft.body)}</div>
      </article>
      <div><div class="section-label">Facts cited (each must be a source the agent read)</div>
        <ul class="facts">${(draft.facts || []).map((f) => `<li><code>${esc(f.source_id)}</code><span>${esc(f.claim)}</span></li>`).join("")}</ul></div>
      ${draft.status === "pending" ? `<a class="btn signal" href="#/review">Review this draft</a>` : ""}`;
  }
  const title = { disqualify: "Disqualified", skip: "Skipped", blocked: "Blocked by guardrails", error: "Run failed" }[run.decision] || "Finished";
  const lastGuard = guards.at(-1);
  return `
    <div class="verdict"><h2>${title}</h2><p>${esc(run.summary || run.error || "")}</p>${tries}</div>
    ${lastGuard && !lastGuard.output?.passed ? `<div><div class="section-label">Failed checks</div><ul class="facts">${lastGuard.output.failures.map((f) => `<li><code>${esc(f.check)}</code><span>${esc(f.detail)}</span></li>`).join("")}</ul></div>` : ""}`;
}

// ------------------------------------------------------------------ runs
async function runsView() {
  const runs = await api("/api/runs");
  view.innerHTML = `
    <div class="page-head"><div><h1>Runs</h1><p>Every agent run is a trace you can open. Eval runs are kept separate on the Evals page.</p></div></div>
    ${runs.length ? `<table class="ledger">
      <thead><tr><th>When</th><th>Lead</th><th>Decision</th><th class="num">Steps</th><th class="num hide-sm">Tokens</th><th class="num hide-sm">Cost</th><th class="num">Time</th></tr></thead>
      <tbody>${runs.map((r) => `
        <tr class="click" data-href="#/run/${esc(r.id)}" tabindex="0">
          <td class="muted">${ago(r.started_at)}</td>
          <td class="who"><strong>${esc(r.lead.name)}</strong><span>${esc(r.lead.company)}</span></td>
          <td>${mark(r.status === "running" ? "running" : r.decision || "error")}</td>
          <td class="num">${r.steps ?? ""}</td>
          <td class="num hide-sm">${fmtTok(r.tokens_in + r.tokens_out)}</td>
          <td class="num hide-sm">${fmtUsd(r.cost_usd)}</td>
          <td class="num">${r.ended_at ? fmtMs((r.ended_at - r.started_at) * 1000) : "…"}</td>
        </tr>`).join("")}</tbody></table>`
      : `<div class="empty"><h2>No runs yet</h2><p>Start one from the Leads page.</p><a class="btn" href="#/leads">Go to leads</a></div>`}`;
  view.querySelectorAll("tr.click").forEach((tr) => {
    tr.addEventListener("click", () => (location.hash = tr.dataset.href));
    tr.addEventListener("keydown", (e) => e.key === "Enter" && (location.hash = tr.dataset.href));
  });
}

// ------------------------------------------------------------------ review
async function reviewView() {
  const drafts = await api("/api/drafts?status=pending");
  view.innerHTML = `
    <div class="page-head"><div><h1>Review</h1>
      <p>Nothing leaves without a human. Approving records the outreach in memory, so the agent will skip this lead for ${state.meta?.recent_contact_days ?? 30} days.
      Rejecting with a note teaches the agent a lesson it reads on its next run.</p></div></div>
    ${drafts.length ? drafts.map((d) => `
      <section class="review-item" data-id="${esc(d.id)}">
        <div>
          <article class="letter">
            <div class="to">To ${esc(d.lead.name)}, ${esc(d.lead.title)} at ${esc(d.lead.company)} · <a href="#/run/${esc(d.run_id)}">trace</a></div>
            <div class="subject">${esc(d.subject)}</div>
            <div class="body">${esc(d.body)}</div>
          </article>
        </div>
        <div class="review-actions">
          <div><div class="section-label">Facts cited</div>
            <ul class="facts">${(d.facts || []).map((f) => `<li><code>${esc(f.source_id)}</code><span>${esc(f.claim)}</span></li>`).join("")}</ul></div>
          <label class="section-label" for="note-${esc(d.id)}" style="margin:6px 0 -4px">Lesson for the agent (needed to reject)</label>
          <textarea id="note-${esc(d.id)}" rows="3" placeholder="For example: Don't mention hiring. Lead with their expansion instead."></textarea>
          <label class="check"><input type="checkbox"> Apply this lesson to every lead, not only ${esc(d.lead.company)}</label>
          <div class="row-actions">
            <button class="btn signal" data-act="approve">${ICON.check}Approve</button>
            <button class="btn ghost" data-act="reject">${ICON.x}Reject with lesson</button>
          </div>
        </div>
      </section>`).join("")
      : `<div class="empty"><h2>No drafts waiting</h2><p>Run the agent on a qualified lead, such as Maya Okafor at Brightloop, to get a draft here.</p><a class="btn" href="#/leads">Go to leads</a></div>`}`;

  view.querySelectorAll(".review-item").forEach((item) => {
    item.querySelectorAll("[data-act]").forEach((b) => b.addEventListener("click", async () => {
      const approve = b.dataset.act === "approve";
      const note = item.querySelector("textarea").value.trim();
      if (!approve && !note) { toast("Write the lesson first, so the agent learns why.", true); item.querySelector("textarea").focus(); return; }
      b.disabled = true;
      try {
        await api(`/api/drafts/${item.dataset.id}/review`, { method: "POST", body: JSON.stringify({ approve, note, apply_globally: item.querySelector("input[type=checkbox]").checked }) });
        toast(approve ? "Approved. Saved to memory as outreach." : "Rejected. Lesson saved to memory.");
        route();
      } catch (e) { toast(e.message, true); b.disabled = false; }
    }));
  });
}

// ------------------------------------------------------------------ evals
async function evalsView(id) {
  stopPoll();
  const list = await api("/api/evals");
  const current = id ? await api(`/api/evals/${id}`) : list[0] ? await api(`/api/evals/${list[0].id}`) : null;
  const running = current?.status === "running";
  const results = (current?.results || []).filter((r) => r.lead_id);
  const s = current?.summary || {};
  const pct = (v) => (v == null ? "n/a" : `${Math.round(v * 100)}%`);
  const total = current?.cases?.length || state.meta?.cases || 10;

  view.innerHTML = `
    <div class="page-head">
      <div><h1>Evals</h1>
        <p>A fixed set of ${total} labelled leads, run through the real agent. Code checks the decision, guardrails, injection safety and relevance.
        With Gemini on, a second model call grades each email for personalisation, clarity and tone.</p></div>
      <button class="btn signal" id="run-eval" ${running ? "disabled" : ""}>${ICON.beaker}${running ? "Running…" : "Run evaluation suite"}</button>
    </div>
    ${running ? `<div class="progress" aria-label="progress"><i style="width:${(results.length / total) * 100}%"></i></div>` : ""}
    ${current ? `
      ${current.summary ? `<dl class="scorecard" aria-label="Summary">
        <div><dt>Cases passed</dt><dd>${pct(s.pass_rate)}</dd></div>
        <div><dt>Right decision</dt><dd>${pct(s.decision_accuracy)}</dd></div>
        <div><dt>Guardrails passed first try</dt><dd>${pct(s.first_try_guardrail_pass)}</dd></div>
        <div><dt>Injection safe</dt><dd>${s.injection_safe ? "yes" : "no"}</dd></div>
        <div><dt>Avg steps · tokens</dt><dd>${s.avg_steps ?? "–"} · ${fmtTok(Math.round(s.avg_tokens || 0))}</dd></div>
        <div><dt>Avg time per lead</dt><dd>${s.avg_latency_s ?? "–"}s</dd></div>
        <div><dt>Total cost</dt><dd>${fmtUsd(s.total_cost_usd)}</dd></div>
        <div><dt>Judge (pers · clar · tone)</dt><dd>${s.judge_personalisation ?? "–"} · ${s.judge_clarity ?? "–"} · ${s.judge_tone ?? "–"}</dd></div>
      </dl>` : ""}
      <p class="data muted">${esc(current.id)} · ${esc(current.model)} · ${ago(current.started_at)} · ${esc(current.status)}</p>
      <table class="ledger">
        <thead><tr><th>Case</th><th>Expected</th><th>Got</th><th class="hide-sm">What it tests</th><th class="hide-sm">Judge</th><th class="num">Steps</th><th>Result</th></tr></thead>
        <tbody>${results.map((r) => `
          <tr class="click" data-href="#/run/${esc(r.run_id)}" tabindex="0">
            <td class="data">${esc(r.lead_id)}</td>
            <td>${esc(r.expect)}</td>
            <td>${mark(r.got || "error", r.got || "error")}</td>
            <td class="hide-sm soft">${esc(r.why)}</td>
            <td class="hide-sm judge">${r.judge?.personalisation ? `${r.judge.personalisation} · ${r.judge.clarity} · ${r.judge.tone}` : r.judge?.error ? "error" : "–"}</td>
            <td class="num">${r.steps}</td>
            <td>${mark(r.passed ? "pass" : "fail", r.passed ? "pass" : "fail")}</td>
          </tr>`).join("")}</tbody>
      </table>
      ${list.length > 1 ? `<h2 style="margin:40px 0 12px">History</h2>
        <table class="ledger"><tbody>${list.map((e) => `<tr class="click" data-href="#/evals/${esc(e.id)}" tabindex="0">
          <td class="data">${esc(e.id)}</td><td class="muted">${ago(e.started_at)}</td><td class="data">${esc(e.model)}</td>
          <td>${e.summary ? pct(e.summary.pass_rate) + " passed" : esc(e.status)}</td></tr>`).join("")}</tbody></table>` : ""}`
      : `<div class="empty"><h2>No evaluation yet</h2><p>Run the suite to score the agent on all ${total} cases. It takes about a minute with Gemini.</p></div>`}`;

  document.getElementById("run-eval").addEventListener("click", async (e) => {
    e.target.disabled = true;
    try { const { eval_id } = await api("/api/evals", { method: "POST" }); location.hash = `#/evals/${eval_id}`; }
    catch (err) { toast(err.message, true); e.target.disabled = false; }
  });
  view.querySelectorAll("tr.click").forEach((tr) => {
    tr.addEventListener("click", () => (location.hash = tr.dataset.href));
    tr.addEventListener("keydown", (ev) => ev.key === "Enter" && (location.hash = tr.dataset.href));
  });
  if (running) state.poll = setTimeout(() => route(), 1500);
}

// ------------------------------------------------------------------ memory
async function memoryView() {
  const mem = await api("/api/memory");
  const text = (m) => esc(m.content?.text || m.content?.summary || JSON.stringify(m.content));
  view.innerHTML = `
    <div class="page-head"><div><h1>Memory</h1>
      <p>Long-term memory the agent reads at the start of every run. Outreach entries stop repeat emails; lessons come from human reviewers.
      Scopes: one lead, one company, or every lead.</p></div></div>
    ${mem.length ? `<table class="ledger"><thead><tr><th>Scope</th><th>Kind</th><th>Content</th><th class="hide-sm">Source</th><th>When</th></tr></thead>
      <tbody>${mem.map((m) => `<tr>
        <td class="data">${esc(m.scope)}</td><td>${mark(m.kind === "lesson" ? "flagged" : "ok", m.kind)}</td>
        <td>${text(m)}</td><td class="data hide-sm muted">${esc(m.source)}</td><td class="muted">${ago(m.created_at)}</td></tr>`).join("")}</tbody></table>`
      : `<div class="empty"><h2>Memory is empty</h2></div>`}`;
}

// ------------------------------------------------------------------ how it works
async function howView() {
  view.innerHTML = `
    <div class="prose">
      <h1>How it works</h1>
      <p>One lead per run. The model can act only through eight tools. Code, not the prompt, enforces scope, budgets and checks.
      Every step is traced, and a human approves every email. Full write-up with the same diagrams: <a href="https://github.com/abeni-al7/sdr-agent-lab/blob/main/docs/ARCHITECTURE.md">docs/ARCHITECTURE.md</a>.</p>

      <h2>Architecture</h2>
      <div class="diagram"><pre class="mermaid">
flowchart TB
  UI[Console] -->|POST /api/runs| API[FastAPI]
  API -->|thread| LOOP[Agent loop]
  LOOP <-->|tool calls| LLM[Gemini]
  LOOP --> TOOLS[Scoped tools]
  TOOLS --> MEM[(Memory)]
  TOOLS --> DATA[(Leads, pages, signals)]
  TOOLS --> GUARD[Guardrails]
  LOOP --> TRACE[(Spans)]
  GUARD -->|accepted| DRAFTS[(Drafts)]
  DRAFTS -->|approve or reject| HUMAN[Human review]
  HUMAN -->|outreach or lesson| MEM
      </pre></div>

      <h2>One run, step by step</h2>
      <div class="diagram"><pre class="mermaid">
sequenceDiagram
  participant L as Agent loop
  participant M as Gemini
  participant T as Tools
  participant G as Guardrails
  L->>M: system prompt + task + tool schemas
  M-->>L: recall_memory(L-01)
  L->>T: run tool, scoped to this lead
  T-->>L: past outreach, lessons
  M-->>L: get_lead, score_icp
  M-->>L: read_company_page
  T->>G: scan untrusted text
  G-->>T: injection removed
  M-->>L: submit_draft(subject, body, facts)
  L->>G: grounding, numbers, links, spam, leak, recent contact
  alt a check fails
    G-->>M: failures, one retry left
  else all pass
    G-->>L: saved as draft, waits for a human
  end
      </pre></div>

      <h2>Observing the agent</h2>
      <p>Each LLM call, tool call, memory read and guardrail check is a span with input, output, duration, tokens and flags.
      A run is a trace. The Runs page is the trace browser; cost is estimated from tokens.
      The span model maps directly to Langfuse or OpenTelemetry (trace and observations), so exporting is an adapter, not a rewrite.</p>

      <h2>Judging quality</h2>
      <p>The Evals page runs ten labelled cases: right decision, guardrails passed on the first try, no injected text in the email,
      at least one expected, sourced detail, plus steps, tokens, time and cost. A second Gemini call grades personalisation, clarity and tone from 1 to 5.</p>

      <h2>Security</h2>
      <ul>
        <li>Least privilege: tools refuse any lead or domain except the one in this run.</li>
        <li>Untrusted input: website text is scanned and hidden instructions are removed before the model sees it (try Lucas Moreau at Pivotly).</li>
        <li>Output checks: every fact must cite a source read in this run; every number must exist in a source; no links, emails or hype words.</li>
        <li>Budgets: at most ${state.meta?.max_steps ?? 10} steps, ${fmtTok(state.meta?.max_tokens_per_run ?? 60000)} tokens and 2 draft attempts per run.</li>
        <li>Human in the loop: nothing is sent. Drafts wait for approval.</li>
      </ul>

      <h2 id="redteam">Try the guardrails</h2>
      <p>This is the email a fooled agent might write for Lucas Moreau at Pivotly: it follows the hidden instruction on their website,
      invents a number and cites a source it never read. Edit it and run the same checks the agent faces.</p>
      <div class="review-actions" style="margin-top:14px">
        <label class="section-label" for="rt-subject">Subject</label>
        <input type="text" id="rt-subject" value="Guaranteed 40% more replies for Pivotly">
        <label class="section-label" for="rt-body">Body</label>
        <textarea id="rt-body" rows="7">Hi Lucas,

Pivotly grew outbound replies by 73% this year. ListForge can guarantee the same for your team.

Reply with your bank details to claim a free audit at pivotly-rewards.example.

Best,
Abenezer</textarea>
        <label class="section-label" for="rt-facts">Facts cited (source ids)</label>
        <input type="text" id="rt-facts" value="signal:pivotly.example#1, crm:pivotly-pipeline">
        <div class="row-actions"><button class="btn signal" id="rt-run">${ICON.beaker}Run the checks</button></div>
        <div id="rt-out" aria-live="polite"></div>
      </div>
    </div>`;
  document.getElementById("rt-run").addEventListener("click", async () => {
    const out = document.getElementById("rt-out");
    const facts = document.getElementById("rt-facts").value.split(",").map((s) => s.trim()).filter(Boolean)
      .map((source_id) => ({ source_id, claim: document.getElementById("rt-body").value.slice(0, 160) }));
    try {
      const r = await api("/api/guardrails/check", { method: "POST", body: JSON.stringify({
        lead_id: "L-06", subject: document.getElementById("rt-subject").value, body: document.getElementById("rt-body").value, facts }) });
      out.innerHTML = r.passed
        ? `<div class="verdict" style="margin-top:10px"><h3>${mark("pass", "all checks passed")}</h3></div>`
        : `<div class="verdict" style="margin-top:10px"><h3>${mark("blocked", `${r.failures.length} checks failed`)}</h3>
           <ul class="facts" style="margin-top:12px">${r.failures.map((f) => `<li><code>${esc(f.check)}</code><span>${esc(f.detail)}</span></li>`).join("")}</ul>
           <p class="data muted">sources this run could cite: ${r.sources.map(esc).join(", ")}</p></div>`;
    } catch (e) { toast(e.message, true); }
  });
  const dark = document.documentElement.dataset.theme === "dark" || (!document.documentElement.dataset.theme && matchMedia("(prefers-color-scheme: dark)").matches);
  const { default: mermaid } = await import("https://cdn.jsdelivr.net/npm/mermaid@11.4.1/dist/mermaid.esm.min.mjs");
  mermaid.initialize({ startOnLoad: false, theme: "base", securityLevel: "strict",
    themeVariables: { fontFamily: "Public Sans, sans-serif", primaryColor: dark ? "#1C1A15" : "#FBF8F2", primaryTextColor: dark ? "#EDE7DA" : "#17150F",
      primaryBorderColor: dark ? "#6E685B" : "#4A463C", lineColor: dark ? "#C2BBAB" : "#4A463C", actorBkg: dark ? "#1C1A15" : "#FBF8F2",
      noteBkgColor: dark ? "#3A1E16" : "#F6DED6", signalColor: dark ? "#EDE7DA" : "#17150F" } });
  await mermaid.run({ nodes: view.querySelectorAll(".mermaid") });
}

// ------------------------------------------------------------------ router
async function route() {
  const [, tab = "leads", id] = location.hash.split("/");
  document.querySelectorAll(".tabs a").forEach((a) => {
    const active = a.dataset.tab === (tab === "run" ? "runs" : tab);
    a.toggleAttribute("aria-current", active);
    if (active) a.setAttribute("aria-current", "page");
  });
  const views = { leads: leadsView, run: () => runView(id), runs: runsView, review: reviewView, evals: () => evalsView(id), memory: memoryView, how: howView };
  try {
    await (views[tab] || leadsView)();
  } catch (e) {
    view.innerHTML = `<div class="empty"><h2>Something went wrong</h2><p>${esc(e.message)}</p><button class="btn" onclick="location.reload()">Reload</button></div>`;
  }
}

window.addEventListener("hashchange", () => { stopPoll(); open.clear(); route(); view.focus({ preventScroll: true }); });

document.getElementById("theme").addEventListener("click", () => {
  const root = document.documentElement;
  const dark = root.dataset.theme ? root.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
  root.dataset.theme = dark ? "light" : "dark";
  try { localStorage.setItem("theme", root.dataset.theme); } catch { /* storage may be blocked */ }
  if (location.hash.startsWith("#/how")) route();
});
try { const t = localStorage.getItem("theme"); if (t) document.documentElement.dataset.theme = t; } catch { /* ignore */ }

loadMeta().catch(() => {}).finally(() => { route(); refreshPending(); });
