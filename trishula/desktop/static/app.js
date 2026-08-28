/* trishula studio — terminal-grade controller.
   Style reference: Claude Code / Codex. Text-first, monospace, flat.
   Two modes: code (shiva, the agent) and chat (the companion). SSE streams
   live tool traces; rich output is inline and collapsible, never card-soup. */

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];

const state = {
  mode: "code", settings: {}, chatHistory: [],
  convId: null, busy: false,
  agent: null,          // current agent turn {body, glyph, status}
  pending: new Map(),   // tool call seq -> trace row
  toolSeq: 0,
  hwAnswers: {},        // clarification answers for the hardware wizard
};

const SUGGEST = {
  code: [
    ["fix a failing test", "point me at a repo; I diagnose and repair"],
    ["add a feature", "plan, edit, verify, and repair until green"],
    ["improve test coverage", "find uncovered lines, write the tests"],
    ["/gate", "evaluate a certification gate against the workspace"],
  ],
  chat: [
    ["design a system", "I’ll reason it out and draw a diagram"],
    ["/formulas", "browse the engineering formula catalogue"],
    ["/calc ohms_law i=0.5 r=100", "evaluate an engineering formula"],
    ["explain a concept", "dense, structured answers"],
  ],
  hw: [
    ["custom FLAC audio player", "on-board speaker, volume knob, SD storage"],
    ["smart environmental sensor", "temp/humidity/pressure → Wi-Fi + OLED"],
    ["indoor drone quadcopter", "IMU + barometer + RC link + 4 motors"],
    ["battery-powered data logger", "pick a platform, sensors, power"],
  ],
};

const COMMANDS = [
  { c: "/calc", d: "evaluate a formula, e.g. /calc ohms_law i=0.5 r=100", run: runCalc },
  { c: "/formulas", d: "browse engineering formulas", run: () => runFormulas("") },
  { c: "/gate", d: "evaluate a certification gate, e.g. /gate electrical_safety", run: runGateCmd },
  { c: "/gates", d: "list certification gates", run: runGates },
  { c: "/diagram", d: "insert an editable diagram", run: insertDiagram },
];

/* ── boot ─────────────────────────────────────────────────────────────── */
async function boot() {
  bind();
  try {
    const h = await api("/api/health");
    state.settings = h.settings || {};
    applySettings();
    setLive(h.mode && h.mode !== "stub");
  } catch { setLive(false); }
  setMode("code");
  loadConversations();
}

function bind() {
  $$(".tab").forEach((t) => t.addEventListener("click", () => setMode(t.dataset.mode)));
  $("#goBtn").addEventListener("click", submit);
  $("#newBtn").addEventListener("click", () => { state.convId = null; setMode(state.mode); });
  $("#menuBtn").addEventListener("click", () => $(".side").classList.toggle("open"));
  const input = $("#input");
  input.addEventListener("input", () => { autosize(); onType(); });
  input.addEventListener("keydown", onKey);
  $("#settingsBtn").addEventListener("click", openSettings);
  $("#closeSettings").addEventListener("click", closeSettings);
  $("#cancelSettings").addEventListener("click", closeSettings);
  $("#saveSettings").addEventListener("click", saveSettings);
  $("#settingsModal").addEventListener("click", (e) => { if (e.target.id === "settingsModal") closeSettings(); });
  $("#memoryBtn").addEventListener("click", openMemory);
  $("#closeMemory").addEventListener("click", () => ($("#memoryModal").hidden = true));
  $("#memoryModal").addEventListener("click", (e) => { if (e.target.id === "memoryModal") $("#memoryModal").hidden = true; });
  $("#memToggle").addEventListener("click", () => ($("#memForm").hidden = !$("#memForm").hidden));
  $("#memSave").addEventListener("click", saveMemory);
  $("#memSearch").addEventListener("input", debounce(() => renderMemory($("#memSearch").value), 250));
  $("#convList").addEventListener("click", onConvClick);
  $("#themeRow").addEventListener("click", (e) => { if (e.target.dataset.theme) chooseOpt("#themeRow .opt", e.target, "theme"); });
  $("#accentRow").addEventListener("click", (e) => { if (e.target.dataset.accent) chooseOpt("#accentRow .opt", e.target, "accent"); });
}

function debounce(fn, ms) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }
function chooseOpt(sel, el) { $$(sel).forEach((o) => o.classList.toggle("sel", o === el)); }

/* ── mode / settings ──────────────────────────────────────────────────── */
function setMode(mode) {
  state.mode = mode;
  $$(".tab").forEach((t) => t.classList.toggle("active", t.dataset.mode === mode));
  const s = state.settings;
  if (mode === "chat") {
    $("#modeCrumb").textContent = s.chat_name || "saraswati";
    $("#crumbSub").textContent = s.chat_tagline || "advanced chat";
    $("#modeHint").textContent = "chat";
    $("#input").placeholder = `message ${s.chat_name || "saraswati"}…  (/ for commands)`;
  } else if (mode === "hw") {
    $("#modeCrumb").textContent = "vishvakarma";
    $("#crumbSub").textContent = "text prompt → complete hardware build package";
    $("#modeHint").textContent = "hardware";
    $("#input").placeholder = "describe the device to build — e.g. a FLAC audio player, smart sensor, drone…";
  } else {
    $("#modeCrumb").textContent = s.agent_name || "shiva";
    $("#crumbSub").textContent = s.agent_tagline || "autonomous coding agent";
    $("#modeHint").textContent = "code";
    $("#input").placeholder = `what should ${s.agent_name || "shiva"} do?  (/ for commands)`;
  }
  resetTerm();
}

function applySettings() {
  const s = state.settings;
  document.documentElement.dataset.theme = s.theme || "dark";
  document.documentElement.dataset.accent = s.accent || "green";
  $('[data-chat-label]').textContent = s.chat_name || "saraswati";
  $('[data-agent-label]').textContent = s.agent_name || "shiva";
  if (s.workspace) $("#wsInput").placeholder = s.workspace;
  setMode(state.mode);
}
function setLive(live) {
  $("#status").classList.toggle("live", live);
  $("#statusText").textContent = live ? "model connected" : "offline · stub";
}
function resetTerm() {
  $("#term").innerHTML = "";
  state.pending.clear(); state.toolSeq = 0; state.agent = null;
  renderWelcome();
}
function renderWelcome() {
  const s = state.settings;
  const name = state.mode === "chat" ? (s.chat_name || "saraswati") : (s.agent_name || "shiva");
  const w = el("div", "welcome");
  const hwHead = state.mode === "hw";
  w.innerHTML = `<div class="wmark">${hwHead ? "◈ vishvakarma" : `▚ ${name}`}</div>
    <h2>${hwHead ? "describe the device to build" : state.mode === "chat" ? "ask anything" : `what should ${name} do?`}</h2>
    <p>${hwHead
      ? "one prompt becomes a complete, ready-to-build package — architecture & part selection, pin-to-pin wiring, BOM with part search, board layout, and assembly steps."
      : state.mode === "chat"
      ? "a reasoning companion. dense answers, diagrams when they help."
      : "give a goal in the workspace below. I plan, edit, run tests, and repair — streaming every step."}</p>
    <div class="w-suggest"></div>`;
  const grid = $(".w-suggest", w);
  SUGGEST[state.mode].forEach(([t, d]) => {
    const c = el("div", "sug");
    c.innerHTML = `<div class="st">${esc(t)}</div><div class="sd">${esc(d)}</div>`;
    c.addEventListener("click", () => { $("#input").value = t; autosize(); submit(); });
    grid.appendChild(c);
  });
  $("#term").appendChild(w);
}

/* ── composer / palette ───────────────────────────────────────────────── */
function autosize() { const t = $("#input"); t.style.height = "auto"; t.style.height = Math.min(t.scrollHeight, 200) + "px"; }
function onType() {
  const v = $("#input").value;
  if (v.startsWith("/") && !v.slice(1).includes("\n")) {
    const q = v.slice(1).trim().toLowerCase();
    const m = COMMANDS.filter((x) => x.c.slice(1).startsWith(q) || x.d.toLowerCase().includes(q));
    showPalette(m);
  } else $("#palette").hidden = true;
}
let palSel = 0;
function showPalette(items) {
  const p = $("#palette");
  if (!items.length) { p.hidden = true; return; }
  p.hidden = false; palSel = Math.min(palSel, items.length - 1);
  p.innerHTML = "";
  items.forEach((it, i) => {
    const row = el("div", "pal-item" + (i === palSel ? " sel" : ""));
    row.innerHTML = `<span class="pc">${esc(it.c)}</span><span class="pd">${esc(it.d)}</span>`;
    row.addEventListener("mousedown", (e) => { e.preventDefault(); execCommand(it); });
    p.appendChild(row);
  });
  p._items = items;
}
function onKey(e) {
  const p = $("#palette");
  if (!p.hidden && p._items) {
    if (e.key === "ArrowDown") { e.preventDefault(); palSel = Math.min(palSel + 1, p._items.length - 1); showPalette(p._items); return; }
    if (e.key === "ArrowUp") { e.preventDefault(); palSel = Math.max(palSel - 1, 0); showPalette(p._items); return; }
    if (e.key === "Enter") { e.preventDefault(); execCommand(p._items[palSel]); return; }
    if (e.key === "Escape") { p.hidden = true; return; }
  }
  if ((e.metaKey || e.ctrlKey) && e.key === "Enter") { e.preventDefault(); submit(); }
}

function execCommand(it) {
  $("#palette").hidden = true;
  const raw = $("#input").value.trim();
  $("#input").value = ""; autosize();
  it.run(raw);
}

/* ── submit ───────────────────────────────────────────────────────────── */
async function submit() {
  if (state.busy) return;
  const text = $("#input").value.trim();
  if (!text) return;
  // slash command as whole line
  if (text.startsWith("/")) {
    const cmd = text.split(/\s+/)[0];
    const it = COMMANDS.find((x) => x.c === cmd);
    if (it) { execCommand(it); return; }
  }
  const ws = $("#wsInput").value.trim();
  $("#welcome")?.remove();
  addUser(text);
  $("#input").value = ""; autosize();
  state.chatHistory.push({ role: "user", content: text });

  if (state.mode === "hw") {
    const agent = startAgent(); state.agent = agent;
    await hardwareFlow(text, agent);
    loadConversations();
    scrollDown();
    return;
  }

  state.busy = true; $("#goBtn").disabled = true;
  const agent = startAgent(); state.agent = agent;

  const body = state.mode === "chat"
    ? { message: text, history: state.chatHistory }
    : { goal: text, workspace: ws || state.settings.workspace || "." };

  await stream(state.mode === "chat" ? "/api/chat" : "/api/code", body,
    (ev, data) => onEvent(ev, data, agent));

  state.busy = false; $("#goBtn").disabled = false;
  persistConversation(text, agent);
  scrollDown();
}

function onEvent(ev, data, agent) {
  if (ev === "token") { chatToken(agent, data.text); }
  else if (ev === "chat_finished") { setStatus(agent, data.model ? `via ${data.model}` : ""); }
  else if (ev === "run_started") { setStatus(agent, "working…"); }
  else if (ev === "event") { journal(data.kind, data.payload, agent); }
  else if (ev === "run_finished") { finishRun(agent, data); setStatus(agent, ""); }
  else if (ev === "error") { note(agent.body, "✗ " + data.message, "bad"); setStatus(agent, ""); }
  scrollDown();
}

/* ── transcript primitives ────────────────────────────────────────────── */
function addUser(text) {
  const l = el("div", "line u-line");
  l.innerHTML = `<span class="prompt-glyph">›</span><span class="who">${state.mode === "chat" ? "you" : "you"}</span>`;
  const b = el("div", "u-bubble"); b.textContent = text;
  l.appendChild(b);
  $("#term").appendChild(l);
}
function startAgent() {
  const l = el("div", "line a-line");
  const name = state.mode === "chat" ? (state.settings.chat_name || "saraswati") : (state.settings.agent_name || "shiva");
  l.innerHTML = `<span class="a-glyph">◆</span><div class="a-body">
    <div class="a-head"><span class="a-name">${esc(name)}</span><span class="a-status">thinking…</span></div>
    <div class="a-content"></div></div>`;
  $("#term").appendChild(l);
  return { line: l, body: $(".a-content", l), status: $(".a-status", l), buf: "" };
}
function setStatus(a, t) { if (a.status) a.status.textContent = t; }
function note(parent, text, cls) {
  const d = el("div", "feedback-note"); if (cls === "bad") d.style.borderColor = "var(--bad)"; d.textContent = text;
  parent.appendChild(d);
}

/* chat markdown streaming */
function chatToken(a, tok) {
  setStatus(a, "");
  a.buf += tok;
  if (!a.prose) { a.prose = el("div", "prose"); a.body.appendChild(a.prose); }
  renderMarkdown(a.prose, a.buf);
}

/* ── journal → compact tool trace ─────────────────────────────────────── */
function journal(kind, p, a) {
  switch (kind) {
    case "plan.made":
      if (p.repair_round) note(a.body, `↻ repair round ${p.repair_round} — verdict ${p.verdict}; feeding failures back`);
      break;
    case "tool.call": {
      state.toolSeq += 1;
      const seq = state.toolSeq;
      const name = p.tool || "tool";
      const args = p.args || p.arguments || {};
      const row = toolRow(a.body, seq, name, toolDesc(name, args));
      state.pending.set(seq, { row, name });
      break;
    }
    case "tool.result": {
      // match most recent pending row of the same tool name
      const key = matchPending(p.tool);
      const ent = key != null ? state.pending.get(key) : null;
      const ok = p.ok !== false && !(p.data && p.data.denied) && !p.timed_out;
      const out = typeof p.output === "string" ? p.output : JSON.stringify(p.output ?? p.data ?? "", null, 2);
      if (ent) { finishRow(ent.row, ok, p.duration_ms, out.slice(0, 6000)); state.pending.delete(key); }
      break;
    }
    case "edit.applied": {
      // edits also arrive as tool calls; render a dedicated diff trace
      const path = p.path || p.file || "file";
      const row = toolRow(a.body, ++state.toolSeq, "edit", path);
      if (p.diff) setRowDetail(row, diffHtml(p.diff), true);
      finishRow(row, true, null, null);
      break;
    }
    case "edit.failed":
      note(a.body, `✗ edit did not match in ${p.path || "file"} (file drifted) — re-read before retrying`, "bad");
      break;
    case "verify.verdict":
      verdictBlock(a.body, p);
      break;
  }
}
function matchPending(toolName) {
  let best = null;
  for (const [seq, ent] of state.pending) { if (!toolName || ent.name === toolName) best = seq; }
  return best;
}

function toolRow(parent, seq, name, desc) {
  const wrap = el("div", "tool trace");
  wrap.innerHTML = `<div class="tool-row">
      <span class="tw"><span class="spin"></span></span>
      <span class="chev">›</span>
      <span class="tn">${esc(name)}</span>
      <span class="ta">${esc(desc)}</span>
      <span class="td"></span></div>
    <div class="detail collapsed"></div>`;
  const row = $(".tool-row", wrap), detail = $(".detail", wrap);
  row.addEventListener("click", () => { wrap.classList.toggle("open"); detail.classList.toggle("collapsed"); });
  parent.appendChild(wrap);
  return { wrap, row, detail, seq, name };
}
function finishRow(ent, ok, ms, out) {
  const glyph = $(".tw", ent.row);
  glyph.innerHTML = ok ? `<span class="g-ok">✓</span>` : `<span class="g-fail">✗</span>`;
  const td = $(".td", ent.row); td.textContent = ok ? (ms != null ? fmtMs(ms) : "") : "error";
  if (!ok) ent.wrap.classList.add("open"), ent.detail.classList.remove("collapsed");
  if (out != null) { ent.detail.classList.remove("diff"); ent.detail.textContent = out; }
}
function setRowDetail(ent, html, isDiff) {
  ent.detail.innerHTML = html;
  if (isDiff) ent.detail.classList.add("diff");
  ent.wrap.classList.add("open"); ent.detail.classList.remove("collapsed");
  // auto-collapse diffs after a beat to keep the trace tight
  setTimeout(() => { ent.wrap.classList.remove("open"); ent.detail.classList.add("collapsed"); }, 1200);
}
function fmtMs(ms) { return ms < 1000 ? `${Math.round(ms)}ms` : `${(ms / 1000).toFixed(1)}s`; }

function toolDesc(name, a) {
  if (name === "run_shell" || name === "shell") return (a.command || a.cmd || "").slice(0, 90);
  if (name === "str_replace" || name === "write_file" || name === "read_file") return a.path || a.file || "";
  if (name === "search_code") return a.query || "";
  if (name === "finish" || name === "finish_task") return a.summary ? "— " + String(a.summary).slice(0, 60) : "";
  return "";
}

function diffHtml(diff) {
  if (!diff) return "";
  return diff.split("\n").map((ln) => {
    let cls = "";
    if (ln.startsWith("+") && !ln.startsWith("+++")) cls = "add";
    else if (ln.startsWith("-") && !ln.startsWith("---")) cls = "del";
    else if (ln.startsWith("@@")) cls = "hunk";
    return `<span class="dl ${cls}">${esc(ln)}</span>`;
  }).join("");
}

function verdictBlock(parent, p) {
  const v = p.verdict || "skipped";
  const cls = v === "pass" ? "pass" : v === "partial" ? "partial" : "fail";
  const line = el("div", "verdict-line");
  let cov = "";
  if (p.coverage && p.coverage.files) {
    cov = Object.entries(p.coverage.files).map(([f, d]) => {
      const pct = d.statements ? d.covered / d.statements : 1;
      const pctS = Math.round(pct * 100);
      return `<span class="cov">${esc(f)} <span class="bar"><i style="width:${pctS}%"></i></span>${pctS}%${d.missing && d.missing.length ? ` · ln ${d.missing.slice(0, 6).join(",")}` : ""}</span>`;
    }).join("  ");
  }
  line.innerHTML = `<span class="vbadge ${cls}">${v}</span>
    ${p.passed != null ? `<span class="dim">${p.passed} passed${p.failed ? ` · ${p.failed} failed` : ""}</span>` : ""}
    ${cov}`;
  parent.appendChild(line);
  if (p.feedback) note(parent, p.feedback);
  if (p.failures && p.failures.length) note(parent, "failing: " + p.failures.slice(0, 6).join(", "), "bad");
}

function finishRun(a, data) {
  const r = data.report || {}, retro = data.retrospective || {};
  setStatus(a, "");
  const ok = r.ok, verdict = r.verdict || "—";
  const changed = r.changed_files || [];
  const summary = el("div", "prose");
  const bits = [];
  bits.push(`<p><span class="${ok ? "g-ok" : "g-fail"}">${ok ? "✓" : "✗"}</span> <b>${ok ? "done" : "needs work"}</b> — ${esc(r.summary || "")}</p>`);
  bits.push(`<p class="dim micro">steps ${r.steps ?? 0}${r.repair_rounds ? ` · repair ${r.repair_rounds}` : ""} · verdict ${esc(verdict)}${retro.score != null ? ` · quality ${Math.round(retro.score * 100)}%` : ""}${changed.length ? ` · ${changed.length} file(s)` : ""}</p>`);
  summary.innerHTML = bits.join("");
  a.body.appendChild(summary);
  if (changed.length) a.body.appendChild(diagramFromRun(r));
  state.chatHistory.push({ role: "assistant", content: r.summary || `run ${ok ? "succeeded" : "finished"}` });
  loadConversations();
}

/* ── diagrams (flat, editable) ────────────────────────────────────────── */
function parseDiagram(text) {
  const nodes = new Map(), edges = [];
  const norm = (s) => s.replace(/[^a-z0-9]/gi, "_").replace(/^_+|_+$/g, "") || ("n" + nodes.size);
  for (const raw of text.split("\n")) {
    const line = raw.trim(); if (!line) continue;
    const m = line.match(/^(.+?)\s*->\s*(.+?)(?::\s*(.+))?$/);
    if (m) {
      const A = m[1].trim(), B = m[2].trim(), lbl = (m[3] || "").trim();
      const ia = norm(A), ib = norm(B);
      if (!nodes.has(ia)) nodes.set(ia, { id: ia, label: stripTag(A) });
      if (!nodes.has(ib)) nodes.set(ib, { id: ib, label: stripTag(B) });
      edges.push({ from: ia, to: ib, label: lbl });
    } else if (line.includes(":")) {
      const [id, ...rest] = line.split(":");
      nodes.set(norm(id), { id: norm(id), label: rest.join(":").trim() });
    } else { const id = norm(line); nodes.set(id, { id, label: line }); }
  }
  return { nodes: [...nodes.values()], edges };
}
function stripTag(s) { return s.replace(/^[A-Za-z]+:\s*/, ""); }
function layout(g) {
  const depth = {}, adj = {};
  g.nodes.forEach((n) => (adj[n.id] = []));
  g.edges.forEach((e) => adj[e.from]?.push(e.to));
  const visit = (id, d) => { if ((depth[id] ?? -1) >= d) return; depth[id] = d; (adj[id] || []).forEach((t) => visit(t, d + 1)); };
  g.nodes.forEach((n) => visit(n.id, depth[n.id] ?? 0));
  const cols = {};
  g.nodes.forEach((n) => { const c = depth[n.id] || 0; (cols[c] = cols[c] || []).push(n); });
  return cols;
}
function renderDiagram(text, { editable = false } = {}) {
  const g = parseDiagram(text);
  if (g.nodes.length < 2 && !g.edges.length) return "";
  const cols = layout(g);
  const ckeys = Object.keys(cols).map(Number).sort((a, b) => a - b);
  const NW = 150, NH = 40, GX = 70, GY = 22;
  const rows = Math.max(...ckeys.map((c) => cols[c].length));
  const W = ckeys.length * (NW + GX) + 30, H = rows * (NH + GY) + 30;
  const pos = {};
  ckeys.forEach((c) => cols[c].forEach((n, i) => { pos[n.id] = { x: 18 + c * (NW + GX), y: 16 + i * (NH + GY) }; }));
  let svg = `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" xmlns="http://www.w3.org/2000/svg">
    <defs><marker id="ar" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto">
      <path d="M0 0 L10 5 L0 10 z" fill="var(--fg3)"/></marker></defs>`;
  for (const e of g.edges) {
    const a = pos[e.from], b = pos[e.to]; if (!a || !b) continue;
    const x1 = a.x + NW, y1 = a.y + NH / 2, x2 = b.x, y2 = b.y + NH / 2, mx = (x1 + x2) / 2;
    svg += `<path class="edge" d="M${x1} ${y1} C ${mx} ${y1}, ${mx} ${y2}, ${x2 - 5} ${y2}" marker-end="url(#ar)"/>`;
    if (e.label) svg += `<text class="edge-label" x="${mx}" y="${(y1 + y2) / 2 - 5}" text-anchor="middle">${esc(e.label)}</text>`;
  }
  for (const id in pos) {
    const n = g.nodes.find((x) => x.id === id), { x, y } = pos[id];
    svg += `<g class="node-group" data-id="${esc(id)}">
      <rect class="nhit" x="${x}" y="${y}" width="${NW}" height="${NH}" rx="6" fill="var(--bg3)" stroke="var(--border2)"/>
      <text class="node-text" x="${x + NW / 2}" y="${y + NH / 2 + 4}" text-anchor="middle">${esc(trunc(n.label, 20))}</text></g>`;
  }
  return { svg: svg + "</svg>", pos, g, W, H };
}
function diagramBlock(lines, title, editable = false) {
  const wrap = el("div", "diagram");
  if (editable) wrap.classList.add("editing");
  const rendered = renderDiagram(lines.join("\n"));
  wrap.innerHTML = `<div class="dhead"><span>◈ ${esc(title || "diagram")}</span>
    <span class="dtools">${editable ? `<button data-act="done">done</button>` : `<button data-act="edit">edit</button>`}</span></div>
    <div class="dcanvas">${rendered ? rendered.svg : "<span class='dim'>not enough structure</span>"}</div>`;
  const btn = $(".dtools button", wrap);
  btn?.addEventListener("click", () => {
    if (editable) { wrap.classList.remove("editing"); btn.textContent = "edit"; btn.dataset.act = "edit"; enableDrag(wrap, false); }
    else { wrap.classList.add("editing"); btn.textContent = "done"; btn.dataset.act = "done"; enableDrag(wrap, true); }
  });
  if (editable) enableDrag(wrap, true);
  return wrap;
}
function diagramFromRun(r) {
  const lines = [`goal: ${trunc(r.goal || "task", 30)}`,
    ...(r.changed_files || []).slice(0, 6).map((f) => `edit: ${trunc(f, 24)}`),
    `verify: ${r.verdict || "—"}`];
  return diagramBlock(lines, "run flow");
}
// simple drag: translate node groups (visual only; layout re-renders on next edit)
function enableDrag(wrap, on) {
  const svg = $("svg", wrap); if (!svg) return;
  svg.querySelectorAll(".node-group").forEach((g) => {
    const hit = $(".nhit", g);
    if (!on) { hit.onmousedown = null; return; }
    hit.onmousedown = (e) => {
      e.preventDefault();
      const pt = svgPoint(svg, e);
      const rect = $(".nhit", g).getBoundingClientRect();
      const start = svgPoint(svg, { clientX: rect.left, clientY: rect.top });
      const dx0 = parseFloat(g.dataset.dx || 0), dy0 = parseFloat(g.dataset.dy || 0);
      const move = (ev) => {
        const p = svgPoint(svg, ev);
        g.dataset.dx = dx0 + (p.x - pt.x); g.dataset.dy = dy0 + (p.y - pt.y);
        g.setAttribute("transform", `translate(${g.dataset.dx},${g.dataset.dy})`);
      };
      const up = () => { window.removeEventListener("mousemove", move); window.removeEventListener("mouseup", up); };
      window.addEventListener("mousemove", move); window.addEventListener("mouseup", up);
    };
  });
}
function svgPoint(svg, e) {
  const r = svg.getBoundingClientRect();
  const vb = svg.viewBox.baseVal;
  return { x: (e.clientX - r.left) * (vb.width / r.width), y: (e.clientY - r.top) * (vb.height / r.height) };
}

/* ── slash commands: engineering ──────────────────────────────────────── */
function agentBody() { $("#welcome")?.remove(); const a = startAgent(); setStatus(a, ""); return a; }

function runCalc(raw) {
  const a = agentBody();
  const parts = raw.trim().split(/\s+/).slice(1);
  const name = parts[0] || "";
  const args = {};
  for (const kv of parts.slice(1)) {
    const m = kv.match(/^([^=]+)=(.+)$/);
    if (m) { const v = m[2]; const num = parseFloat(v); args[m[1]] = isNaN(num) ? v : num; }
  }
  setStatus(a, "calculating…");
  api("/api/eng/calc", { method: "POST", body: JSON.stringify({ name, args }) }).then((d) => {
    setStatus(a, "");
    const blk = el("div", "eng open");
    if (d.error) { blk.innerHTML = `<div class="ehead"><span class="et">calc</span><span class="es">${esc(d.error)}</span></div>`; }
    else blk.innerHTML = `<div class="ehead"><span class="et">ƒ ${esc(d.name)}</span><span class="es">${esc(d.description || "")}</span></div>
      <div class="ebody"><span class="eval">${fmtNum(d.value)}</span> <span class="earg">${esc(d.result_unit || "")}</span>
      <div class="dim micro" style="margin-top:6px">args: ${esc(parts.slice(1).join("  "))}</div></div>`;
    a.body.appendChild(blk); scrollDown();
  });
}
async function runFormulas(domain) {
  const a = agentBody();
  const d = await api("/api/eng/formulas" + (domain ? `?domain=${encodeURIComponent(domain)}` : ""));
  const blk = el("div", "eng open");
  blk.innerHTML = `<div class="ehead"><span class="et">∑ formulas</span><span class="es">${d.formulas.length} · ${d.domains.length} domains — click a domain</span></div>
    <div class="ebody">
      <div class="dim micro" style="margin-bottom:6px">${d.domains.map((dm) => `<span class="mk" style="cursor:pointer" data-d="${esc(dm)}">${esc(dm)}</span>`).join("  ")}</div>
      ${d.formulas.slice(0, 40).map((f) => `<div class="gline ok"><span class="gi">ƒ</span><span><b>${esc(f.name)}</b> <span class="dim">— ${esc(f.description)} (${esc(f.domain)})</span></span></div>`).join("")}
      ${d.formulas.length > 40 ? `<div class="dim micro">…${d.formulas.length - 40} more — /calc &lt;name&gt;</div>` : ""}
    </div>`;
  blk.querySelectorAll("[data-d]").forEach((s) => s.addEventListener("click", () => { blk.remove(); runFormulas(s.dataset.d); }));
  a.body.appendChild(blk); scrollDown();
}
async function runGates() {
  const a = agentBody();
  const d = await api("/api/eng/gates");
  const blk = el("div", "eng open");
  blk.innerHTML = `<div class="ehead"><span class="et">🛡 gates</span><span class="es">${d.gates.length} — click to evaluate</span></div>
    <div class="ebody">${d.gates.map((g) =>
      `<div class="gline ok" style="cursor:pointer" data-g="${esc(g.key)}"><span class="gi">🛡</span><span><b>${esc(g.name)}</b> <span class="dim">(${esc(g.domain)} · ${g.items}) — ${esc(g.description)}</span></span></div>`).join("")}</div>`;
  blk.querySelectorAll("[data-g]").forEach((r) => r.addEventListener("click", () => { runGateEval(r.dataset.g, a); }));
  a.body.appendChild(blk); scrollDown();
}
function runGateCmd(raw) {
  const key = raw.trim().split(/\s+/)[1];
  if (!key) return runGates();
  const a = agentBody(); runGateEval(key, a);
}
async function runGateEval(key, a) {
  const ws = $("#wsInput").value.trim() || state.settings.workspace || ".";
  setStatus(a, `evaluating ${key}…`);
  const d = await api("/api/eng/gate", { method: "POST", body: JSON.stringify({ gate: key, workspace: ws }) });
  setStatus(a, "");
  const blk = el("div", "eng open");
  if (d.error) { blk.innerHTML = `<div class="ehead"><span class="et">gate</span><span class="es">${esc(d.error)}</span></div>`; a.body.appendChild(blk); return; }
  const vCls = d.verdict === "pass" ? "g-ok" : d.verdict === "gap" ? "g-fail" : "g-warn";
  blk.innerHTML = `<div class="ehead"><span class="et">🛡 ${esc(d.gate_name)}</span>
    <span class="es ${vCls}">${esc(d.verdict)} · ${Math.round((d.coverage || 0) * 100)}%</span></div>
    <div class="ebody">
      ${(d.satisfied || []).map((s) => `<div class="gline ok"><span class="gi">✓</span><span class="dim">${esc(s.id)}${s.detail ? " — " + esc(s.detail) : ""}</span></div>`).join("")}
      ${(d.missing || []).map((m) => `<div class="gline miss"><span class="gi">✗</span><span>${esc(m.id)} <span class="dim">— ${esc(m.text || m.detail || "")}</span></span></div>`).join("")}
    </div>`;
  a.body.appendChild(blk); scrollDown();
}
function insertDiagram() {
  const a = agentBody();
  const sample = ["plan: design", "build: implement", "build -> test", "test -> verify", "verify -> ship"];
  a.body.appendChild(diagramBlock(sample, "diagram (drag nodes, click done)", true));
  scrollDown();
}
function fmtNum(v) { return typeof v === "number" ? (Math.abs(v) >= 1000 || (Math.abs(v) < 0.01 && v !== 0) ? v.toExponential(3) : String(Math.round(v * 1000) / 1000)) : esc(String(v)); }

/* ── hardware engineering flow (vishvakarma) ──────────────────────────── */
async function hardwareFlow(prompt, a) {
  setStatus(a, "clarifying…");
  const cl = await api("/api/hw/clarify", { method: "POST", body: JSON.stringify({ prompt }) });
  state.hwAnswers = {};
  // wizard
  if (cl.questions && cl.questions.length) {
    const wb = el("div", "hw-wizard");
    wb.innerHTML = `<div class="hw-whead">◆ a few choices to size the design — type is detected: <b>${esc(cl.type?.type || "")}</b></div>
      <div class="hw-qlist"></div>`;
    const qlist = $(".hw-qlist", wb);
    cl.questions.forEach((q) => {
      const qel = el("div", "hw-q");
      qel.innerHTML = `<div class="hw-qt">${esc(q.q)}</div><div class="hw-opts"></div>`;
      const opts = $(".hw-opts", qel);
      q.options.forEach((opt, i) => {
        const b = el("button", "hw-opt" + (i === 0 ? " picked" : ""));
        b.textContent = opt;
        b.addEventListener("click", () => {
          $$(".hw-opt", opts).forEach((x) => x.classList.remove("picked"));
          b.classList.add("picked");
          state.hwAnswers[q.id] = opt;
        });
        opts.appendChild(b);
        if (i === 0) state.hwAnswers[q.id] = opt;
      });
      qlist.appendChild(qel);
    });
    const go = el("button", "btn-go wide"); go.textContent = "generate build package"; go.style.marginTop = "10px";
    go.addEventListener("click", async () => { wb.remove(); await runHwPlan(prompt, a); });
    wb.appendChild(go);
    a.body.appendChild(wb);
    setStatus(a, "");
    scrollDown();
    return;
  }
  await runHwPlan(prompt, a);
}

async function runHwPlan(prompt, a) {
  setStatus(a, "selecting architecture & parts…");
  const plan = await api("/api/hw/plan", {
    method: "POST", body: JSON.stringify({ prompt, answers: state.hwAnswers }),
  });
  setStatus(a, "");
  if (plan.error) { note(a.body, "✗ " + plan.error, "bad"); return; }
  a.body.appendChild(hwHeader(plan));
  a.body.appendChild(archBlock(plan));
  a.body.appendChild(wiringBlock(plan));
  a.body.appendChild(bomBlock(plan));
  a.body.appendChild(boardBlock(plan));
  a.body.appendChild(stepsBlock("assembly", plan.assembly, "🔩"));
  a.body.appendChild(stepsBlock("bring-up & test", plan.tests, "✓"));
  a.body.appendChild(certBlock(plan));
  if (plan.notes && plan.notes.length) a.body.appendChild(notesBlock(plan.notes));
  // markdown export
  const bar = el("div", "hw-export");
  const md = hwMarkdown(plan);
  const btn = el("button", "btn-line"); btn.textContent = "⬇ download plan.md";
  btn.addEventListener("click", () => {
    const blob = new Blob([md], { type: "text/markdown" });
    const u = URL.createObjectURL(blob);
    const link = document.createElement("a"); link.href = u; link.download = slug(plan.title) + "-build-plan.md"; link.click();
    URL.revokeObjectURL(u);
  });
  bar.appendChild(btn);
  a.body.appendChild(bar);
}
function slug(s){return s.toLowerCase().replace(/[^a-z0-9]+/g,"-").replace(/^-|-$/g,"")||"hardware-plan";}

function hwHeader(plan) {
  const b = el("div", "hw-title");
  b.innerHTML = `<div class="hw-t">${esc(plan.title)}</div>
    <div class="hw-sub">platform <b>${esc(plan.platform.name)}</b> · ${plan.bom.length} BOM items · ${plan.wiring.length} connections
    ${plan.power?.estimated_peak_mA ? ` · ≈${plan.power.estimated_peak_mA}mA peak` : ""}
    ${plan.power?.estimated_runtime_min ? ` · ~${plan.power.estimated_runtime_min}min battery` : ""}</div>`;
  return b;
}
function archBlock(plan) {
  const lines = [];
  plan.architecture.forEach((blk, i) => {
    const id = `b${i}`;
    lines.push(`${id}: ${blk.block}`);
    if (i < plan.architecture.length - 1) lines.push(`b${i} -> b${i + 1}: ${blk.via}`);
  });
  return diagramBlock(lines, "system architecture", false);
}

/* wiring: grouped bus table + pin map */
function wiringBlock(plan) {
  const b = el("div", "eng open");
  const conns = plan.wiring;
  // group by protocol
  const groups = {};
  conns.forEach((c) => (groups[c.protocol] = groups[c.protocol] || []).push(c));
  let rows = "";
  const order = ["i2s", "i2c", "spi", "uart", "pwm", "gpio", "power"];
  Object.keys(groups).sort((x, y) => order.indexOf(x) - order.indexOf(y)).forEach((proto) => {
    rows += `<tr class="proto-row"><td colspan="3">${esc(proto.toUpperCase())}</td></tr>`;
    groups[proto].forEach((c) => {
      rows += `<tr><td class="mono">${esc(c.source_pin)}</td><td class="mono sig">${esc(c.signal)}</td>
        <td class="mono">${esc(c.target)} · ${esc(c.target_pin)}</td></tr>`;
    });
  });
  b.innerHTML = `<div class="ehead"><span class="et">⎇ wiring & pin map</span>
    <span class="es">${esc(plan.platform.mcu)} — pin-to-pin</span></div>
    <div class="ebody no-pad">
      <table class="wtable">
        <thead><tr><th>${esc(plan.platform.mcu)}</th><th>signal</th><th>peripheral</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
      ${(plan.rails || []).map(r => `<div class="rail">⚡ ${esc(r.rail)} — ${esc(r.note)}</div>`).join("")}
    </div>`;
  return b;
}

function bomBlock(plan) {
  const b = el("div", "eng open");
  b.innerHTML = `<div class="ehead"><span class="et">▤ bill of materials</span>
    <span class="es">${plan.bom.length} line items — links open supplier search</span></div>
    <div class="ebody no-pad"><table class="wtable bom">
      <thead><tr><th>ref</th><th>qty</th><th>part</th><th></th></tr></thead><tbody>
      ${plan.bom.map(it => `<tr>
        <td class="mono">${esc(it.ref)}</td><td class="mono">${it.qty}</td>
        <td>${esc(it.name)}${it.note ? ` <span class="dim">— ${esc(it.note)}</span>` : ""}</td>
        <td class="r"><a href="${esc(it.search_url)}" target="_blank" rel="noopener" class="bomlink">find ↗</a></td></tr>`).join("")}
      </tbody></table></div>`;
  return b;
}

/* board / CAD: scaled SVG placement */
function boardBlock(plan) {
  const bd = plan.board; const sx = 4.2;
  const W = bd.width_mm * sx, H = bd.height_mm * sx;
  let parts = "";
  bd.parts.forEach((p, i) => {
    const x = p.x * sx, y = p.y * sx, w = p.w * sx, h = p.h * sx;
    parts += `<g><rect x="${x}" y="${y}" width="${w}" height="${h}" rx="5"
      class="${i === 0 ? "b-mcu" : "b-part"}"/>
      <text x="${x + w / 2}" y="${y + h / 2 - 2}" text-anchor="middle" class="b-label">${esc(p.label)}</text>
      <text x="${x + w / 2}" y="${y + h / 2 + 11}" text-anchor="middle" class="b-sub">${esc(p.sub || "")}</text></g>`;
  });
  const holes = bd.mount_holes.map(m => `<circle cx="${m.x * sx + 4}" cy="${m.y * sx + 4}" r="3.2" class="b-hole"/>`).join("");
  const b = el("div", "eng open");
  b.innerHTML = `<div class="ehead"><span class="et">▢ board layout / placement</span>
    <span class="es">${bd.width_mm}×${bd.height_mm}mm · ${esc(bd.shape)} (concept placement)</span></div>
    <div class="ebody"><svg viewBox="0 0 ${W + 10} ${H + 10}" width="100%" style="max-width:560px" xmlns="http://www.w3.org/2000/svg">
      <rect x="5" y="5" width="${W}" height="${H}" rx="10" class="b-board"/>
      ${holes}${parts}</svg></div>`;
  return b;
}

function stepsBlock(title, steps, glyph) {
  const b = el("div", "eng");
  b.innerHTML = `<div class="ehead"><span class="et">${glyph} ${esc(title)}</span><span class="es">${steps.length} steps</span></div>
    <div class="ebody">${steps.map((s, i) => `<div class="step"><span class="step-n">${i + 1}</span><span>${esc(s)}</span></div>`).join("")}</div>`;
  $(".ehead", b).addEventListener("click", () => b.classList.toggle("collapsed"));
  return b;
}
function certBlock(plan) {
  const b = el("div", "eng");
  b.innerHTML = `<div class="ehead"><span class="et">🛡 certification & safety</span><span class="es">external gates flagged honestly</span></div>
    <div class="ebody">${plan.certifications.map(c => {
      const cls = c.status === "external" ? "g-fail" : "g-warn";
      return `<div class="gline"><span class="gi ${cls}">${c.status === "external" ? "⚑" : "⚠"}</span>
        <span><b>${esc(c.gate)}</b> <span class="dim">[${esc(c.status)}] — ${esc(c.note)}</span></span></div>`;
    }).join("")}</div>`;
  $(".ehead", b).addEventListener("click", () => b.classList.toggle("collapsed"));
  return b;
}
function notesBlock(notes) {
  const b = el("div", "eng open");
  b.innerHTML = `<div class="ehead"><span class="et">◆ design rationale</span></div>
    <div class="ebody">${notes.map(n => `<div class="step"><span class="step-n">•</span><span>${esc(n)}</span></div>`).join("")}</div>`;
  return b;
}

function hwMarkdown(plan) {
  const L = [];
  L.push(`# ${plan.title}`, "");
  L.push(`**Platform:** ${plan.platform.name} (${plan.platform.arch})  `);
  L.push(`**Est. peak current:** ${plan.power.estimated_peak_mA} mA ${plan.power.note ? `(${plan.power.note})` : ""}  `);
  if (plan.power.battery) L.push(`**Battery:** ${plan.power.battery} — est. ${plan.power.estimated_runtime_min} min  `);
  L.push("", "## Architecture", "");
  plan.architecture.forEach(b => L.push(`- **${b.block}** — ${b.role} _(via ${b.via})_`));
  L.push("", "## Wiring / pin map", "", `| ${plan.platform.mcu} pin | signal | peripheral |`, "|---|---|---|");
  plan.wiring.forEach(c => L.push(`| ${c.source_pin} | ${c.signal} | ${c.target} · ${c.target_pin} |`));
  L.push("", "## Bill of materials", "", "| ref | qty | part | search |", "|---|---|---|---|");
  plan.bom.forEach(it => L.push(`| ${it.ref} | ${it.qty} | ${it.name} | ${it.search_url} |`));
  L.push("", "## Assembly", "");
  plan.assembly.forEach((s, i) => L.push(`${i + 1}. ${s}`));
  L.push("", "## Bring-up & test", "");
  plan.tests.forEach(s => L.push(`- [ ] ${s}`));
  L.push("", "## Certification & safety", "");
  plan.certifications.forEach(c => L.push(`- **${c.gate}** [${c.status}] — ${c.note}`));
  if (plan.notes && plan.notes.length) { L.push("", "## Design rationale", ""); plan.notes.forEach(n => L.push(`- ${n}`)); }
  L.push("", "_Generated by Vishvakarma. Verify all pinouts against current datasheets; links are supplier searches, not endorsements._");
  return L.join("\n");
}

/* ── conversations ────────────────────────────────────────────────────── */
async function loadConversations() {
  try {
    const d = await api("/api/conversations");
    const list = $("#convList");
    list.innerHTML = (d.conversations || []).length
      ? d.conversations.map((c) => `<div class="hist-item ${c.id === state.convId ? "active" : ""}" data-id="${esc(c.id)}"><span class="mk">${c.mode === "chat" ? "◆" : "▚"}</span>${esc(trunc(c.title, 26))}</div>`).join("")
      : `<div class="dim empty">none yet</div>`;
  } catch {}
}
async function onConvClick(e) {
  const item = e.target.closest(".hist-item"); if (!item) return;
  const conv = await api(`/api/conversation?id=${encodeURIComponent(item.dataset.id)}`);
  if (!conv || !conv.messages) return;
  state.convId = conv.id; state.mode = conv.mode || "chat"; state.chatHistory = [];
  setMode(state.mode);
  $("#term").innerHTML = "";
  for (const m of conv.messages) {
    if (m.role === "user") { addUser(m.content); state.chatHistory.push(m); }
    else {
      const a = startAgent(); setStatus(a, "");
      if (m.content) renderMarkdown(ensureProse(a), m.content);
      (m.diagrams || []).forEach((dg) => a.body.appendChild(diagramBlock(dg.lines, dg.title || "diagram", false)));
      state.chatHistory.push({ role: "assistant", content: m.content || "" });
    }
  }
  $(".side").classList.remove("open");
  loadConversations(); scrollDown();
}
function ensureProse(a) { if (!a.prose) { a.prose = el("div", "prose"); a.body.appendChild(a.prose); } return a.prose; }
function persistConversation(firstUser, agent) {
  const messages = [];
  // reconstruct from transcript for faithful history
  $$("#term .line").forEach((l) => {
    if (l.classList.contains("u-line")) messages.push({ role: "user", content: $(".u-bubble", l).textContent });
    else {
      const prose = $(".prose", l);
      const content = prose ? prose.textContent.trim() : "";
      const diagrams = [];
      $$(".diagram", l).forEach((dg) => {
        // diagrams are generated; store a placeholder marker reconstructed loosely
        diagrams.push({ title: $(".dhead span", dg)?.textContent || "diagram", lines: [] });
      });
      if (content || diagrams.length) messages.push({ role: "assistant", content, diagrams });
    }
  });
  const payload = { id: state.convId, mode: state.mode, title: trunc(firstUser, 48), messages };
  api("/api/conversation/save", { method: "POST", body: JSON.stringify(payload) })
    .then((d) => { if (d.id) { state.convId = d.id; loadConversations(); } });
}

/* ── settings modal ───────────────────────────────────────────────────── */
function openSettings() {
  const s = state.settings;
  $("#setAgentName").value = s.agent_name || ""; $("#setChatName").value = s.chat_name || "";
  $("#setAgentTag").value = s.agent_tagline || ""; $("#setChatTag").value = s.chat_tagline || "";
  $("#setChatSystem").value = s.chat_system || "";
  $("#setProvider").value = s.model_provider || ""; $("#setModel").value = s.model || "";
  $$("#themeRow .opt").forEach((o) => o.classList.toggle("sel", o.dataset.theme === (s.theme || "dark")));
  $$("#accentRow .opt").forEach((o) => o.classList.toggle("sel", o.dataset.accent === (s.accent || "green")));
  $("#settingsModal").hidden = false;
}
function closeSettings() { $("#settingsModal").hidden = true; }
async function saveSettings() {
  const payload = {
    agent_name: $("#setAgentName").value.trim() || "shiva",
    chat_name: $("#setChatName").value.trim() || "saraswati",
    agent_tagline: $("#setAgentTag").value.trim(),
    chat_tagline: $("#setChatTag").value.trim(),
    chat_system: $("#setChatSystem").value.trim(),
    theme: $("#themeRow .opt.sel")?.dataset.theme || "dark",
    accent: $("#accentRow .opt.sel")?.dataset.accent || "green",
    model_provider: $("#setProvider").value.trim(),
    model: $("#setModel").value.trim(),
  };
  state.settings = await api("/api/settings", { method: "POST", body: JSON.stringify(payload) });
  applySettings(); setLive(!!payload.model_provider && payload.model_provider !== "stub"); closeSettings();
}

/* ── memory explorer ──────────────────────────────────────────────────── */
async function openMemory() {
  $("#memoryModal").hidden = false; renderMemory("");
}
async function renderMemory(q) {
  const path = q ? `/api/memory/search?q=${encodeURIComponent(q)}` : "/api/memory";
  const d = await api(path);
  const recs = d.results || d.records || [];
  const stats = d.stats;
  $("#memStats").innerHTML = stats
    ? Object.entries(stats.by_kind || {}).map(([k, n]) => `<span class="chip">${k} ${n}</span>`).join("") + `<span class="chip">${stats.records} total</span>`
    : "";
  $("#memList").innerHTML = recs.length ? recs.map(memCard).join("") : `<div class="dim empty">nothing stored yet</div>`;
}
function memCard(r) {
  let body = "";
  if (r.kind === "datasheet") {
    const p = r.data?.parameters || {};
    body = Object.entries(p).slice(0, 8).map(([k, v]) => `${k}=${v.value}${v.unit || ""}`).join(", ");
  } else body = r.text || "";
  return `<div class="mem-card"><div class="mt"><span class="mk-tag ${esc(r.kind)}">${esc(r.kind)}</span><span class="mk">${esc(r.key.replace(/^(ds|fact|dec|lesson):[^:]*:/, "").replace(/^ds:/, ""))}</span></div>
    <div class="mtext">${esc(trunc(body, 160))}</div>
    ${r.source ? `<div class="msrc">src: ${esc(r.source)} · conf ${Math.round((r.confidence || 0) * 100)}%</div>` : ""}</div>`;
}
async function saveMemory() {
  const kind = $("#memKind").value, key = $("#memKey").value.trim(), detail = $("#memDetail").value;
  const domain = $("#memDomain").value.trim(), source = $("#memSource").value.trim() || "studio ui";
  if (!key) return;
  let res;
  if (kind === "datasheet") {
    const parameters = {};
    detail.split("\n").forEach((ln) => { const m = ln.match(/^\s*([^=]+)=(.+)$/); if (m) { const [val, unit] = m[2].trim().split(/\s+/); parameters[m[1].trim()] = { value: isNaN(parseFloat(val)) ? val : parseFloat(val), unit: unit || "" }; } });
    res = await api("/api/memory/capture", { method: "POST", body: JSON.stringify({ part: key, parameters, domain, source }) });
  } else if (kind === "fact") {
    const m = detail.match(/[-+0-9.eE]+/);
    res = await api("/api/memory/fact", { method: "POST", body: JSON.stringify({ name: key, value: m ? parseFloat(m[0]) : detail, unit: (detail.split(/\s+/)[1] || ""), domain, note: detail, source }) });
  } else {
    res = await api("/api/memory/decision", { method: "POST", body: JSON.stringify({ topic: key, choice: detail.split(/\n|because|why|rationale/i)[0], rationale: detail, domain, source }) });
  }
  $("#memForm").hidden = true; $("#memDetail").value = ""; $("#memKey").value = "";
  renderMemory($("#memSearch").value);
}

/* ── markdown (small, safe) ───────────────────────────────────────────── */
function renderMarkdown(container, md) {
  // pull out ```diagram fences into diagram blocks; render the rest inline
  container.innerHTML = "";
  const parts = md.split(/```(\w*)\n?([\s\S]*?)```/g);
  let i = 0;
  while (i < parts.length) {
    const prose = parts[i++];
    if (prose) container.appendChild(mdNode(prose));
    if (i < parts.length) {
      const lang = parts[i++], code = parts[i++];
      if (lang === "diagram") {
        const blk = diagramBlock(code.split("\n"), "diagram", false);
        container.appendChild(blk);
      } else {
        const pre = document.createElement("pre"); const c = document.createElement("code");
        c.textContent = code.replace(/^\n|\n$/g, ""); pre.appendChild(c); container.appendChild(pre);
      }
    }
  }
}
function mdNode(md) {
  const div = document.createElement("div"); let html = "", list = null;
  const flush = () => { if (list) { html += `</${list}>`; list = null; } };
  for (let ln of md.split("\n")) {
    if (/^\s*[-*]\s+/.test(ln)) { if (list !== "ul") { flush(); html += "<ul>"; list = "ul"; } html += `<li>${inline(ln.replace(/^\s*[-*]\s+/, ""))}</li>`; }
    else if (/^\s*\d+\.\s+/.test(ln)) { if (list !== "ol") { flush(); html += "<ol>"; list = "ol"; } html += `<li>${inline(ln.replace(/^\s*\d+\.\s+/, ""))}</li>`; }
    else if (/^###\s+/.test(ln)) { flush(); html += `<h3>${inline(ln.replace(/^###\s+/, ""))}</h3>`; }
    else if (/^##\s+/.test(ln)) { flush(); html += `<h2>${inline(ln.replace(/^##\s+/, ""))}</h2>`; }
    else if (/^#\s+/.test(ln)) { flush(); html += `<h1>${inline(ln.replace(/^#\s+/, ""))}</h1>`; }
    else if (ln.trim() === "") flush();
    else { flush(); html += `<p>${inline(ln)}</p>`; }
  }
  flush(); div.innerHTML = html; return div;
}
function inline(s) {
  return esc(s).replace(/`([^`]+)`/g, "<code>$1</code>").replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>").replace(/\*([^*]+)\*/g, "<em>$1</em>");
}

/* ── utilities ────────────────────────────────────────────────────────── */
function el(tag, cls) { const e = document.createElement(tag); if (cls) e.className = cls; return e; }
function esc(s) { return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }
function trunc(s, n) { s = String(s || ""); return s.length > n ? s.slice(0, n - 1) + "…" : s; }
function scrollDown() { const t = $("#term"); t.scrollTop = t.scrollHeight; }
async function api(path, opts) { const r = await fetch(path, opts); return r.json(); }
async function stream(path, body, onEvent) {
  let res;
  try { res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }); }
  catch { onEvent("error", { message: "connection failed" }); return; }
  if (!res.ok || !res.body) { onEvent("error", { message: "stream failed" }); return; }
  const reader = res.body.getReader(), dec = new TextDecoder();
  let buf = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let idx;
    while ((idx = buf.indexOf("\n\n")) >= 0) {
      const chunk = buf.slice(0, idx); buf = buf.slice(idx + 2);
      let ev = "message", data = {};
      chunk.split("\n").forEach((ln) => {
        if (ln.startsWith("event:")) ev = ln.slice(6).trim();
        else if (ln.startsWith("data:")) { try { data = JSON.parse(ln.slice(5).trim()); } catch {} }
      });
      onEvent(ev, data);
    }
  }
}

boot();
