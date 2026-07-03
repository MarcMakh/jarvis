// JARVIS HUD client
const $ = (id) => document.getElementById(id);
const player = $("player");
let voiceOn = true;
let pollSeconds = 20;
let lastState = null;
let pendingGreet = null; // greeting deferred until the first user gesture (autoplay)
let hermesInfo = null;   // latest Hermes chat-app status (for the open button)

// ---------- voice ----------
// Returns true only if audio actually started (so callers can defer on autoplay block).
async function speak(text) {
  if (!voiceOn || !text) return false;
  try {
    const r = await fetch("/api/speak", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    if (r.status === 204) return false;
    const blob = await r.blob();
    player.src = URL.createObjectURL(blob);
    await player.play(); // rejects if autoplay is blocked
    return true;
  } catch (e) {
    return false; // most often: browser blocked autoplay before a user gesture
  }
}

function showVoiceCue() {
  if ($("voiceCue")) return;
  const el = document.createElement("div");
  el.id = "voiceCue"; el.className = "voice-cue";
  el.innerHTML = `<span class="cue-dot"></span> 🔊 Click to enable JARVIS voice`;
  el.addEventListener("click", unlockAudio);
  document.body.appendChild(el);
}
function hideVoiceCue() { const el = $("voiceCue"); if (el) el.remove(); }

// First real user gesture unlocks audio; flush any greeting that was blocked.
function unlockAudio() {
  const btn = $("muteBtn");
  if (btn) btn.classList.remove("need-audio");
  hideVoiceCue();
  if (pendingGreet) {
    const t = pendingGreet; pendingGreet = null;
    speak(t);
  }
}
["pointerdown", "keydown"].forEach((ev) =>
  window.addEventListener(ev, unlockAudio, { once: true }));

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
    <span class="name">${h.name || "Hermes Skills"}${h.branch ? " · " + h.branch : ""}</span>
    <span class="ver ${h.present ? "" : "miss"}">${h.present ? (h.version || h.status) : h.status}</span>
    <span class="drill">details ›</span></div>`;
  if (h.skills && h.skills.length) {
    html += h.skills.map((s) =>
      `<div class="row sub"><span class="led on"></span>
        <span class="name">skill · ${s.name}</span>
        <span class="ver">${s.category}</span></div>`).join("");
  }
  const ob = (lastState && lastState.obsidian) || {};
  html += `<div class="row clickable" data-kind="conn" data-name="obsidian">
    <span class="led ${ob.available ? "on" : "off"}"></span>
    <span class="name">Obsidian <span class="muted">brain</span></span>
    <span class="ver ${ob.available ? "" : "miss"}">${ob.available ? ob.notes + " notes" : "not linked"}</span>
    <span class="drill">details ›</span></div>`;
  const oc = conn.openclaw;
  html += `<div class="row clickable" data-kind="conn" data-name="openclaw">
    <span class="led ${led(oc.status)}"></span>
    <span class="name">${oc.name}</span>
    <span class="ver ${oc.present ? "" : "miss"}">${oc.version ? "v" + oc.version : oc.status}</span>
    <span class="drill">details ›</span></div>`;
  $("connectors").innerHTML = html;
}

function renderHermes(h) {
  const el = $("hermes");
  if (!el) return;
  hermesInfo = h || null;
  if (!h) { el.innerHTML = `<div class="muted">Hermes chat not configured.</div>`; return; }
  const online = h.status === "online";
  const host = (h.url || "").replace(/^https?:\/\//, "");
  let html = `<div class="row">
    <span class="led ${online ? "on" : "off"}"></span>
    <span class="name">chat server ${online ? "online" : "offline"}${h.version ? " · v" + esc(h.version) : ""}</span>
    <span class="ver">${esc(host)}</span></div>`;

  if (online) {
    const provs = h.providers || [];
    if (provs.length) {
      const lu = h.lmstudio_update;
      html += provs.map((p) => {
        const models = (p.models || []).slice(0, 3).join(", ") +
          ((p.models || []).length > 3 ? ` +${p.models.length - 3}` : "");
        // LM Studio app-update flag (from winget), shown on the local provider row
        const upd = (p.id === "lmstudio" && lu)
          ? `<span class="badge amber" title="LM Studio ${esc(lu.current)} → ${esc(lu.latest)}">⚠ update ${esc(lu.latest)}</span>
             <button class="mini-btn" data-act="winget" data-id="${esc(lu.id)}">update</button>`
          : "";
        return `<div class="row sub">
          <span class="led ${p.available ? "on" : "off"}"></span>
          <span class="name">${p.available ? "" : "⚠ "}${esc(p.label || p.id)}</span>
          <span class="ver ${p.available ? "" : "miss"}">${esc(p.available ? (models || "ready") : p.reason)}</span>${upd}</div>`;
      }).join("");
    } else {
      html += `<div class="muted" style="margin:4px 0">No providers configured yet.</div>`;
    }
    html += `<div class="act-row" style="margin-top:10px">
      <button class="act-btn" data-act="hermes-open">▶ open Hermes chat</button></div>`;
  } else {
    html += `<div class="muted" style="margin:6px 0">Not running on ${esc(h.url)}.</div>`;
    html += `<div class="act-row" style="margin-top:8px">` +
      (h.path ? `<button class="act-btn" data-act="hermes-start" data-path="${esc(h.path)}">▶ start Hermes</button>` : "") +
      `<button class="act-btn" data-act="hermes-open">open anyway ⇱</button></div>`;
  }
  el.innerHTML = html;
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
    renderHermes(s.connectors.hermes_chat);
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
      <button class="act-btn" data-act="winget" data-id="${esc(hit.id)}">update via winget</button>`;
    let run = "";
    if (it.present && launchable.has(it.name))
      run = `<button class="act-btn" data-act="launch-term" data-target="${esc(it.name)}" data-label="${esc(it.name)}">▶ launch ${esc(it.name)}</button>`;
    if (it.name === "code" && it.present)
      run = `<button class="act-btn" data-act="launch-term" data-target="code" data-label="VS Code">▶ open VS Code</button>`;
    openModal(it.label || it.name, it.present ? "installed" : "not installed", kv([
      ["Name", it.label || it.name], ["Version", it.version || "—"],
      ["Status", it.present ? "present" : "missing"], ["Path", it.path || "—"],
    ]) + (run ? `<div style="margin:6px 0 12px">${run}</div>` : "") + upd);
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
      ]) + `<div style="margin-top:10px"><button class="act-btn" data-act="launch-term" data-target="claude" data-label="Claude">▶ open Claude terminal</button></div>`);
    } else if (ds.name === "hermes_chat") {
      const hc = conn.hermes_chat || {};
      const provs = (hc.providers || []).map((p) => {
        const models = (p.models || []).length ? ` <span class="muted">(${esc((p.models || []).join(", "))})</span>` : "";
        return `<div class="commit">${p.available ? "●" : "○"} <b>${esc(p.label || p.id)}</b>${models}${p.available ? "" : ` <span class="muted">— ${esc(p.reason)}</span>`}</div>`;
      }).join("");
      const online = hc.status === "online";
      const act = `<div class="act-row" style="margin-top:10px">` +
        (online ? `<button class="act-btn" data-act="hermes-open">▶ open Hermes chat</button>` : "") +
        (!online && hc.path ? `<button class="act-btn" data-act="hermes-start" data-path="${esc(hc.path)}">▶ start Hermes</button>` : "") +
        (hc.path ? `<button class="act-btn" data-act="open-code" data-path="${esc(hc.path)}">open in VS Code</button>` : "") +
        `</div>`;
      openModal("Hermes · Open-LLM Chat", "multi-provider LLM relay", kv([
        ["Status", hc.status], ["URL", hc.url], ["Version", hc.version ? "v" + hc.version : "—"],
        ["Providers", (hc.providers || []).length], ["Path", hc.path || "—"],
      ]) + (provs ? `<div class="section-title">Providers</div>${provs}` : "") + act);
    } else if (ds.name === "hermes") {
      const h = conn.hermes;
      const skills = (h.skills || []).map((s) =>
        `<div class="commit">• <b>${esc(s.name)}</b> <span class="muted">(${esc(s.category)})</span></div>`).join("");
      const act = h.path ? `<div style="margin-top:10px">
        <button class="act-btn" data-act="open-code" data-path="${esc(h.path)}">▶ open in VS Code</button>
        <button class="act-btn" data-act="open-folder" data-path="${esc(h.path)}">▶ open folder</button></div>` : "";
      openModal(h.name || "Hermes Skills", "reusable agent skills", kv([
        ["Status", h.status], ["Branch", h.branch], ["Path", h.path], ["Skills", (h.skills || []).length],
      ]) + (skills ? `<div class="section-title">Skills</div>${skills}` : "") + act);
    } else if (ds.name === "obsidian") {
      const ob = lastState.obsidian;
      openModal("Obsidian", "knowledge brain", kv([
        ["Status", ob.available ? "linked" : "not linked"], ["Notes", ob.notes], ["Vault", ob.vault],
      ]) + (ob.available ? `<div style="margin-top:10px"><button class="act-btn" data-act="launch-obsidian">▶ open Obsidian</button></div>` : ""));
    } else if (ds.name === "openclaw") {
      const o = conn.openclaw;
      openModal("OpenClaw", "AI framework", kv([
        ["Status", o.status], ["Detected", o.present ? "yes" : "no"], ["Path", o.path || "—"],
      ]) + `<div class="muted" style="margin-top:10px">Not detected. Install a <code>openclaw</code> CLI and JARVIS will light it up automatically.</div>`);
    }
  } else if (ds.kind === "project") {
    openProjectDetail(decodeURIComponent(ds.path));
  } else if (ds.kind === "launch") {
    openModal(ds.name || "Launch", "library shortcut",
      kv([["File", ds.path]]) +
      `<div style="margin-top:12px"><button class="act-btn" data-act="launch-file" data-path="${esc(ds.path)}" data-label="${esc(ds.name || "")}">▶ launch</button></div>`);
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
    // actions
    body += `<div class="act-row" style="margin-top:14px">`;
    if (d.runnable)
      body += `<button class="act-btn" data-act="run-app" data-path="${esc(d.run_path || d.path)}" data-label="${esc(d.run_label || "app")}">▶ run app${d.run_label ? " · " + esc(d.run_label) : ""}</button>`;
    body += `<button class="act-btn" data-act="open-code" data-path="${esc(d.path)}">open in VS Code</button>`;
    body += `<button class="act-btn" data-act="open-folder" data-path="${esc(d.path)}">open folder</button></div>`;
    // sub-projects (each independently runnable / openable)
    if (d.subprojects && d.subprojects.length) {
      body += `<div class="section-title">📦 Sub-projects (${d.subprojects.length})</div><div class="doc-list">`;
      body += d.subprojects.map((s) =>
        `<div class="subproj">
           <span class="led ${s.runnable ? "on" : "off"}"></span>
           <span class="doc-name">${esc(s.name)}</span>
           <span class="sp-actions">
             ${s.runnable ? `<button class="mini-btn" data-act="run-app" data-path="${esc(s.path)}" data-label="${esc(s.run_label || s.name)}">▶ run</button>` : ""}
             <button class="mini-btn" data-act="open-code" data-path="${esc(s.path)}">code</button>
             <button class="mini-btn" data-act="open-folder" data-path="${esc(s.path)}">folder</button>
           </span>
         </div>`).join("");
      body += `</div>`;
    }
    // supporting documents
    if (d.docs && d.docs.length) {
      const src = d.docs_source ? ` <span class="muted" style="font-size:10px;text-transform:none;letter-spacing:0">from ${esc(d.docs_source)}</span>` : "";
      body += `<div class="section-title">📄 Supporting documents (${d.docs.length})${src}</div><div class="doc-list">`;
      body += d.docs.map((doc) =>
        `<div class="doc-item" data-act="open-doc" data-path="${esc(doc.path)}">
           <span class="doc-ext doc-${esc(doc.ext)}">${esc(doc.ext)}</span>
           <span class="doc-name">${esc(doc.name)}</span>
           <span class="drill">open ›</span></div>`).join("");
      body += `</div>`;
    }
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
          <button class="mini-btn" data-act="winget" data-id="${esc(r.id)}">update</button></div>`).join("")
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

function postPath(url, path, okMsg) {
  fetch(url, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  }).then((r) => r.json()).then((res) =>
    printMsg("jarvis", res.ok ? okMsg : "Couldn't open: " + (res.error || "")));
}

// Delegated actions for modal buttons — data-attributes avoid embedding Windows
// paths in inline JS (backslashes were corrupting paths in onclick handlers).
function handleAct(ds, el) {
  switch (ds.act) {
    case "run-app": launch("app", { path: ds.path }, ds.label || "app"); break;
    case "open-code": openInCode(ds.path); break;
    case "open-folder": postPath("/api/open/folder", ds.path, "Opening folder, sir."); break;
    case "open-doc": postPath("/api/open/doc", ds.path, "Opening document, sir."); break;
    case "launch-term": launch("terminal", { target: ds.target }, ds.label || ds.target); break;
    case "launch-obsidian": launch("obsidian", {}, "Obsidian"); break;
    case "launch-file": launch("file", { path: ds.path }, ds.label || "program"); break;
    case "winget": applyToolUpgrade(ds.id, el); break;
    case "hermes-open":
      printMsg("jarvis", "Opening HERMES, sir.");
      fetch("/api/hermes/open", { method: "POST" }).then((r) => r.json()).then((res) =>
        printMsg("jarvis", res.ok
          ? (res.action === "starting" ? "HERMES is starting…" : "HERMES opened.")
          : "Couldn't open HERMES: " + (res.error || ""))).catch(() => {});
      break;
    case "hermes-start":
      launch("app", { path: ds.path }, "Hermes");
      printMsg("jarvis", "Starting Hermes, sir. It should be online in a moment.");
      // Re-scan a few times so the panel flips to online once the server binds.
      [4000, 8000, 13000].forEach((t) => setTimeout(() => loadState(true), t));
      break;
  }
}

// generic launch: kind = "terminal" | "app" | "obsidian"
function launch(kind, extra, label) {
  printMsg("jarvis", `Launching ${label || kind}, sir.`);
  fetch("/api/launch", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ kind, ...extra }),
  }).then((r) => r.json()).then((res) =>
    printMsg("jarvis", res.ok ? `Launched ${res.plan || res.launched || label}.` : `Launch failed: ${res.error}`));
}
const launchable = new Set(["python", "node", "claude", "uv", "gh"]);

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

// ---------- ecosystem force graph ----------
const GROUP_COLORS = {
  core: "#ff4d9d", ai: "#38bdf8", mcp: "#2dd4bf", mcp_err: "#f43f5e",
  brain: "#a78bfa", note: "#818cf8", skill: "#fbbf24", runtime: "#22d3ee",
  tool: "#fb923c", hub: "#fcd34d", project: "#f472b6", absent: "#4b6472",
  game: "#22c55e", emulator: "#c084fc",
};
const G = {
  canvas: null, ctx: null, dpr: 1, w: 0, h: 0,
  nodes: [], links: [], byId: {}, adj: {},
  hover: null, drag: null, mouse: { x: -1, y: -1 }, down: null, raf: null,
};

function recenterNodes() {
  const cx = G.w / 2, cy = G.h / 2;
  for (const n of G.nodes) {
    n.x = cx + (Math.random() - 0.5) * Math.min(520, G.w * 0.6);
    n.y = cy + (Math.random() - 0.5) * Math.min(260, G.h * 0.6);
    n.vx = n.vy = 0;
  }
  if (G.byId["jarvis"]) { G.byId["jarvis"].x = cx; G.byId["jarvis"].y = cy; }
}

function gResize() {
  const c = G.canvas;
  if (!c) return;
  const rect = c.getBoundingClientRect();
  const hadSize = G.w > 0 && G.h > 0;
  G.dpr = window.devicePixelRatio || 1;
  G.w = rect.width; G.h = rect.height;
  if (G.w <= 0 || G.h <= 0) return; // not laid out yet; ResizeObserver will retry
  c.width = G.w * G.dpr; c.height = G.h * G.dpr;
  G.ctx.setTransform(G.dpr, 0, 0, G.dpr, 0, 0);
  if (!hadSize && G.nodes.length) recenterNodes(); // dimensions just became known
}

async function initGraph() {
  G.canvas = $("graph");
  if (!G.canvas) return;
  G.ctx = G.canvas.getContext("2d");
  gResize();
  window.addEventListener("resize", gResize);
  // The canvas may not be laid out when init runs (esp. a fresh desktop-launched
  // tab); observe it so we size + re-center the moment it gets real dimensions.
  if (window.ResizeObserver) new ResizeObserver(() => gResize()).observe(G.canvas);
  bindGraphEvents();
  await loadGraph();
  requestAnimationFrame(gResize);
  setTimeout(gResize, 350);
  if (!G.raf) gStep();
}

async function loadGraph() {
  try {
    const data = await (await fetch("/api/graph")).json();
    const cx = G.w / 2 || 600, cy = G.h / 2 || 200;
    G.byId = {}; G.adj = {};
    G.nodes = data.nodes.map((n) => {
      const node = { ...n, x: cx + (Math.random() - 0.5) * 400, y: cy + (Math.random() - 0.5) * 200, vx: 0, vy: 0 };
      G.byId[n.id] = node; G.adj[n.id] = new Set();
      return node;
    });
    if (G.byId["jarvis"]) { G.byId["jarvis"].x = cx; G.byId["jarvis"].y = cy; }
    G.links = data.links.filter((l) => G.byId[l.source] && G.byId[l.target]);
    G.links.forEach((l) => { G.adj[l.source].add(l.target); G.adj[l.target].add(l.source); });
  } catch (e) { /* graph optional */ }
}

function gStep() {
  const { nodes, links, byId, ctx } = G;
  const cx = G.w / 2, cy = G.h / 2;
  // repulsion
  for (let i = 0; i < nodes.length; i++) {
    const a = nodes[i];
    for (let j = i + 1; j < nodes.length; j++) {
      const b = nodes[j];
      let dx = a.x - b.x, dy = a.y - b.y;
      let d2 = dx * dx + dy * dy || 0.01;
      const f = 1400 / d2;
      const d = Math.sqrt(d2);
      const ux = dx / d, uy = dy / d;
      a.vx += ux * f; a.vy += uy * f; b.vx -= ux * f; b.vy -= uy * f;
    }
  }
  // springs
  for (const l of links) {
    const a = byId[l.source], b = byId[l.target];
    let dx = b.x - a.x, dy = b.y - a.y;
    const d = Math.sqrt(dx * dx + dy * dy) || 0.01;
    const rest = 58 + a.r + b.r;
    const f = (d - rest) * 0.015;
    const ux = dx / d, uy = dy / d;
    a.vx += ux * f; a.vy += uy * f; b.vx -= ux * f; b.vy -= uy * f;
  }
  // gravity + integrate + gentle drift
  for (const n of nodes) {
    n.vx += (cx - n.x) * 0.004; n.vy += (cy - n.y) * 0.004;
    n.vx += (Math.random() - 0.5) * 0.08; n.vy += (Math.random() - 0.5) * 0.08;
    n.vx *= 0.86; n.vy *= 0.86;
    if (G.drag === n) { n.x = G.mouse.x; n.y = G.mouse.y; n.vx = n.vy = 0; }
    else { n.x += n.vx; n.y += n.vy; }
    if (G.w > 0 && G.h > 0) { // don't clamp to a zero-size canvas (collapses the graph)
      n.x = Math.max(n.r, Math.min(G.w - n.r, n.x));
      n.y = Math.max(n.r, Math.min(G.h - n.r, n.y));
    }
  }
  // hover detection
  if (!G.drag && G.mouse.x >= 0) {
    G.hover = null; let best = 1e9;
    for (const n of nodes) {
      const dx = n.x - G.mouse.x, dy = n.y - G.mouse.y, d = Math.hypot(dx, dy);
      if (d < n.r + 7 && d < best) { best = d; G.hover = n; }
    }
    G.canvas.style.cursor = G.hover ? "pointer" : "grab";
  }
  gDraw(ctx);
  G.raf = requestAnimationFrame(gStep);
}

function gDraw(ctx) {
  ctx.clearRect(0, 0, G.w, G.h);
  const hov = G.hover;
  const lit = hov ? G.adj[hov.id] : null;
  // links
  for (const l of G.links) {
    const a = G.byId[l.source], b = G.byId[l.target];
    const on = hov && (l.source === hov.id || l.target === hov.id);
    ctx.strokeStyle = on ? "rgba(70,214,255,0.55)" : (hov ? "rgba(120,150,170,0.06)" : "rgba(120,160,180,0.16)");
    ctx.lineWidth = on ? 1.6 : 0.7;
    ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
  }
  // nodes
  for (const n of G.nodes) {
    const color = GROUP_COLORS[n.group] || "#9fb8c8";
    const dim = hov && n !== hov && !(lit && lit.has(n.id));
    ctx.globalAlpha = dim ? 0.28 : 1;
    if (n === hov || n.group === "core") {
      ctx.shadowColor = color; ctx.shadowBlur = n === hov ? 22 : 14;
    } else { ctx.shadowBlur = 0; }
    ctx.beginPath();
    ctx.arc(n.x, n.y, n.r * (n === hov ? 1.25 : 1), 0, 6.2832);
    ctx.fillStyle = color; ctx.fill();
    ctx.shadowBlur = 0;
    // labels: core, hubs, ai/brain always; others on hover/neighbour
    const showLabel = n.r >= 9 || n.group === "ai" || n.group === "brain" ||
      n === hov || (lit && lit.has(n.id));
    if (showLabel) {
      ctx.globalAlpha = dim ? 0.4 : 1;
      ctx.fillStyle = n === hov ? "#eaf7ff" : "#9fc4d6";
      ctx.font = (n === hov ? "600 " : "") + Math.max(10, Math.min(13, n.r)) + "px Consolas, monospace";
      ctx.textAlign = "center";
      ctx.fillText(n.label, n.x, n.y - n.r - 5);
    }
  }
  ctx.globalAlpha = 1;
  // tooltip
  const tip = $("graphTip");
  if (hov) {
    tip.style.display = "block";
    tip.style.left = hov.x + "px"; tip.style.top = hov.y + "px";
    tip.textContent = hov.label + (hov.drill ? "  · click to open" : "");
  } else { tip.style.display = "none"; }
}

function bindGraphEvents() {
  const c = G.canvas;
  c.addEventListener("mousemove", (e) => {
    const r = c.getBoundingClientRect();
    G.mouse.x = e.clientX - r.left; G.mouse.y = e.clientY - r.top;
  });
  c.addEventListener("mouseleave", () => { G.mouse.x = G.mouse.y = -1; G.hover = null; });
  c.addEventListener("mousedown", () => {
    if (G.hover) { G.drag = G.hover; G.down = G.hover; G.downXY = { x: G.mouse.x, y: G.mouse.y }; }
  });
  window.addEventListener("mouseup", () => {
    if (G.down && G.down.drill && G.downXY) {
      const moved = Math.hypot(G.mouse.x - G.downXY.x, G.mouse.y - G.downXY.y);
      if (moved < 6) handleDrill(G.down.drill); // a click, not a drag
    }
    G.drag = null; G.down = null;
  });
}

// ---------- ecosystem map (2D force graph · 3D-shaded beads · collapsible) ----------
// Flat layout, but each node is drawn as a shaded sphere. Branch nodes (hubs like
// "PC Games", connectors, etc.) start collapsed; click one to open its children and
// zoom in, click empty space to collapse everything and zoom back out.
let MAP = null;
let rawNodes = [], rawLinks = [], childMap = {}, parentMap = {}, initialCollapsed = new Set(), collapsed = new Set();

function _rgb(h) { h = h.replace("#", ""); return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16)); }
function lighten(h, a) { const [r, g, b] = _rgb(h); const f = (x) => Math.round(x + (255 - x) * a); return `rgb(${f(r)},${f(g)},${f(b)})`; }
function darken(h, a) { const [r, g, b] = _rgb(h); const f = (x) => Math.round(x * (1 - a)); return `rgb(${f(r)},${f(g)},${f(b)})`; }
function hasKids(id) { return (childMap[id] || []).length > 0; }

function sizeGraphMap() {
  const el = $("graphmap");
  if (MAP && el) MAP.width(el.clientWidth).height(el.clientHeight || 440);
}

// Only show nodes reachable from the core through non-collapsed branches.
function visibleData() {
  const shown = new Set(["jarvis"]);
  const q = ["jarvis"];
  while (q.length) {
    const id = q.shift();
    if (collapsed.has(id)) continue;
    for (const c of (childMap[id] || [])) if (!shown.has(c)) { shown.add(c); q.push(c); }
  }
  return {
    nodes: rawNodes.filter((n) => shown.has(n.id)),
    links: rawLinks.filter((l) => shown.has(l.s) && shown.has(l.t)).map((l) => ({ source: l.s, target: l.t })),
  };
}

// Lazily fetch shortcut icons, max 6 at a time (avoid spawning 60 PowerShells).
const _iconQueue = [];
let _iconActive = 0;
function _pumpIcons() {
  while (_iconActive < 6 && _iconQueue.length) {
    const n = _iconQueue.shift();
    _iconActive++;
    const img = new Image();
    const done = () => { _iconActive--; _pumpIcons(); };
    img.onload = () => { n._icon = img; done(); };
    img.onerror = done;
    img.src = "/api/icon?path=" + encodeURIComponent(n.drill.path);
  }
}
function ensureIcons(nodes) {
  for (const n of nodes)
    if ((n.group === "game" || n.group === "emulator") && !n._iconReq && n.drill && n.drill.path) {
      n._iconReq = true; _iconQueue.push(n);
    }
  _pumpIcons();
}
function renderMap() {
  const vis = visibleData();
  MAP.graphData(vis);
  ensureIcons(vis.nodes);   // load icons for whatever games are now visible
}

// Draw a node as a shaded 3D bead (radial gradient + glow), with a label.
function drawNode(node, ctx, scale) {
  const base = GROUP_COLORS[node.group] || "#9fb8c8";
  const r = node.r || 6;
  const kids = hasKids(node.id);
  // game/emulator nodes show their real shortcut icon (clipped to a circle)
  if (node._icon) {
    ctx.save();
    ctx.beginPath(); ctx.arc(node.x, node.y, r, 0, 6.2832); ctx.closePath(); ctx.clip();
    ctx.drawImage(node._icon, node.x - r, node.y - r, r * 2, r * 2);
    ctx.restore();
    ctx.lineWidth = 1.2 / scale; ctx.strokeStyle = "rgba(180,220,255,0.55)";
    ctx.beginPath(); ctx.arc(node.x, node.y, r, 0, 6.2832); ctx.stroke();
    return;
  }
  const grad = ctx.createRadialGradient(node.x - r * 0.36, node.y - r * 0.36, r * 0.1, node.x, node.y, r);
  grad.addColorStop(0, lighten(base, 0.6));
  grad.addColorStop(0.5, base);
  grad.addColorStop(1, darken(base, 0.5));
  if (node.group === "core" || node.group === "hub" || kids) { ctx.shadowColor = base; ctx.shadowBlur = 18; }
  ctx.beginPath(); ctx.arc(node.x, node.y, r, 0, 6.2832); ctx.fillStyle = grad; ctx.fill();
  ctx.shadowBlur = 0;
  // collapsed branch → ring, signalling "click to open"
  if (kids && collapsed.has(node.id)) {
    ctx.lineWidth = 1.6 / scale; ctx.strokeStyle = lighten(base, 0.4);
    ctx.beginPath(); ctx.arc(node.x, node.y, r + 3 / scale, 0, 6.2832); ctx.stroke();
  }
  const showLabel = ["core", "hub", "ai", "brain"].includes(node.group) || kids;
  if (showLabel) {
    const fs = Math.max(4, (node.group === "core" ? 15 : 12) / scale);
    ctx.font = `${fs}px Consolas, monospace`;
    ctx.fillStyle = "#dbeffb"; ctx.textAlign = "center"; ctx.textBaseline = "bottom";
    ctx.fillText(node.label, node.x, node.y - r - 2 / scale);
  }
}
function nodeHit(node, color, ctx) {
  const r = (node.r || 6) + 2;   // a touch larger than drawn, for easier clicking
  ctx.fillStyle = color; ctx.beginPath(); ctx.arc(node.x, node.y, r, 0, 6.2832); ctx.fill();
}

function initGraphMap() {
  const el = $("graphmap");
  if (!el || typeof ForceGraph === "undefined") return;
  MAP = ForceGraph()(el)
    .backgroundColor("rgba(0,0,0,0)")
    .linkColor(() => "rgba(120,160,180,0.3)")
    .linkWidth(0.9)
    .nodeLabel((n) => n.label)
    .nodeCanvasObject(drawNode)
    .nodePointerAreaPaint(nodeHit)
    .onNodeClick((node) => {
      if (hasKids(node.id)) {
        const opening = collapsed.has(node.id);
        if (opening) collapsed.delete(node.id); else collapsed.add(node.id);
        renderMap();
        setTimeout(() => {
          if (opening) {
            MAP.centerAt(node.x, node.y, 700); MAP.zoom(2.6, 700);  // drill in → focus this bead
          } else {
            // collapse → fly back to the parent bead (or fit if it's top-level)
            const par = MAP.graphData().nodes.find((n) => n.id === parentMap[node.id]);
            if (par && par.id !== "jarvis") { MAP.centerAt(par.x, par.y, 700); MAP.zoom(2.0, 700); }
            else MAP.zoomToFit(700, 60);
          }
        }, 60);
      } else if (node.drill) {
        handleDrill(node.drill);  // leaf → launch a game / open detail
      }
    })
    .onBackgroundClick(() => {
      collapsed = new Set(initialCollapsed);   // click away → collapse everything
      renderMap();
      MAP.zoomToFit(700, 60);
    });
  MAP.d3Force("charge").strength(-70);
  if (MAP.d3Force("link")) MAP.d3Force("link").distance((l) => {
    const s = typeof l.source === "object" ? l.source.id : l.source;
    const t = typeof l.target === "object" ? l.target.id : l.target;
    return (s === "jarvis" || t === "jarvis") ? 95 : 34;   // push hubs out from the core
  });
  MAP.d3VelocityDecay(0.72);
  MAP.cooldownTicks(Infinity);
  MAP.cooldownTime(Infinity);   // never stop ticking (default 15s) so it keeps floating
  // Gentle perpetual float: each node drifts randomly but springs back toward a
  // slowly-following "home", so the map jiggles without flying apart or freezing.
  MAP.d3Force("float", () => {
    for (const n of MAP.graphData().nodes) {
      if (n.hx === undefined) { n.hx = n.x; n.hy = n.y; }
      n.hx += (n.x - n.hx) * 0.05;
      n.hy += (n.y - n.hy) * 0.05;
      n.vx += (Math.random() - 0.5) * 0.6 + (n.hx - n.x) * 0.05;
      n.vy += (Math.random() - 0.5) * 0.6 + (n.hy - n.y) * 0.05;
    }
  });
  sizeGraphMap();
  window.addEventListener("resize", sizeGraphMap);
  loadGraphMap();
}

async function loadGraphMap() {
  if (!MAP) return;
  try {
    const data = await (await fetch("/api/graph")).json();
    rawNodes = data.nodes;
    rawLinks = data.links.map((l) => ({ s: l.source, t: l.target }));
    childMap = {};
    parentMap = {};
    for (const l of rawLinks) { (childMap[l.s] = childMap[l.s] || []).push(l.t); parentMap[l.t] = l.s; }
    // Show one level below the core by default; only the big game/emulator
    // libraries start collapsed, so you drill into those to avoid clutter.
    initialCollapsed = new Set(rawNodes.filter((n) => String(n.id).startsWith("lib:")).map((n) => n.id));
    collapsed = new Set(initialCollapsed);
    renderMap();
    setTimeout(() => MAP && MAP.zoomToFit(600, 70), 500);
  } catch (e) { /* graph optional */ }
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
  $("refreshBtn").addEventListener("click", () => { loadState(true); checkUpdates(true); loadGraphMap(); });
  $("graphRefresh").addEventListener("click", () => loadGraphMap());
  $("clearConsole").addEventListener("click", () => { $("consoleIO").innerHTML = ""; });
  $("checkUpdatesBtn").addEventListener("click", () => checkUpdates(true));
  $("speakAnswers").addEventListener("change", (e) => { voiceOn = e.target.checked; });
  // delegated clicks: modal action buttons first, then drill-down rows
  document.addEventListener("click", (e) => {
    const act = e.target.closest("[data-act]");
    if (act) { handleAct({ ...act.dataset }, act); return; }
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
  initGraphMap();

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

  // vault-memory pill (local RAG) — shows index size; click to (re)index
  try {
    const rp = $("ragPill");
    const renderRag = (s) => {
      const p = s.progress || {};
      if (p.state === "embedding" || p.state === "scanning") {
        rp.textContent = `vault · indexing ${p.done_chunks || 0}/${p.new_chunks || "…"}`;
        rp.classList.remove("ready", "notready");
        return true; // still running
      }
      if (s.indexed) {
        rp.textContent = `vault · ${s.chunks} chunks`;
        rp.classList.add(s.available ? "ready" : "notready");
      } else {
        rp.textContent = s.available ? "vault · click to index" : "vault · embedder offline";
        rp.classList.add(s.available ? "notready" : "notready");
      }
      return false;
    };
    const ragStatus = async () => renderRag(await (await fetch("/api/rag/status")).json());
    await ragStatus();
    rp.style.cursor = "pointer";
    rp.title = "Vault memory — local RAG over your notes. Click to (re)index.";
    rp.addEventListener("click", async () => {
      const r = await (await fetch("/api/rag/reindex", { method: "POST" })).json();
      if (r.error) { printMsg("jarvis", `Vault indexing failed: ${r.error}`); return; }
      printMsg("jarvis", "Indexing your vaults, sir. I'll keep the pill posted.");
      const timer = setInterval(async () => {
        const running = await ragStatus();
        if (!running) {
          clearInterval(timer);
          const s = await (await fetch("/api/rag/status")).json();
          printMsg("jarvis", s.indexed
            ? `Vault memory ready: ${s.chunks} chunks from ${s.files} notes.`
            : `Vault indexing did not complete: ${(s.progress || {}).error || "unknown"}`);
        }
      }, 1500);
    });
  } catch (e) {}

  // greeting
  try {
    const g = await (await fetch("/api/greeting")).json();
    printMsg("jarvis", g.text);
    if (g.enabled && voiceOn) {
      const played = await speak(g.text);
      if (!played) {
        // autoplay blocked — defer to first gesture and cue the user clearly
        pendingGreet = g.text;
        const btn = $("muteBtn");
        if (btn) { btn.classList.add("need-audio"); btn.title = "click anywhere to enable JARVIS voice"; }
        showVoiceCue();
      }
    }
  } catch (e) {}

  setInterval(() => loadState(false), pollSeconds * 1000);
}

boot();
