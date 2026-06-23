// JARVIS HUD client
const $ = (id) => document.getElementById(id);
const player = $("player");
let voiceOn = true;
let pollSeconds = 20;
let lastState = null;

// ---------- voice ----------
async function speak(text) {
  if (!voiceOn || !text) return;
  try {
    const r = await fetch("/api/speak", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    if (r.status === 204) return;
    const blob = await r.blob();
    player.src = URL.createObjectURL(blob);
    await player.play().catch(() => {});
  } catch (e) { /* voice is best-effort */ }
}

// ---------- helpers ----------
function led(status) {
  const map = { online: "on", configured: "warn", "detected (repo)": "warn",
    error: "err", "not detected": "off" };
  return map[status] || (status ? "on" : "off");
}
function fmtAgo(ts) {
  const s = (Date.now() / 1000) - ts;
  if (s < 3600) return Math.round(s / 60) + "m ago";
  if (s < 86400) return Math.round(s / 3600) + "h ago";
  return Math.round(s / 86400) + "d ago";
}
function setGauge(id, pct) {
  const el = $(id);
  const fill = el.querySelector(".fill");
  const C = 2 * Math.PI * 50;
  fill.style.strokeDashoffset = C * (1 - (pct || 0) / 100);
  el.classList.toggle("warn", pct >= 70 && pct < 88);
  el.classList.toggle("crit", pct >= 88);
}

// ---------- renderers ----------
function renderSystem(sys) {
  if (!sys || !sys.ok) return;
  setGauge("g-cpu", sys.cpu_pct); $("cpuVal").textContent = Math.round(sys.cpu_pct) + "%";
  setGauge("g-mem", sys.mem_pct); $("memVal").textContent = Math.round(sys.mem_pct) + "%";
  setGauge("g-disk", sys.disk_pct); $("diskVal").textContent = Math.round(sys.disk_pct) + "%";
  $("uptime").textContent = sys.uptime_h + " h";
  $("memDetail").textContent = `${sys.mem_used_gb} / ${sys.mem_total_gb} GB`;
  $("diskDetail").textContent = `${sys.disk_free_gb} GB`;
  $("cores").textContent = sys.cpu_cores;
}

function renderList(elId, items) {
  $(elId).innerHTML = items.map((it) => {
    const on = it.present;
    const ver = it.version ? `<span class="ver">${it.version}</span>`
      : `<span class="ver miss">not installed</span>`;
    return `<div class="row clickable" data-kind="tool" data-name="${it.name}">
      <span class="led ${on ? "on" : "off"}"></span>
      <span class="name">${it.label || it.name}</span>${ver}<span class="drill">details ›</span></div>`;
  }).join("");
}

function renderConnectors(conn) {
  const c = conn.claude;
  let html = `<div class="row clickable" data-kind="conn" data-name="claude">
    <span class="led ${led(c.status)}"></span>
    <span class="name">Claude Code</span>
    <span class="ver">${c.version ? "v" + c.version : c.status}</span><span class="drill">details ›</span></div>`;
  if (conn.mcp_servers && conn.mcp_servers.length) {
    html += conn.mcp_servers.map((m) =>
      `<div class="row sub clickable" data-kind="mcp" data-name="${m.name}">
        <span class="led ${led(m.status)}"></span>
        <span class="name">mcp · ${m.name}</span>
        <span class="ver">${m.status}</span><span class="drill">›</span></div>`).join("");
  }
  const h = conn.hermes;
  html += `<div class="row clickable" data-kind="conn" data-name="hermes">
    <span class="led ${led(h.status)}"></span>
    <span class="name">Hermes${h.branch ? " · " + h.branch : ""}</span>
    <span class="ver ${h.present ? "" : "miss"}">${h.present ? (h.version || h.status) : h.status}</span>
    <span class="drill">details ›</span></div>`;
  if (h.skills && h.skills.length) {
    html += h.skills.map((s) =>
      `<div class="row sub"><span class="led on"></span>
        <span class="name">skill · ${s.name}</span>
        <span class="ver">${s.category}</span></div>`).join("");
  }
  const oc = conn.openclaw;
  html += `<div class="row clickable" data-kind="conn" data-name="openclaw">
    <span class="led ${led(oc.status)}"></span>
    <span class="name">${oc.name}</span>
    <span class="ver ${oc.present ? "" : "miss"}">${oc.version ? "v" + oc.version : oc.status}</span>
    <span class="drill">details ›</span></div>`;
  $("connectors").innerHTML = html;
}

function renderObsidian(ob) {
  if (!ob.available) {
    $("obsidian").innerHTML = `<div class="row"><span class="led off"></span>
      <span class="name">vault not found</span></div>
      <div class="muted">${ob.vault || "set [obsidian].vault in config.toml"}</div>`;
    return;
  }
  let html = `<div class="row"><span class="led on"></span><span class="name">vault linked</span>
    <span class="badge green">${ob.notes} notes</span></div>
    <div class="muted" style="margin:4px 0">${ob.vault}</div>`;
  html += ob.recent.map((n) =>
    `<div class="row sub"><span class="name">${n.title}</span>
      <span class="ver">${fmtAgo(n.mtime)}</span></div>`).join("");
  $("obsidian").innerHTML = html;
}

function renderProjects(projs) {
  $("projects").innerHTML = projs.map((p) => {
    const branch = p.git
      ? `<span class="branch ${p.dirty ? "dirty" : ""}">${p.branch || "git"}${p.dirty ? " ●" : ""}</span>`
      : `<span class="branch">—</span>`;
    return `<div class="proj" data-kind="project" data-path="${encodeURIComponent(p.path)}" title="${p.root}">
      <span class="led ${p.has_pyproject ? "on" : "off"}"></span>
      <span class="name">${p.name}</span>${branch}</div>`;
  }).join("");
}

function renderUpdates(u) {
  const blocks = [];
  let total = 0;
  for (const key of ["pip", "npm", "uv", "claude"]) {
    const g = u[key]; if (!g) continue;
    total += g.count || 0;
    const badge = g.count
      ? `<span class="badge amber">${g.count}</span>`
      : `<span class="badge green">ok</span>`;
    blocks.push(`<div class="update-line"><span class="led ${g.count ? "warn" : "on"}"></span>
      <span class="name">${g.label}</span>${badge}
      <button class="mini-btn apply" data-target="${key}">update</button></div>`);
    if (g.items && g.items.length) {
      blocks.push(`<div class="upd-detail muted">` +
        g.items.slice(0, 6).map((i) => `${i.name} ${i.current}→${i.latest}`).join(" · ") +
        (g.items.length > 6 ? ` +${g.items.length - 6} more` : "") + `</div>`);
    }
  }
  // winget dev toolchain (Python + tools) — per-item upgrade buttons
  const w = u.winget;
  if (w) {
    total += w.count || 0;
    blocks.push(`<div class="update-line"><span class="led ${w.count ? "warn" : "on"}"></span>
      <span class="name">${w.label}</span>
      <span class="badge ${w.count ? "amber" : "green"}">${w.count || "ok"}</span></div>`);
    (w.items || []).forEach((i) => {
      blocks.push(`<div class="update-line" style="padding-left:16px">
        <span class="name">${i.label || i.name}</span>
        <span class="muted">${i.current}→${i.latest}</span>
        <button class="mini-btn winapply" data-id="${i.id}">update</button></div>`);
    });
    if (w.others > 0) {
      blocks.push(`<div class="row clickable" data-kind="winget-all" style="margin-top:6px">
        <span class="name muted">⊕ ${w.others} other system updates (apps, drivers…)</span>
        <span class="drill">view ›</span></div>`);
    }
  }
  $("updates").innerHTML = blocks.join("") || `<div class="muted">Up to date.</div>`;
  $("updates").querySelectorAll(".apply").forEach((b) =>
    b.addEventListener("click", () => applyUpdate(b.dataset.target, b)));
  $("updates").querySelectorAll(".winapply").forEach((b) =>
    b.addEventListener("click", () => applyToolUpgrade(b.dataset.id, b)));
  $("statusPill").title = total ? `${total} updates pending` : "all current";
}

async function applyToolUpgrade(id, btn) {
  btn.disabled = true; btn.textContent = "…";
  printMsg("jarvis", `Upgrading ${id} via winget, sir. This may take a minute.`);
  try {
    const r = await fetch("/api/tools/upgrade", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id }),
    });
    const res = await r.json();
    printMsg("jarvis", res.ok ? `${id} upgraded.` : `${id} upgrade failed: ${res.error || (res.output || "").slice(-200)}`);
    if (res.ok) speak(`${id.split(".").pop()} upgraded, sir.`);
  } catch (e) { printMsg("jarvis", `${id} upgrade error.`); }
  setTimeout(() => checkUpdates(true), 800);
}

// ---------- data flow ----------
async function loadState(refresh = false) {
  try {
    const r = await fetch("/api/state" + (refresh ? "?refresh=true" : ""));
    const s = await r.json();
    lastState = s;
    $("version").textContent = "v" + s.version;
    renderSystem(s.inventory.system);
    renderList("runtimes", s.inventory.runtimes.filter((x) => x.present));
    renderList("tools", s.inventory.dev_tools);
    renderConnectors(s.connectors);
    renderObsidian(s.obsidian);
    renderProjects(s.projects);
    setOnline(true);
  } catch (e) { setOnline(false); }
}

function setOnline(ok) {
  const pill = $("statusPill");
  pill.classList.toggle("offline", !ok);
  pill.childNodes[1].nodeValue = ok ? " online" : " offline";
}

async function checkUpdates(refresh = true) {
  $("updates").innerHTML = `<div class="muted">scanning…</div>`;
  const r = await fetch("/api/updates?refresh=" + refresh);
  renderUpdates(await r.json());
}

async function applyUpdate(target, btn) {
  btn.disabled = true; btn.textContent = "…";
  printMsg("jarvis", `Applying update for ${target}, sir. Standing by.`);
  const r = await fetch("/api/update", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ target }),
  });
  const res = await r.json();
  printMsg("jarvis", res.ok
    ? `${target} update complete.`
    : `${target} update failed: ${res.error || res.output || "unknown"}`);
  if (res.ok) speak(`${target} updated, sir.`);
  setTimeout(() => checkUpdates(true), 800);
}

// ---------- console / ask ----------
function printMsg(who, text, src) {
  const io = $("consoleIO");
  const div = document.createElement("div");
  div.className = "msg " + who;
  div.textContent = text;
  if (src) { const s = document.createElement("span"); s.className = "src"; s.textContent = "  [" + src + "]"; div.appendChild(s); }
  io.appendChild(div); io.scrollTop = io.scrollHeight;
  return div;
}

async function ask(question) {
  printMsg("you", question);
  const thinking = printMsg("jarvis", "thinking…");
  thinking.classList.add("thinking");
  try {
    const r = await fetch("/api/ask", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question }),
    });
    const res = await r.json();
    thinking.classList.remove("thinking");
    thinking.textContent = res.answer;
    if (res.source) { const s = document.createElement("span"); s.className = "src"; s.textContent = "  [" + res.source + "]"; thinking.appendChild(s); }
    if (res.ok) speak(res.answer);
  } catch (e) {
    thinking.classList.remove("thinking");
    thinking.textContent = "Connection error, sir.";
  }
}

// ---------- drill-down modal ----------
function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}
function kv(pairs) {
  return `<div class="kv">` + pairs.filter(([, v]) => v != null && v !== "")
    .map(([k, v]) => `<span>${esc(k)}</span><b>${esc(v)}</b>`).join("") + `</div>`;
}
function openModal(title, sub, bodyHtml) {
  $("modalTitle").textContent = title;
  $("modalSub").textContent = sub || "";
  $("modalBody").innerHTML = bodyHtml;
  $("modalBackdrop").classList.add("open");
}
function closeModal() { $("modalBackdrop").classList.remove("open"); }

function handleDrill(ds) {
  if (!lastState) return;
  if (ds.kind === "tool") {
    const inv = lastState.inventory;
    const it = [...inv.runtimes, ...inv.dev_tools].find((x) => x.name === ds.name);
    if (!it) return;
    let upd = "";
    const w = (lastState.updates && lastState.updates.winget) || null;
    const hit = w && (w.items || []).find((i) => (i.label || i.name).toLowerCase().includes((it.label || "").toLowerCase()));
    if (hit) upd = `<div class="section-title">Update available</div>
      <div class="chip-row"><span class="chip">${esc(hit.current)} → ${esc(hit.latest)}</span></div>
      <button class="act-btn" onclick="applyToolUpgrade('${esc(hit.id)}', this)">update via winget</button>`;
    openModal(it.label || it.name, it.present ? "installed" : "not installed", kv([
      ["Name", it.label || it.name], ["Version", it.version || "—"],
      ["Status", it.present ? "present" : "missing"], ["Path", it.path || "—"],
    ]) + upd);
  } else if (ds.kind === "mcp") {
    const m = lastState.connectors.mcp_servers.find((x) => x.name === ds.name);
    if (!m) return;
    const cmd = m.command ? `${m.command} ${(m.args || []).join(" ")}` : (m.url || "—");
    openModal("MCP · " + m.name, "model context protocol server", kv([
      ["Server", m.name], ["Status", m.status], ["Transport", m.transport], ["Scope", m.scope],
    ]) + `<div class="section-title">Launch / endpoint</div><div class="codeblock">${esc(cmd)}</div>` +
      (m.status === "error" ? `<div class="section-title">⚠ This server is failing — check its command/token with <span style="color:var(--cyan)">claude mcp</span>.</div>` : ""));
  } else if (ds.kind === "conn") {
    const conn = lastState.connectors;
    if (ds.name === "claude") {
      const d = conn.claude.detail || {};
      openModal("Claude Code", "AI CLI", kv([
        ["Version", "v" + conn.claude.version], ["Status", conn.claude.status],
        ["Path", conn.claude.path], ["MCP servers", `${d.mcp_online}/${d.mcp_count} online`],
      ]));
    } else if (ds.name === "hermes") {
      const h = conn.hermes;
      const skills = (h.skills || []).map((s) =>
        `<div class="commit">• <b>${esc(s.name)}</b> <span class="muted">(${esc(s.category)})</span></div>`).join("");
      openModal("Hermes", "AI skills framework", kv([
        ["Status", h.status], ["Branch", h.branch], ["Path", h.path], ["Skills", (h.skills || []).length],
      ]) + (skills ? `<div class="section-title">Skills</div>${skills}` : ""));
    } else if (ds.name === "openclaw") {
      const o = conn.openclaw;
      openModal("OpenClaw", "AI framework", kv([
        ["Status", o.status], ["Detected", o.present ? "yes" : "no"], ["Path", o.path || "—"],
      ]) + `<div class="muted" style="margin-top:10px">Not detected. Install a <code>openclaw</code> CLI and JARVIS will light it up automatically.</div>`);
    }
  } else if (ds.kind === "project") {
    openProjectDetail(decodeURIComponent(ds.path));
  } else if (ds.kind === "winget-all") {
    openWingetAll();
  }
}

async function openProjectDetail(path) {
  openModal("Loading…", path, `<div class="muted">Fetching project details…</div>`);
  try {
    const d = await (await fetch("/api/project?path=" + encodeURIComponent(path))).json();
    if (!d.ok) { openModal("Project", path, `<div class="muted">${esc(d.error)}</div>`); return; }
    let body = kv([
      ["Branch", d.branch], ["Remote", d.remote], ["Uncommitted", d.dirty_files],
      ["Files", d.file_count], ["Virtualenv", d.has_venv ? "yes" : "no"],
    ]);
    if (d.pyproject) {
      body += `<div class="section-title">pyproject</div>` + kv([
        ["Name", d.pyproject.name], ["Version", d.pyproject.version],
        ["Requires", d.pyproject.requires_python],
      ]);
      if (d.pyproject.deps && d.pyproject.deps.length)
        body += `<div class="chip-row">` + d.pyproject.deps.slice(0, 24).map((x) =>
          `<span class="chip">${esc(x)}</span>`).join("") + `</div>`;
    }
    if (d.commits && d.commits.length)
      body += `<div class="section-title">Recent commits</div>` +
        d.commits.map((c) => `<div class="commit">${esc(c)}</div>`).join("");
    body += `<div style="margin-top:14px"><button class="act-btn" onclick="openInCode('${esc(path)}')">open in VS Code</button></div>`;
    if (d.readme)
      body += `<div class="section-title">README</div><pre>${esc(d.readme)}</pre>`;
    openModal(d.name, d.path, body);
  } catch (e) { openModal("Project", path, `<div class="muted">Error loading project.</div>`); }
}

async function openWingetAll() {
  openModal("All system updates", "winget", `<div class="muted">Scanning…</div>`);
  try {
    const rows = await (await fetch("/api/winget/all")).json();
    const body = rows.length
      ? rows.map((r) => `<div class="update-line">
          <span class="name">${esc(r.name)}</span>
          <span class="muted">${esc(r.current)}→${esc(r.latest)}</span>
          <button class="mini-btn" onclick="applyToolUpgrade('${esc(r.id)}', this)">update</button></div>`).join("")
      : `<div class="muted">Everything is up to date.</div>`;
    openModal("All system updates", `${rows.length} packages`, body);
  } catch (e) { openModal("System updates", "winget", `<div class="muted">Error.</div>`); }
}

function openInCode(path) {
  fetch("/api/open/code", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  }).then((r) => r.json()).then((res) =>
    printMsg("jarvis", res.ok ? "Opening in VS Code, sir." : "Couldn't open VS Code: " + (res.error || "")));
}

// ---------- obsidian search ----------
let searchTimer;
function setupSearch() {
  $("obQuery").addEventListener("input", (e) => {
    clearTimeout(searchTimer);
    const q = e.target.value.trim();
    if (q.length < 2) { $("obResults").innerHTML = ""; return; }
    searchTimer = setTimeout(async () => {
      const r = await fetch("/api/obsidian/search?q=" + encodeURIComponent(q));
      const hits = await r.json();
      $("obResults").innerHTML = hits.length
        ? hits.map((h) => `<div class="hit"><b>${h.title}</b><small>${h.snippet || h.rel}</small></div>`).join("")
        : `<div class="muted">no matches</div>`;
    }, 250);
  });
}

// ---------- clock ----------
function tickClock() {
  $("clock").textContent = new Date().toLocaleTimeString("en-GB");
}

// ---------- boot ----------
async function boot() {
  setInterval(tickClock, 1000); tickClock();
  setupSearch();

  $("askForm").addEventListener("submit", (e) => {
    e.preventDefault();
    const v = $("askInput").value.trim();
    if (v) { ask(v); $("askInput").value = ""; }
  });
  $("muteBtn").addEventListener("click", () => {
    voiceOn = !voiceOn;
    $("muteBtn").textContent = voiceOn ? "🔊" : "🔇";
    $("muteBtn").classList.toggle("muted", !voiceOn);
  });
  $("refreshBtn").addEventListener("click", () => { loadState(true); checkUpdates(true); });
  $("checkUpdatesBtn").addEventListener("click", () => checkUpdates(true));
  $("speakAnswers").addEventListener("change", (e) => { voiceOn = e.target.checked; });
  // drill-down: delegated click for any [data-kind] element
  document.addEventListener("click", (e) => {
    const el = e.target.closest("[data-kind]");
    if (el) handleDrill({ ...el.dataset });
  });
  $("modalClose").addEventListener("click", closeModal);
  $("modalBackdrop").addEventListener("click", (e) => {
    if (e.target === $("modalBackdrop")) closeModal();
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeModal(); });

  $("snapshotBtn").addEventListener("click", async () => {
    printMsg("jarvis", "Writing system snapshot to your vault, sir…");
    const r = await fetch("/api/obsidian/snapshot", { method: "POST" });
    const res = await r.json();
    printMsg("jarvis", res.ok ? `Snapshot saved: ${res.rel}` : `Snapshot failed: ${res.error}`);
  });

  await loadState(false); // instant render from server cache; refresh button forces a rescan

  // brain readiness pill (click to run a live test)
  try {
    const b = await (await fetch("/api/brain/status")).json();
    const pill = $("brainPill");
    pill.textContent = "brain · " + b.label;
    pill.classList.add(b.ready ? "ready" : "notready");
    pill.title = "click to test the brain";
    pill.style.cursor = "pointer";
    pill.addEventListener("click", async () => {
      const prev = pill.textContent;
      pill.textContent = "brain · testing…";
      try {
        const t = await (await fetch("/api/brain/test", { method: "POST" })).json();
        pill.classList.remove("ready", "notready");
        pill.classList.add(t.ok ? "ready" : "notready");
        pill.textContent = t.ok ? "brain · online" : "brain · " + (t.source || "error");
        printMsg("jarvis", t.ok ? "Brain online and responding, sir." : t.answer);
      } catch (e) {
        pill.textContent = prev;
      }
    });
  } catch (e) {}

  // greeting
  try {
    const g = await (await fetch("/api/greeting")).json();
    printMsg("jarvis", g.text);
    if (g.enabled) {
      // browsers block autoplay until a gesture; play on first interaction if needed
      speak(g.text).catch(() => {
        document.addEventListener("click", () => speak(g.text), { once: true });
      });
    }
  } catch (e) {}

  setInterval(() => loadState(false), pollSeconds * 1000);
}

boot();
