/* Trishula Studio — front-end controller.
   Two modes: Code (Shiva, the agent) and Chat (the advanced companion).
   Streams Server-Sent Events and renders rich, structured responses:
   tool cards, diffs, verification badges + coverage meters, architecture
   diagrams (a tiny SVG layer), and an execution timeline. */

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];

const state = {
  mode: "code",          // code | chat
  settings: {},
  chatHistory: [],       // {role, content}
  busy: false,
  currentAgent: null,    // active agent turn element
  blocks: new Map(),     // blockKey -> element (live-updated tool cards)
  timeline: [],
};

const SUGGESTIONS = {
  code: [
    { t: "Fix a failing test", d: "Point me at a repo and I’ll diagnose and repair it." },
    { t: "Add a feature end-to-end", d: "I plan, edit, verify, and repair until green." },
    { t: "Improve coverage", d: "Find uncovered lines and write tests that exercise them." },
    { t: "Refactor safely", d: "Surgical edits with the test suite as the safety net." },
  ],
  chat: [
    { t: "Design an architecture", d: "Ask for a system and I’ll diagram it." },
    { t: "Explain a concept", d: "Deep, structured answers with diagrams when helpful." },
    { t: "Debug a tricky problem", d: "Reason through root causes with me." },
    { t: "Plan an engineering sprint", d: "Break big goals into verified milestones." },
  ],
};

/* ── boot ─────────────────────────────────────────────────────────────── */

async function boot() {
  bindUI();
  try {
    const h = await api("/api/health");
    state.settings = h.settings || {};
    applySettings(state.settings);
    setLive(h.mode && h.mode !== "stub");
  } catch (e) {
    setLive(false);
  }
  renderSuggestions();
  setMode("code");
}

function bindUI() {
  $$(".mode-btn").forEach((b) =>
    b.addEventListener("click", () => setMode(b.dataset.mode))
  );
  $("#sendBtn").addEventListener("click", submit);
  $("#newTaskBtn").addEventListener("click", resetTranscript);
  $("#menuBtn").addEventListener("click", () => $("#sidebar").classList.toggle("open"));
  const input = $("#input");
  input.addEventListener("input", autosize);
  input.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") submit();
  });
  // settings modal
  $("#settingsBtn").addEventListener("click", openSettings);
  $("#closeSettings").addEventListener("click", closeSettings);
  $("#cancelSettings").addEventListener("click", closeSettings);
  $("#saveSettings").addEventListener("click", saveSettings);
  $("#settingsModal").addEventListener("click", (e) => {
    if (e.target.id === "settingsModal") closeSettings();
  });
}

function setMode(mode) {
  state.mode = mode;
  $$(".mode-btn").forEach((b) => b.classList.toggle("active", b.dataset.mode === mode));
  const s = state.settings;
  if (mode === "chat") {
    $("#modeTitle").textContent = s.chat_name || "Saraswati";
    $("#modeSub").textContent = s.chat_tagline || "Advanced chat";
    $("#input").placeholder = `Message ${s.chat_name || "Saraswati"}…`;
    $("#modeHint").textContent = "chat mode";
  } else {
    $("#modeTitle").textContent = s.agent_name || "Shiva";
    $("#modeSub").textContent = s.agent_tagline || "Autonomous engineering agent";
    $("#input").placeholder = "Ask Shiva to build, fix, or verify something…";
    $("#modeHint").textContent = "code mode";
  }
  resetTranscript();
}

function resetTranscript() {
  $("#transcript").innerHTML = "";
  state.chatHistory = [];
  state.blocks.clear();
  state.timeline = [];
  renderWelcome();
}

function renderWelcome() {
  const s = state.settings;
  const name = state.mode === "chat" ? (s.chat_name || "Saraswati") : (s.agent_name || "Shiva");
  const w = el("div", "welcome");
  w.innerHTML = `
    <div class="welcome-mark"><svg viewBox="0 0 32 32" width="54" height="54"><path d="M16 2 L20 14 L30 16 L20 18 L16 30 L12 18 L2 16 L12 14 Z"/></svg></div>
    <h2>${state.mode === "chat" ? `Talk to ${name}` : `What should ${name} build?`}</h2>
    <p>${state.mode === "chat"
      ? "A reasoning companion that answers richly and diagrams systems when they help."
      : "Describe a task. Shiva plans, edits, runs tests, and repairs — streaming every step."}</p>
    <div class="suggestion-grid"></div>`;
  const grid = $(".suggestion-grid", w);
  SUGGESTIONS[state.mode].forEach((sug) => {
    const card = el("div", "suggestion");
    card.innerHTML = `<div class="s-title">${sug.t}</div><div class="s-desc">${sug.d}</div>`;
    card.addEventListener("click", () => { $("#input").value = sug.t; submit(); });
    grid.appendChild(card);
  });
  $("#transcript").appendChild(w);
}

function renderSuggestions() { /* welcome re-renders per mode */ }

function setLive(live) {
  $("#statusPill").classList.toggle("live", live);
  $("#statusText").textContent = live ? "model connected" : "offline mode (stub)";
}

/* ── submit / streaming ───────────────────────────────────────────────── */

async function submit() {
  if (state.busy) return;
  const text = $("#input").value.trim();
  if (!text) return;
  const workspace = $("#workspaceInput").value.trim();
  $("#welcome")?.remove();
  addUserBubble(text);
  $("#input").value = "";
  autosize();
  state.busy = true;
  $("#sendBtn").disabled = true;

  const agent = startAgentTurn();
  state.currentAgent = agent;

  const body = state.mode === "chat"
    ? { message: text, history: state.chatHistory }
    : { goal: text, workspace: workspace || state.settings.workspace || "." };
  state.chatHistory.push({ role: "user", content: text });

  await streamSSE(state.mode === "chat" ? "/api/chat" : "/api/code", body, {
    onEvent: (ev, data) => handleEvent(ev, data, agent),
  });

  state.busy = false;
  $("#sendBtn").disabled = false;
  scrollToEnd();
}

function handleEvent(ev, data, agent) {
  switch (ev) {
    case "token":
      appendChatToken(agent, data.text);
      break;
    case "chat_finished":
      finalizeChat(agent, data.model);
      break;
    case "run_started":
      addTimeline("run", `Run started — <b>${escapeHtml(data.goal)}</b>`);
      break;
    case "event":
      handleJournal(data.kind, data.payload, agent);
      break;
    case "run_finished":
      finishRun(agent, data);
      break;
    case "error":
      addFeedback(agent.body, `⚠ ${data.message}`, "fail");
      addTimeline("fail", `Error: ${data.message}`);
      setTyping(agent, "");
      break;
    case "done":
      setTyping(agent, "");
      break;
  }
  scrollToEnd();
}

/* ── journal → rich blocks (code mode) ────────────────────────────────── */

function handleJournal(kind, p, agent) {
  const body = agent.body;
  switch (kind) {
    case "plan.made":
      if (p.repair_round) {
        addFeedback(body, `🔧 Repair round ${p.repair_round} — verdict was <b>${p.verdict}</b>. ` +
          `Feeding failures back and re-verifying…`);
        addTimeline("run", `Repair round ${p.repair_round} (${p.verdict})`);
      }
      break;
    case "tool.call": {
      const key = `t${p.step ?? Math.random()}`;
      const name = p.tool || "tool";
      const args = p.arguments || p.args || {};
      upsertBlock(body, key, {
        icon: toolIcon(name),
        title: toolTitle(name, args),
        meta: toolMeta(name, args),
        state: "running",
        stateLabel: "running",
      });
      addTimeline("run", `Tool <b>${escapeHtml(name)}</b> ${escapeHtml(shortArgs(args))}`);
      break;
    }
    case "tool.result": {
      const key = `t${p.step ?? "x"}`;
      const ok = p.ok !== false && !(p.data && p.data.denied) && !p.timed_out;
      const blk = state.blocks.get(key);
      const out = typeof p.output === "string" ? p.output : JSON.stringify(p.output ?? p.data ?? "", null, 2);
      if (blk) {
        setBlockState(blk, ok ? "ok" : "fail", ok ? "done" : (p.data && p.data.denied ? "denied" : "error"));
        setBlockBody(blk, out.slice(0, 4000));
      }
      addTimeline(ok ? "ok" : "fail", `${ok ? "✓" : "✕"} ${escapeHtml(p.tool || "tool")}`);
      break;
    }
    case "edit.applied": {
      const path = p.path || p.file || "file";
      upsertBlock(body, `edit:${path}`, {
        icon: "✎", title: `Edited ${path}`, meta: p.summary || "",
        state: "ok", stateLabel: "saved", kind: "diff",
        diff: { old: p.old_string || p.before || "", neu: p.new_string || p.after || "" },
      });
      addTimeline("ok", `Edited <b>${escapeHtml(path)}</b>`);
      break;
    }
    case "edit.failed":
      addFeedback(body, `Edit failed to match in <b>${escapeHtml(p.path || "file")}</b> — the file drifted; re-read before retrying.`);
      addTimeline("fail", `Edit failed: ${escapeHtml(p.path || "")}`);
      break;
    case "verify.verdict":
      renderVerdict(body, p);
      addTimeline(p.verdict === "pass" ? "ok" : "fail",
        `Verification: <b>${escapeHtml(p.verdict)}</b>` +
        (p.failed ? ` — ${p.failed} failed` : ""));
      break;
    default:
      break;
  }
}

function renderVerdict(body, p) {
  const v = (p.verdict || "skipped");
  const cls = v === "pass" ? "pass" : v === "partial" ? "partial" : "fail";
  const icon = v === "pass" ? "✓" : v === "partial" ? "◐" : "✕";
  const blk = el("div", "block");
  let meters = "";
  if (p.coverage) {
    for (const [file, d] of Object.entries(p.coverage.files || {})) {
      const pct = d.statements ? d.covered / d.statements : 1;
      meters += meterHtml(file, pct, d.missing || []);
    }
  }
  blk.innerHTML = `
    <div class="block-head">
      <span class="b-icon">🛡</span>
      <span class="b-title">Verification</span>
      <span class="b-state state-${cls === "pass" ? "ok" : cls === "fail" ? "fail" : "warn"}">${v.toUpperCase()}</span>
    </div>
    <div class="verdict">
      <span class="v-badge ${cls}">${icon} ${v}</span>
      ${p.passed != null ? `<span class="muted small">${p.passed} passed${p.failed ? ` · ${p.failed} failed` : ""}</span>` : ""}
      ${meters}
    </div>`;
  body.appendChild(blk);
  if (p.feedback) addFeedback(body, p.feedback);
  if (p.failures && p.failures.length) {
    addFeedback(body, "Failing: " + p.failures.slice(0, 6).map(escapeHtml).join(", "));
  }
}

function finishRun(agent, data) {
  const report = data.report || {};
  const retro = data.retrospective || {};
  setTyping(agent, "");
  const verdict = report.verdict || "skipped";
  const ok = report.ok;
  const blk = el("div", "block");
  const steps = report.steps ?? 0;
  const changed = (report.changed_files || []);
  blk.innerHTML = `
    <div class="block-head">
      <span class="b-icon">${ok ? "✓" : "✕"}</span>
      <span class="b-title">Run complete</span>
      <span class="b-state state-${ok ? "ok" : "fail"}">${ok ? "SUCCESS" : "NEEDS WORK"}</span>
    </div>
    <div class="block-body">${escapeHtml(report.summary || "")}
• steps: ${steps}${report.repair_rounds ? `\n• repair rounds: ${report.repair_rounds}` : ""}
• changed: ${changed.join(", ") || "(none)"}
• verdict: ${verdict}${retro.score != null ? `\n• run quality score: ${(retro.score*100).toFixed(0)}%` : ""}</div>`;
  collapseLater(blk);
  agent.body.appendChild(blk);

  // memory / learning diagram of what the agent did
  if (changed.length || steps) {
    const flow = [`Goal: ${short(report.goal || "task", 40)}`,
      ...changed.slice(0, 6).map((f) => `Edit: ${f}`),
      `Verify: ${verdict}`];
    agent.body.appendChild(diagramBlock(flow, "Run flow"));
  }
  state.chatHistory.push({ role: "assistant", content: report.summary || `Run ${ok ? "succeeded" : "finished"}.` });
  loadRuns();
}

/* ── block helpers ────────────────────────────────────────────────────── */

function startAgentTurn() {
  const turn = el("div", "turn msg-agent");
  const name = state.mode === "chat"
    ? (state.settings.chat_name || "Saraswati")
    : (state.settings.agent_name || "Shiva");
  turn.innerHTML = `
    <div class="avatar"><svg viewBox="0 0 32 32" width="18" height="18"><path d="M16 2 L20 14 L30 16 L20 18 L16 30 L12 18 L2 16 L12 14 Z"/></svg></div>
    <div class="agent-body">
      <div class="agent-name">${escapeHtml(name)} <span class="typing">thinking…</span></div>
      <div class="prose"></div>
    </div>`;
  $("#transcript").appendChild(turn);
  return { turn, body: $(".prose", turn), typing: $(".typing", turn) };
}

function setTyping(agent, t) { if (agent.typing) agent.typing.textContent = t; }

function addUserBubble(text) {
  const turn = el("div", "turn msg-user");
  const b = el("div", "bubble");
  b.textContent = text;
  turn.appendChild(b);
  $("#transcript").appendChild(turn);
}

function appendChatToken(agent, tok) {
  setTyping(agent, "");
  let prose = agent.body;
  // The chat reply renders as markdown; we accumulate into a buffer.
  agent._buf = (agent._buf || "") + tok;
  const rendered = renderRich(agent._buf);
  // keep a live-updating prose node, but preserve any blocks already added
  if (!agent._chatProse) {
    agent._chatProse = el("div", "prose");
    body: {
      const tmp = document.createElement("div");
      tmp.innerHTML = rendered;
      // extract ```diagram blocks
      agent._chatProse.innerHTML = "";
    }
    prose.appendChild(agent._chatProse);
  }
  renderMarkdownInto(agent._chatProse, agent._buf);
}

function finalizeChat(agent, model) {
  setTyping(agent, model ? `via ${model}` : "");
  const full = agent._buf || "";
  state.chatHistory.push({ role: "assistant", content: full });
  // diagrams already extracted by renderMarkdownInto
}

function upsertBlock(parent, key, spec) {
  let blk = state.blocks.get(key);
  if (!blk) {
    blk = el("div", "block");
    blk.innerHTML = `
      <div class="block-head">
        <span class="b-icon">${spec.icon || "•"}</span>
        <span class="b-title">${escapeHtml(spec.title || "")}</span>
        <span class="b-meta">${escapeHtml(spec.meta || "")}</span>
        <span class="b-state state-running">running</span>
        <span class="chev">▾</span>
      </div>
      <div class="block-body"></div>`;
    $(".block-head", blk).addEventListener("click", () => blk.classList.toggle("collapsed"));
    parent.appendChild(blk);
    state.blocks.set(key, blk);
  }
  if (spec.state) setBlockState(blk, spec.state, spec.stateLabel);
  if (spec.body) setBlockBody(blk, spec.body);
  if (spec.diff) setBlockDiff(blk, spec.diff);
  return blk;
}

function setBlockState(blk, stateCls, label) {
  const st = $(".b-state", blk);
  st.className = `b-state state-${stateCls}`;
  st.textContent = label;
}
function setBlockBody(blk, text) {
  const b = $(".block-body", blk);
  if (!b.dataset.diff) b.textContent = text;
}
function setBlockDiff(blk, diff) {
  const b = $(".block-body", blk);
  b.classList.add("diff");
  b.dataset.diff = "1";
  b.innerHTML = renderDiff(diff.old, diff.neu);
}
function collapseLater(blk) { setTimeout(() => blk.classList.add("collapsed"), 1500); }

function addFeedback(parent, html, kind) {
  const f = el("div", "feedback" + (kind === "info" ? " info" : ""));
  f.innerHTML = html;
  parent.appendChild(f);
}

function addTimeline(cls, html) {
  state.timeline.push({ cls, html, t: new Date() });
  // Timeline is rendered as a block in the current agent body at run end;
  // for live feel we also keep it lightweight here.
  const agent = state.currentAgent;
  if (!agent) return;
  if (!agent._tl) {
    const wrap = el("div", "block");
    wrap.innerHTML = `<div class="block-head"><span class="b-icon">⏱</span>
      <span class="b-title">Activity timeline</span><span class="chev">▾</span></div>
      <div class="timeline"></div>`;
    $(".block-head", wrap).addEventListener("click", () => wrap.classList.toggle("collapsed"));
    agent.body.appendChild(wrap);
    agent._tl = $(".timeline", wrap);
  }
  const row = el("div", "tl-row");
  row.innerHTML = `<span class="tl-dot ${cls}"></span>
    <span class="tl-text">${html}</span>
    <span class="tl-time">${new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}</span>`;
  agent._tl.appendChild(row);
}

/* ── meters / verdict ─────────────────────────────────────────────────── */

function meterHtml(file, pct, missing) {
  const p = Math.round(pct * 100);
  const color = pct >= 0.7 ? "var(--ok)" : pct >= 0.5 ? "var(--warn)" : "var(--bad)";
  return `<div class="meter">
    <div class="m-track"><div class="m-fill" style="width:${p}%;background:${color}"></div></div>
    <div class="m-label">${escapeHtml(file)} — ${p}% covered${missing.length ? ` · lines ${missing.slice(0,8).join(", ")}` : ""}</div>
  </div>`;
}

/* ── diagrams (tiny SVG layer) ────────────────────────────────────────── */

/* Parse a mini-DSL:
     "A -> B"          edge
     "A: Label"        node label
     "A -> B: edge"    edge label
   Lines may also be free text used as node labels. Produces a layered,
   auto-laid-out SVG with rounded nodes and glowing connectors. */
function parseDiagram(text) {
  const nodes = new Map();
  const edges = [];
  const lines = text.split("\n").map((l) => l.trim()).filter(Boolean);
  const norm = (s) => s.replace(/[^a-z0-9]/gi, "_").replace(/^_+|_+$/g, "") || ("n" + nodes.size);
  for (const line of lines) {
    const arrow = line.match(/^(.+?)\s*->\s*(.+?)(?::\s*(.+))?$/);
    if (arrow) {
      const a = arrow[1].trim(), b = arrow[2].trim(), lbl = (arrow[3] || "").trim();
      const ia = norm(a), ib = norm(b);
      if (!nodes.has(ia)) nodes.set(ia, { id: ia, label: a.replace(/^[A-Za-z]+:\s*/, "") });
      if (!nodes.has(ib)) nodes.set(ib, { id: ib, label: b.replace(/^[A-Za-z]+:\s*/, "") });
      edges.push({ from: ia, to: ib, label: lbl });
    } else if (line.includes(":")) {
      const [id, ...rest] = line.split(":");
      nodes.set(norm(id), { id: norm(id), label: rest.join(":").trim() });
    } else {
      const id = norm(line);
      nodes.set(id, { id, label: line });
    }
  }
  return { nodes: [...nodes.values()], edges };
}

function layoutDiagram(graph) {
  // longest-path layering (left → right columns)
  const depth = {};
  const adj = {};
  graph.nodes.forEach((n) => (adj[n.id] = []));
  graph.edges.forEach((e) => adj[e.from]?.push(e.to));
  const visit = (id, d) => {
    if ((depth[id] ?? -1) >= d) return;
    depth[id] = d;
    (adj[id] || []).forEach((t) => visit(t, d + 1));
  };
  graph.nodes.forEach((n) => visit(n.id, depth[n.id] ?? 0));
  const cols = {};
  graph.nodes.forEach((n) => {
    const c = depth[n.id] || 0;
    (cols[c] = cols[c] || []).push(n);
  });
  return cols;
}

function renderDiagramSVG(text) {
  const graph = parseDiagram(text);
  if (graph.nodes.length < 2 && graph.edges.length === 0) return "";
  const cols = layoutDiagram(graph);
  const colKeys = Object.keys(cols).map(Number).sort((a, b) => a - b);
  const NW = 168, NH = 52, GX = 90, GY = 28;
  const rows = Math.max(...colKeys.map((c) => cols[c].length));
  const width = colKeys.length * (NW + GX) + 40;
  const height = rows * (NH + GY) + 40;
  const pos = {};
  colKeys.forEach((c) => {
    cols[c].forEach((n, i) => {
      pos[n.id] = { x: 24 + c * (NW + GX), y: 24 + i * (NH + GY), n };
    });
  });
  let svg = `<svg viewBox="0 0 ${width} ${height}" width="${width}" height="${height}" xmlns="http://www.w3.org/2000/svg">
    <defs>
      <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
        <path d="M0 0 L10 5 L0 10 z" fill="var(--accent)"/></marker>
      <linearGradient id="nodeg" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0" stop-color="var(--surface-3)"/><stop offset="1" stop-color="var(--surface-2)"/></linearGradient>
    </defs>`;
  for (const e of graph.edges) {
    const a = pos[e.from], b = pos[e.to];
    if (!a || !b) continue;
    const x1 = a.x + NW, y1 = a.y + NH / 2, x2 = b.x, y2 = b.y + NH / 2;
    const mx = (x1 + x2) / 2;
    svg += `<path d="M${x1} ${y1} C ${mx} ${y1}, ${mx} ${y2}, ${x2 - 6} ${y2}" fill="none"
      stroke="var(--accent)" stroke-width="2" opacity="0.7" marker-end="url(#arrow)"/>`;
    if (e.label)
      svg += `<text x="${mx}" y="${(y1 + y2) / 2 - 6}" fill="var(--text-dim)" font-size="11"
        text-anchor="middle" font-family="var(--font)">${escapeHtml(e.label)}</text>`;
  }
  for (const id in pos) {
    const { x, y, n } = pos[id];
    svg += `<g>
      <rect x="${x}" y="${y}" rx="12" ry="12" width="${NW}" height="${NH}" fill="url(#nodeg)"
        stroke="var(--border-strong)"/>
      <text x="${x + NW / 2}" y="${y + NH / 2 + 4}" fill="var(--text)" font-size="12.5"
        text-anchor="middle" font-family="var(--font)" font-weight="600">${escapeHtml(truncate(n.label, 22))}</text>
    </g>`;
  }
  return svg + "</svg>";
}

function diagramBlock(lines, title) {
  const blk = el("div", "block");
  const svg = renderDiagramSVG(lines.join("\n"));
  blk.innerHTML = `<div class="block-head"><span class="b-icon">◈</span>
    <span class="b-title">${escapeHtml(title || "Diagram")}</span></div>
    <div class="diagram-wrap">${svg || "<span class='muted'>Not enough structure to diagram.</span>"}</div>`;
  return blk;
}

/* ── markdown with diagram extraction ─────────────────────────────────── */

function renderRich(text) {
  // split out ```diagram fenced blocks
  const parts = text.split(/```diagram\n?([\s\S]*?)```/g);
  return parts;
}

function renderMarkdownInto(container, text) {
  // Extract diagram fences; render markdown for the rest, appending diagrams.
  container.innerHTML = "";
  const segments = text.split(/```(\w*)\n?([\s\S]*?)```/g);
  // segments: [pre, lang, code, pre, lang, code...]
  let i = 0;
  while (i < segments.length) {
    const prose = segments[i++];
    if (prose) container.appendChild(mdNode(prose));
    if (i < segments.length) {
      const lang = segments[i++], code = segments[i++];
      if (lang === "diagram") {
        const wrap = el("div", "diagram-wrap");
        wrap.innerHTML = renderDiagramSVG(code) || escapeHtml(code);
        container.appendChild(wrap);
      } else {
        const pre = document.createElement("pre");
        const c = document.createElement("code");
        c.textContent = code.replace(/^\n|\n$/g, "");
        pre.appendChild(c);
        container.appendChild(pre);
      }
    }
  }
}

function mdNode(md) {
  const div = document.createElement("div");
  // extremely small, safe markdown: headings, bold, inline code, lists, paragraphs
  const lines = md.split("\n");
  let html = "", list = null;
  const flush = () => { if (list) { html += `</${list}>`; list = null; } };
  for (let ln of lines) {
    if (/^\s*[-*]\s+/.test(ln)) {
      if (list !== "ul") { flush(); html += "<ul>"; list = "ul"; }
      html += `<li>${inline(ln.replace(/^\s*[-*]\s+/, ""))}</li>`;
    } else if (/^\s*\d+\.\s+/.test(ln)) {
      if (list !== "ol") { flush(); html += "<ol>"; list = "ol"; }
      html += `<li>${inline(ln.replace(/^\s*\d+\.\s+/, ""))}</li>`;
    } else if (/^###\s+/.test(ln)) { flush(); html += `<h3>${inline(ln.replace(/^###\s+/, ""))}</h3>`; }
    else if (/^##\s+/.test(ln)) { flush(); html += `<h2>${inline(ln.replace(/^##\s+/, ""))}</h2>`; }
    else if (/^#\s+/.test(ln)) { flush(); html += `<h1>${inline(ln.replace(/^#\s+/, ""))}</h1>`; }
    else if (ln.trim() === "") { flush(); }
    else { flush(); html += `<p>${inline(ln)}</p>`; }
  }
  flush();
  div.innerHTML = html;
  return div;
}

function inline(s) {
  return escapeHtml(s)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/\*([^*]+)\*/g, "<em>$1</em>");
}

/* ── diff ─────────────────────────────────────────────────────────────── */

function renderDiff(oldStr, neuStr) {
  const o = (oldStr || "").split("\n"), n = (neuStr || "").split("\n");
  let html = `<span class="line hunk">@@ change @@</span>`;
  const common = new Set(n);
  o.forEach((l) => { if (!n.includes(l) && l.trim()) html += `<span class="line del">- ${escapeHtml(l)}</span>`; });
  n.forEach((l) => { if (!o.includes(l) && l.trim()) html += `<span class="line add">+ ${escapeHtml(l)}</span>`; });
  return html;
}

/* ── tool meta ────────────────────────────────────────────────────────── */

function toolIcon(name) {
  return { read_file: "📄", write_file: "📝", str_replace: "✎", run_shell: "▶",
    shell: "▶", search_code: "🔍", repo_map: "🗺", finish: "🏁", finish_task: "🏁" }[name] || "⚙";
}
function toolTitle(name, args) {
  if (name === "str_replace" || name === "write_file") return `Edit ${args.path || args.file || ""}`;
  if (name === "read_file") return `Read ${args.path || ""}`;
  if (name === "run_shell" || name === "shell") return "Run command";
  if (name === "search_code") return `Search “${args.query || ""}”`;
  if (name === "finish" || name === "finish_task") return "Finish";
  return name;
}
function toolMeta(name, args) {
  if (name === "run_shell" || name === "shell") return (args.command || args.cmd || "").slice(0, 80);
  if (name === "read_file" || name === "str_replace") return args.path || args.file || "";
  return "";
}
function shortArgs(args) {
  try { return JSON.stringify(args).slice(0, 90); } catch { return ""; }
}

/* ── settings ─────────────────────────────────────────────────────────── */

function applySettings(s) {
  document.documentElement.dataset.theme = s.theme || "midnight";
  document.documentElement.dataset.accent = s.accent || "indigo";
  $('[data-chat-label]').textContent = s.chat_name || "Saraswati";
  $('[data-agent-label]').textContent = s.agent_name || "Shiva";
  if (s.workspace) $("#workspaceInput").placeholder = s.workspace;
  setMode(state.mode);
}
function openSettings() {
  const s = state.settings;
  $("#setChatName").value = s.chat_name || "";
  $("#setChatTag").value = s.chat_tagline || "";
  $("#setAgentName").value = s.agent_name || "";
  $("#setAgentTag").value = s.agent_tagline || "";
  $("#setChatSystem").value = s.chat_system || "";
  $("#setProvider").value = s.model_provider || "";
  $("#setModel").value = s.model || "";
  $$(".swatch").forEach((b) => b.classList.toggle("selected", b.dataset.theme === (s.theme || "midnight")));
  $$(".accent").forEach((b) => b.classList.toggle("selected", b.dataset.accent === (s.accent || "indigo")));
  $("#settingsModal").hidden = false;
}
function closeSettings() { $("#settingsModal").hidden = true; }
async function saveSettings() {
  const payload = {
    chat_name: $("#setChatName").value.trim() || "Saraswati",
    chat_tagline: $("#setChatTag").value.trim(),
    agent_name: $("#setAgentName").value.trim() || "Shiva",
    agent_tagline: $("#setAgentTag").value.trim(),
    chat_system: $("#setChatSystem").value.trim(),
    theme: $(".swatch.selected")?.dataset.theme || "midnight",
    accent: $(".accent.selected")?.dataset.accent || "indigo",
    model_provider: $("#setProvider").value.trim(),
    model: $("#setModel").value.trim(),
  };
  state.settings = await api("/api/settings", { method: "POST", body: JSON.stringify(payload) });
  applySettings(state.settings);
  setLive(payload.model_provider && payload.model_provider !== "stub");
  closeSettings();
}
// swatch / accent selection
document.addEventListener("click", (e) => {
  if (e.target.classList.contains("swatch"))
    $$(".swatch").forEach((b) => b.classList.toggle("selected", b === e.target));
  if (e.target.classList.contains("accent"))
    $$(".accent").forEach((b) => b.classList.toggle("selected", b === e.target));
});

async function loadRuns() {
  try {
    const d = await api("/api/runs");
    const list = $("#runList");
    list.innerHTML = (d.runs || []).length
      ? d.runs.map((r) => `<div class="run-item"><span class="${r.ok ? "ok" : "fail"}">${r.ok ? "●" : "○"}</span> ${escapeHtml(truncate(r.goal, 34))}</div>`).join("")
      : `<div class="muted small">No runs yet.</div>`;
  } catch {}
}

/* ── utilities ────────────────────────────────────────────────────────── */

function el(tag, cls) { const e = document.createElement(tag); if (cls) e.className = cls; return e; }
function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function truncate(s, n) { s = String(s || ""); return s.length > n ? s.slice(0, n - 1) + "…" : s; }
function short(s, n) { return truncate(s, n); }
function autosize() { const t = $("#input"); t.style.height = "auto"; t.style.height = Math.min(t.scrollHeight, 200) + "px"; }
function scrollToEnd() { const t = $("#transcript"); t.scrollTop = t.scrollHeight; }

async function api(path, opts) {
  const r = await fetch(path, opts);
  return r.json();
}

async function streamSSE(path, body, { onEvent }) {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok || !res.body) { onEvent("error", { message: "stream failed" }); return; }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let idx;
    while ((idx = buf.indexOf("\n\n")) >= 0) {
      const chunk = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      let ev = "message", data = {};
      chunk.split("\n").forEach((line) => {
        if (line.startsWith("event:")) ev = line.slice(6).trim();
        else if (line.startsWith("data:")) {
          try { data = JSON.parse(line.slice(5).trim()); } catch {}
        }
      });
      onEvent(ev, data);
    }
  }
}

boot();
