// The four content views (Overview, Projects, Systems, Vault) and the detail
// drawer. Every renderer is a pure function of the latest state and goes
// through setHtml(), so re-rendering on each state change costs nothing when
// the markup is unchanged.

import { $, S, ago, api, bus, debounce, esc, icon, level, num, qsa, setHtml, toast, when } from "./core.js";
import { renderMarkdown } from "./md.js";

const led = (status) => ({ online: "on", linked: "on", configured: "warn", error: "err" }[status] || (status ? "warn" : ""));
const btn = (label, data, cls = "") =>
  `<button class="mini ${cls}" ${Object.entries(data).map(([k, v]) =>
    (k === "title" ? `title="${esc(v)}"` : `data-${k}="${esc(v)}"`)).join(" ")}>${label}</button>`;

// ═══════════════════════════ Overview ════════════════════════════════════

export async function renderBriefing(wait = 0) {
  let b;
  try { b = await api(`/api/briefing?wait=${wait}`); } catch { return; }
  S.briefing = b;
  const s = S.state;
  if (s) $("heroEyebrow").textContent = `${(s.identity && s.identity.name) || "JARVIS"} · v${s.version}`;
  $("heroGreeting").textContent = `Good ${b.part}, ${b.honorific}.`;
  const hl = $("heroHeadline");
  hl.textContent = b.headline;
  hl.classList.toggle("attn", b.attention > 0);
  setHtml($("briefList"), b.items.map((it) => {
    const a = it.act || {};
    const action = a.label
      ? btn(esc(a.label), a.act ? { act: a.act, id: a.id || "" }
        : a.ui === "view" ? { act: "view", view: a.view, filter: a.filter || "" }
        : { act: "settings" })
      : "";
    const mute = it.key && it.level !== "ok" ? btn("mute", { act: "alert-mute", key: it.key }) : "";
    return `<div class="brief ${it.level}"><span class="bi">${esc(it.icon)}</span>
      <span class="bt">${esc(it.text)}</span>${action}${mute}</div>`;
  }).join("") || `<div class="empty">Nothing to report.</div>`);
}

const GROUP_ORDER = ["AI", "Trading", "Delivery", "Play", "Home", "Custom"];

export function renderApps(s) {
  const apps = s.apps || [];
  if (!apps.length) {
    if (!(s.meta.apps || {}).ts) return;
    setHtml($("apps"), `<div class="empty">No apps registered. Add your own under <code>[[apps]]</code> in config.toml.</div>`);
    return;
  }
  const groups = new Map();
  for (const a of apps) (groups.get(a.group) || groups.set(a.group, []).get(a.group)).push(a);
  const order = [...groups.keys()].sort((a, b) =>
    (GROUP_ORDER.indexOf(a) + 1 || 99) - (GROUP_ORDER.indexOf(b) + 1 || 99));
  const running = apps.filter((a) => a.running).length;
  $("appsHint").textContent = `${running} running · ${apps.length} apps`;
  setHtml($("apps"), order.map((g) =>
    `<div class="app-group"><div class="gl">${esc(g)}</div><div class="app-tiles">` +
    groups.get(g).map((a) =>
      `<button class="app ${a.running ? "running" : ""}" data-act="app-open" data-id="${esc(a.id)}"
               title="${esc(a.name)} — ${esc(a.desc)}${a.port ? ` · port ${a.port}` : ""}${a.running ? " · running" : ""}">
         <span class="ai">${esc(a.icon)}</span>
         <span class="at"><span class="an">${esc(a.name)}</span><span class="ad">${esc(a.desc)}</span></span>
         ${a.running == null ? "" : `<span class="led ${a.running ? "on" : ""}"></span>`}
         ${a.can_stop ? `<span class="stop" data-act="app-stop" data-id="${esc(a.id)}" data-name="${esc(a.name)}" title="Stop ${esc(a.name)}">${icon("stop")}</span>` : ""}
       </button>`).join("") + `</div></div>`).join(""));
}

function gitBits(p) {
  if (!p.git) return `<span class="tag">no git</span>`;
  let out = `<span class="br">${esc(p.branch || "git")}</span>`;
  if (p.dirty_files) out += ` <span class="tag warn" title="uncommitted files">● ${p.dirty_files}</span>`;
  if (p.ahead) out += ` <span class="tag warn" title="commits not pushed">↑ ${p.ahead}</span>`;
  if (p.behind) out += ` <span class="tag" title="commits to pull">↓ ${p.behind}</span>`;
  return out;
}

export function renderRecent(s) {
  const projs = (s.projects || []).slice(0, 7);
  if (!projs.length && !(s.meta.projects || {}).ts) return;
  setHtml($("recentProjects"), projs.map((p) =>
    `<div class="row click" data-kind="project" data-path="${esc(p.path)}">
       <span class="led ${p.git ? (p.dirty ? "warn" : "on") : ""}"></span>
       <span class="name grow">${esc(p.name)}</span>
       <span class="val">${p.git ? esc(p.branch || "git") + (p.dirty_files ? ` ●${p.dirty_files}` : "") : "no git"}</span>
       <span class="muted" style="font-size:11px;min-width:54px;text-align:right">${ago(p.mtime)}</span>
     </div>`).join("") || `<div class="empty">No projects found under your project roots.</div>`);
}

export function renderStack(s) {
  const c = s.connectors;
  const cl = c.claude, auth = cl.auth || {};
  const mcp = c.mcp_servers || [];
  const hc = c.hermes_chat || {};
  const ob = s.obsidian || {};
  let html = `<div class="row click" data-kind="conn" data-name="claude">
      <span class="led ${cl.present ? (auth.logged_in ? "on" : "warn") : ""}"></span>
      <span class="name grow">Claude Code</span>
      ${cl.present && !auth.logged_in ? `<span class="tag warn" title="The standalone claude CLI is signed out — run: claude auth login">CLI signed out</span>` : ""}
      <span class="val">${cl.version ? "v" + esc(cl.version) : esc(cl.status)}</span></div>`;
  if (mcp.length) {
    const up = mcp.filter((m) => m.status === "online").length;
    html += `<div class="row click" data-act="view" data-view="systems">
      <span class="led ${up === mcp.length ? "on" : up ? "warn" : "err"}"></span>
      <span class="name grow">MCP servers</span>
      <span class="val">${up}/${mcp.length} online</span></div>`;
    html += mcp.filter((m) => m.status !== "online").map((m) =>
      `<div class="row sub click" data-kind="mcp" data-name="${esc(m.name)}"><span class="led ${led(m.status)}"></span>
         <span class="name grow">${esc(m.name)}</span><span class="val miss">${esc(m.status)}</span></div>`).join("");
  }
  const online = hc.status === "online";
  html += `<div class="row click" data-kind="conn" data-name="hermes_chat">
      <span class="led ${online ? "on" : ""}"></span><span class="name grow">Hermes</span>
      <span class="val ${online ? "" : "miss"}">${online ? "v" + esc(hc.version || "?") : "offline"}</span></div>`;
  for (const p of (hc.providers || [])) {
    const n = p.model_count ?? (p.models || []).length;
    const loaded = (p.loaded || []).length ? ` · ${esc(p.loaded[0])} loaded` : "";
    html += `<div class="row sub" title="${esc(p.available ? `${n} models${loaded}` : p.reason)}"><span class="led ${p.available ? "on" : ""}"></span>
      <span class="name grow">${esc(p.label || p.id)}</span>
      <span class="val ${p.available ? "" : "miss"}">${p.available ? `${n} model${n === 1 ? "" : "s"}` : "unavailable"}</span></div>`;
  }
  if (!online && hc.url) {
    html += `<div class="row sub"><span class="muted grow">not running</span>${btn("start Hermes", { act: "app-open", id: "hermes" })}</div>`;
  }
  html += `<div class="row click" data-act="view" data-view="vault"><span class="led ${ob.available ? "on" : ""}"></span>
      <span class="name grow">Obsidian</span><span class="val ${ob.available ? "" : "miss"}">${ob.available ? ob.notes + " notes" : "not linked"}</span></div>`;
  const h = c.hermes || {};
  if (h.present) {
    html += `<div class="row click" data-kind="conn" data-name="hermes"><span class="led on"></span>
      <span class="name grow">Hermes Skills</span><span class="val">${(h.skills || []).length} skills</span></div>`;
  }
  setHtml($("aiStack"), html);
}

// ═══════════════════════════ Projects ════════════════════════════════════

const projUi = { filter: "all", sort: "recent", q: "" };
const SHIELD_LABEL = {
  protected: "pushed", covered: "snapshotted", unpushed: "unpushed work",
  local: "local only", exposed: "no backup",
};
const KIND = { python: "PY", node: "JS", powershell: "PS", dotnet: ".N", other: "··" };

function shieldOf(s) {
  const map = new Map();
  for (const r of ((s.shield || {}).projects || [])) map.set(r.path, r);
  return map;
}
function shieldClass(r) {
  if (!r) return "exposed";
  return r.level !== "protected" && r.covered ? "covered" : r.level;
}

export function setProjectFilter(f) {
  projUi.filter = f || "all";
  qsa("#projFilter button").forEach((b) => b.classList.toggle("on", b.dataset.f === projUi.filter));
  if (S.state) renderProjects(S.state);
}

export function renderProjects(s) {
  const projs = s.projects || [];
  const sh = s.shield || {};
  const smap = shieldOf(s);
  const jobs = sh.jobs || {};

  // ── protection bar ──
  const cts = { protected: 0, covered: 0, unpushed: 0, local: 0, exposed: 0 };
  for (const p of projs) cts[shieldClass(smap.get(p.path))]++;
  const total = projs.length || 1;
  const atRisk = cts.local + cts.exposed + cts.unpushed;
  setHtml($("shieldBar"),
    `<span class="sb-title">${icon("shield")} Protection</span>
     <div class="shield-meter" title="How many projects have a copy somewhere other than the working folder">
       ${["protected", "covered", "unpushed", "local", "exposed"].map((k) =>
         cts[k] ? `<i class="sm-${k}" style="width:${(100 * cts[k] / total).toFixed(2)}%"></i>` : "").join("")}
     </div>
     <div class="sb-legend">
       <span><b>${cts.protected}</b> pushed</span><span><b>${cts.covered}</b> snapshotted</span>
       <span><b>${cts.unpushed}</b> unpushed</span><span><b>${cts.local}</b> local-only git</span>
       <span><b>${cts.exposed}</b> no backup</span>
     </div>
     <div class="sb-acts">
       ${atRisk ? `<button class="btn" data-act="snapshot-all" ${sh.busy ? "disabled" : ""}>${icon("shield")}${sh.busy ? "Snapshotting…" : `Snapshot ${cts.local + cts.exposed + cts.unpushed} at risk`}</button>` : `<span class="tag ok">everything is covered</span>`}
       <button class="btn ghost" data-act="shield-open" title="${esc(sh.dir || "")}">${icon("folder")}Backups</button>
     </div>`);

  // ── filter counts ──
  const counts = {
    all: projs.length,
    dirty: projs.filter((p) => p.dirty || p.ahead).length,
    risk: projs.filter((p) => { const c = shieldClass(smap.get(p.path)); return c === "exposed" || c === "local" || c === "unpushed"; }).length,
    run: projs.filter((p) => p.runnable).length,
  };
  qsa("#projFilter button").forEach((b) => {
    const label = b.textContent.replace(/\s*\d+$/, "");
    b.innerHTML = `${label}<b>${counts[b.dataset.f]}</b>`;
  });

  // ── list ──
  const q = projUi.q.toLowerCase();
  let list = projs.filter((p) => {
    if (q && !p.name.toLowerCase().includes(q)) return false;
    const c = shieldClass(smap.get(p.path));
    if (projUi.filter === "dirty") return p.dirty || p.ahead;
    if (projUi.filter === "risk") return c === "exposed" || c === "local" || c === "unpushed";
    if (projUi.filter === "run") return p.runnable;
    return true;
  });
  const riskRank = { exposed: 0, local: 1, unpushed: 2, covered: 3, protected: 4 };
  if (projUi.sort === "name") list = [...list].sort((a, b) => a.name.localeCompare(b.name));
  else if (projUi.sort === "risk") list = [...list].sort((a, b) =>
    riskRank[shieldClass(smap.get(a.path))] - riskRank[shieldClass(smap.get(b.path))] || b.mtime - a.mtime);

  setHtml($("projGrid"), list.map((p) => {
    const r = smap.get(p.path);
    const cls = shieldClass(r);
    const job = jobs[p.path];
    const snapping = job && (job.state === "queued" || job.state === "running");
    const snapInfo = snapping ? `<span class="snapping">snapshotting… ${job.files || 0} files</span>`
      : r && r.last_snapshot ? `snapshot ${ago(r.last_snapshot)}` : "";
    return `<div class="pcard" data-kind="project" data-path="${esc(p.path)}" tabindex="0">
      <div class="pt"><span class="pk ${esc(p.kind)}">${KIND[p.kind] || "··"}</span>
        <span class="pn" title="${esc(p.path)}">${esc(p.name)}</span>
        <span class="shield-ico ${cls}" title="${esc(r ? r.why : "")}">${icon("shield")}${SHIELD_LABEL[cls]}</span></div>
      <div class="pm">${gitBits(p)}${p.last_commit ? `<span class="muted">· committed ${ago(p.last_commit)}</span>` : ""}</div>
      <div class="pf"><span>${esc(p.root)} · edited ${ago(p.mtime)}</span>${snapInfo ? `<span>· ${snapInfo}</span>` : ""}
        <span class="pa">
          ${p.runnable ? btn(icon("play") + "run", { act: "run-app", path: p.run_path || p.path, label: p.name }) : ""}
          ${btn(icon("code") + "code", { act: "open-code", path: p.path })}
          ${btn(icon("shield"), { act: "snapshot", path: p.path, title: "Snapshot this project to a zip" })}
        </span></div>
    </div>`;
  }).join("") || `<div class="empty" style="grid-column:1/-1">No projects match.</div>`);
}

export function bindProjects() {
  $("projFilter").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-f]");
    if (b) setProjectFilter(b.dataset.f);
  });
  $("projSort").addEventListener("change", (e) => { projUi.sort = e.target.value; if (S.state) renderProjects(S.state); });
  $("projSearch").addEventListener("input", (e) => { projUi.q = e.target.value.trim(); if (S.state) renderProjects(S.state); });
}

// ═══════════════════════════ Systems ═════════════════════════════════════

function toolRows(items, kind) {
  return items.map((it) =>
    `<div class="row click" data-kind="${kind}" data-name="${esc(it.name)}" data-label="${esc(it.label || it.name)}">
       <span class="led ${it.present ? "on" : ""}"></span><span class="name grow">${esc(it.label || it.name)}</span>
       <span class="val ${it.present ? "" : "miss"}">${it.present ? esc(it.version || "installed") : "not installed"}</span></div>`).join("");
}

export function renderSystems(s) {
  const inv = s.inventory, meta = s.meta;
  const fresh = (n) => (meta[n] && meta[n].ts ? "checked " + ago(meta[n].ts) : meta[n] && meta[n].refreshing ? "scanning…" : "");
  if (meta.runtimes.ts) setHtml($("runtimes"), toolRows(inv.runtimes.filter((r) => r.present), "tool"));
  if (meta.dev_tools.ts) setHtml($("tools"), toolRows(inv.dev_tools, "tool"));
  $("rtHint").textContent = fresh("runtimes");
  $("toolsHint").textContent = fresh("dev_tools");

  // connectors
  const c = s.connectors, cl = c.claude, auth = cl.auth || {};
  let html = `<div class="row click" data-kind="conn" data-name="claude">
      <span class="led ${cl.present ? (auth.logged_in ? "on" : "warn") : ""}"></span><span class="name grow">Claude Code</span>
      ${cl.present && !auth.logged_in ? `<span class="tag warn">CLI signed out</span>` : cl.present ? `<span class="tag ok">signed in</span>` : ""}
      <span class="val">${cl.version ? "v" + esc(cl.version) : esc(cl.status)}</span></div>`;
  html += (c.mcp_servers || []).map((m) =>
    `<div class="row sub click" data-kind="mcp" data-name="${esc(m.name)}"><span class="led ${led(m.status)}"></span>
       <span class="name grow">mcp · ${esc(m.name)}</span><span class="val ${m.status === "online" ? "" : "miss"}">${esc(m.status)}</span></div>`).join("");
  const hc = c.hermes_chat || {};
  html += `<div class="row click" data-kind="conn" data-name="hermes_chat"><span class="led ${hc.status === "online" ? "on" : ""}"></span>
      <span class="name grow">Hermes · open-LLM chat</span><span class="val ${hc.status === "online" ? "" : "miss"}">${esc(hc.status || "offline")}</span></div>`;
  html += (hc.providers || []).map((p) =>
    `<div class="row sub" title="${esc(p.available ? (p.models || []).slice(0, 8).join(", ") : p.reason)}"><span class="led ${p.available ? "on" : ""}"></span>
       <span class="name grow">${esc(p.label || p.id)}</span>
       <span class="val ${p.available ? "" : "miss"}">${esc(p.available ? `${p.model_count ?? (p.models || []).length} models` : p.reason)}</span></div>`).join("");
  const h = c.hermes || {};
  html += `<div class="row click" data-kind="conn" data-name="hermes"><span class="led ${h.present ? "on" : ""}"></span>
      <span class="name grow">${esc(h.name || "Hermes Skills")}${h.branch ? ` <span class="muted mono">${esc(h.branch)}</span>` : ""}</span>
      <span class="val ${h.present ? "" : "miss"}">${esc(h.present ? h.version || h.status : h.status)}</span></div>`;
  html += (h.skills || []).map((sk) =>
    `<div class="row sub"><span class="led on"></span><span class="name grow">skill · ${esc(sk.name)}</span><span class="val">${esc(sk.category)}</span></div>`).join("");
  const ob = s.obsidian || {};
  html += `<div class="row click" data-act="view" data-view="vault"><span class="led ${ob.available ? "on" : ""}"></span>
      <span class="name grow">Obsidian</span><span class="val ${ob.available ? "" : "miss"}">${ob.available ? ob.notes + " notes" : "not linked"}</span></div>`;
  const oc = c.openclaw || {};
  html += `<div class="row click" data-kind="conn" data-name="openclaw"><span class="led ${oc.present ? "on" : ""}"></span>
      <span class="name grow">OpenClaw</span><span class="val ${oc.present ? "" : "miss"}">${esc(oc.version ? "v" + oc.version : oc.status || "not detected")}</span></div>`;
  if (meta.claude.ts || meta.mcp.ts) setHtml($("connectors"), html);
  $("connHint").textContent = meta.mcp.refreshing ? "checking MCP…" : fresh("mcp");

  renderUpdates(s);
  renderAutomations(s);
  renderStorage();
}

function renderUpdates(s) {
  const u = s.updates || {}, m = s.meta.updates || {};
  $("updHint").textContent = m.refreshing ? "checking — this takes a minute…" : u.checked ? "checked " + ago(u.checked) : "not checked yet";
  if (!u.checked) {
    setHtml($("updates"), `<div class="empty">${m.refreshing ? "Asking winget, npm and uv what's new…" : "No check yet — JARVIS looks every few hours, or press “check now”."}</div>`);
    return;
  }
  const group = (g, body, okText) =>
    `<div class="upd-group"><div class="upd-head"><span class="led ${g.count ? "warn" : "on"}"></span>${esc(g.label)}
       <span class="tag ${g.count ? "warn" : "ok"}">${g.count || okText || "current"}</span></div>${body}</div>`;
  let html = "";
  const w = u.winget;
  if (w) {
    html += group(w, (w.items || []).map((i) =>
      `<div class="upd-item"><span>${esc(i.label || i.name)}</span><span class="ver">${esc(i.current)} → <b>${esc(i.latest)}</b></span>
         ${btn("update", { act: "winget", id: i.id })}</div>`).join("") +
      (w.others > 0 ? `<div class="upd-item"><span class="muted">${w.others} other system updates (apps, drivers…)</span><span></span>${btn("view", { act: "winget-all" })}</div>` : ""));
  }
  const n = u.npm;
  if (n) {
    html += group(n, (n.items || []).map((i) =>
      `<div class="upd-item"><span>${esc(i.name)}</span><span class="ver">${esc(i.current)} → <b>${esc(i.latest)}</b></span>
         ${btn("update", { act: "npm-update", pkg: i.name })}</div>`).join(""));
  }
  const cl = u.claude;
  if (cl && cl.available) {
    html += `<div class="upd-group"><div class="upd-head"><span class="led on"></span>Claude Code
      <span class="tag">${esc(cl.current ? "v" + cl.current : "installed")}</span>
      <span style="margin-left:auto">${btn("update", { act: "claude-update" })}</span></div>
      <div class="upd-item"><span class="muted">${esc(cl.note)}</span></div></div>`;
  }
  const e = u.env;
  if (e) {
    html += group(e, (e.items || []).map((i) =>
      `<div class="upd-item"><span>${esc(i.name)}</span><span class="ver">${esc(i.current)} → <b>${esc(i.latest)}</b></span><span></span></div>`).join("") +
      (e.count ? `<div class="upd-item"><span class="muted">${esc(e.note)}</span></div>` : ""));
  }
  setHtml($("updates"), html || `<div class="empty">Nothing to check here.</div>`);
}

function renderAutomations(s) {
  const autos = s.automations || [];
  if (!(s.meta.automations || {}).ts) return;
  setHtml($("automations"), autos.map((t) => {
    const state = t.failed ? ["crit", "last run failed"] : !t.enabled ? ["", "disabled"]
      : t.state === "Running" ? ["accent", "running"] : ["ok", "ready"];
    return `<div class="task" title="${esc(t.command)}">
      <div class="tn"><span class="led ${t.failed ? "err" : t.enabled ? "on" : ""}"></span><span class="name grow">${esc(t.name)}</span>
        <span class="tag ${state[0]}">${state[1]}</span></div>
      <div class="td">${t.enabled && t.next_run ? "next " + esc(when(t.next_run)) : "not scheduled"}
        ${t.last_run ? " · last " + esc(when(t.last_run)) : ""}${t.failed ? ` · code ${esc(t.last_result)}` : ""}</div></div>`;
  }).join("") || `<div class="empty">No scheduled tasks belong to your projects.</div>`);
}

export function renderStorage() {
  const disks = (S.tel && S.tel.disks) || [];
  setHtml($("storage"), disks.map((d) =>
    `<div class="storage-drive ${level(d.pct, 95, 98)}">
       <div class="sd-top"><span><b>${esc(d.mount)}</b>${d.system ? ' <span class="muted">system</span>' : ""}</span>
         <span>${num(d.free_gb, d.free_gb < 10 ? 1 : 0)} GB free of ${Math.round(d.total_gb)}</span></div>
       <div class="bar"><i style="width:${Math.min(100, d.pct)}%"></i></div></div>`).join("") ||
    `<div class="empty">Reading drives…</div>`);
}

// ═══════════════════════════ Vault ═══════════════════════════════════════

let vaultSeq = 0;

function markHit(text, q) {
  const safe = esc(text);
  const words = q.trim().split(/\s+/).filter((w) => w.length > 1).map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  return words.length ? safe.replace(new RegExp(`(${words.join("|")})`, "gi"), "<mark>$1</mark>") : safe;
}

async function vaultSearch(q) {
  const seq = ++vaultSeq;
  const box = $("vaultResults");
  if (q.length < 2) {
    box.__html = null;
    setHtml(box, `<div class="empty">Type to search. Keyword matches appear instantly; matches by meaning follow from the local index.</div>`);
    return;
  }
  let kw = [];
  try { kw = await api("/api/obsidian/search?q=" + encodeURIComponent(q)); } catch { /* vault offline */ }
  if (seq !== vaultSeq) return;
  const part = (title, hits, render, empty) =>
    `<div class="sect">${title}</div>` + (hits.length ? hits.map(render).join("") : `<div class="muted" style="font-size:12.5px;padding:2px 0 8px">${empty}</div>`);
  const kwHtml = part("Exact matches", kw, (h) =>
    `<div class="hit" data-act="vault-open" data-rel="${esc(h.rel)}"><div class="ht">${markHit(h.title, q)}<span class="where">${esc(h.rel)}</span></div>
       <div class="hs">${markHit(h.snippet || "", q)}</div></div>`, "No note contains those exact words.");
  setHtml(box, kwHtml + `<div class="sect">By meaning</div><div class="skeleton-lines"><i></i><i></i></div>`);

  // Semantic hits need the embedding model; they arrive a moment later.
  let sem = [];
  try { sem = (await api("/api/rag/search?k=8&q=" + encodeURIComponent(q))).results || []; } catch { /* index offline */ }
  if (seq !== vaultSeq) return;
  const vault = (S.state && S.state.obsidian && S.state.obsidian.vault) || "";
  const semHtml = part("By meaning", sem.filter((h) => h.score >= 0.35), (h) => {
    const inVault = vault && h.path.toLowerCase().startsWith(vault.toLowerCase());
    const rel = inVault ? h.path.slice(vault.length + 1) : "";
    const attrs = inVault ? `data-act="vault-open" data-rel="${esc(rel)}"` : `data-act="open-doc" data-path="${esc(h.path)}"`;
    return `<div class="hit" ${attrs} title="${esc(h.path)}"><div class="ht">${esc(h.note)}${h.heading ? ` <span class="muted">› ${esc(h.heading)}</span>` : ""}
        <span class="where">${Math.round(h.score * 100)}% match</span></div><div class="hs">${markHit(h.text.slice(0, 320), q)}</div></div>`;
  }, S.rag && !S.rag.indexed ? "The vault isn't indexed yet — use “reindex” on the right." : "Nothing close in meaning (is LM Studio's server running?).");
  setHtml(box, kwHtml + semHtml);
}

export function renderVault(s) {
  const ob = s.obsidian || {};
  $("inboxHint").textContent = ob.available ? "→ " + (ob.inbox || "Inbox") : "vault not linked";
  setHtml($("vaultRecent"), ob.available
    ? (ob.recent || []).map((n) =>
        `<div class="row click" data-act="vault-open" data-rel="${esc(n.rel)}" title="${esc(n.rel)}">
           <span class="name grow">${esc(n.title)}</span><span class="val">${ago(n.mtime)}</span></div>`).join("")
    : `<div class="empty">No vault found.<br><span class="mono">${esc(ob.vault || "set [obsidian].vault in config.toml")}</span></div>`);
  renderVaultFoot();
}

export function renderVaultFoot() {
  const r = S.rag;
  const p = (r && r.progress) || {};
  const running = ["scanning", "converting", "embedding"].includes(p.state);
  let text = "index: checking…";
  if (r) {
    text = running ? `indexing — ${p.state}${p.new_chunks ? ` ${p.done_chunks || 0}/${p.new_chunks}` : ""}`
      : r.indexed ? `${r.chunks} chunks from ${r.files} sources · ${r.available ? "embedder online" : "embedder offline"}`
      : r.available ? "not indexed yet" : "embedder offline (start LM Studio's server)";
  }
  setHtml($("vaultFoot"),
    `<span class="led ${r && r.indexed && r.available ? "on" : r && r.indexed ? "warn" : ""}"></span><span style="flex:1">${esc(text)}</span>
     ${btn(running ? "indexing…" : "reindex", { act: "rag-reindex" })}${btn("snapshot note", { act: "ob-snapshot" })}${btn("open Obsidian", { act: "app-open", id: "obsidian" })}`);
}

export function bindVault() {
  const run = debounce((q) => vaultSearch(q), 220);
  $("vaultQuery").addEventListener("input", (e) => run(e.target.value.trim()));
  $("captureForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const ta = $("captureText");
    const text = ta.value.trim();
    if (!text) return;
    try {
      const res = await api("/api/vault/capture", { text });
      if (res.ok) { ta.value = ""; toast("Saved to your inbox", res.rel, "ok"); }
      else toast("Couldn't save the note", res.error, "warn");
    } catch { toast("Couldn't save the note", "server unreachable", "warn"); }
  });
  $("captureText").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) $("captureForm").requestSubmit();
  });
}

export function vaultQuery(q) {
  const inp = $("vaultQuery");
  inp.value = q || "";
  inp.focus();
  vaultSearch(inp.value.trim());
}

// ═══════════════════════════ Drawer ══════════════════════════════════════

const kv = (pairs) => `<div class="kv">` + pairs.filter(([, v]) => v != null && v !== "")
  .map(([k, v]) => `<span>${esc(k)}</span><b>${esc(v)}</b>`).join("") + `</div>`;

export function openDrawer(title, sub, html) {
  $("drawerTitle").textContent = title;
  $("drawerSub").textContent = sub || "";
  const body = $("drawerBody");
  body.__html = null;
  setHtml(body, html);
  $("drawer").hidden = false;
  $("scrim").hidden = false;
}
export function closeDrawer() { $("drawer").hidden = true; if ($("bellPanel").hidden) $("scrim").hidden = true; }

export async function openProject(path) {
  const p = (S.state.projects || []).find((x) => x.path === path);
  openDrawer(p ? p.name : "Project", path, `<div class="skeleton-lines"><i></i><i></i><i></i></div>`);
  let d;
  try { d = await api("/api/project?path=" + encodeURIComponent(path)); }
  catch { return setHtml($("drawerBody"), `<div class="empty">Couldn't load this project.</div>`); }
  if (!d.ok) return setHtml($("drawerBody"), `<div class="empty">${esc(d.error)}</div>`);
  const r = shieldOf(S.state).get(path);
  const cls = shieldClass(r);
  let b = `<div class="act-row">
      ${d.runnable ? `<button class="btn" data-act="run-app" data-path="${esc(d.run_path || d.path)}" data-label="${esc(d.name)}">${icon("play")}Run${d.run_label ? " · " + esc(d.run_label) : ""}</button>` : ""}
      <button class="btn ghost" data-act="open-code" data-path="${esc(d.path)}">${icon("code")}VS Code</button>
      <button class="btn ghost" data-act="open-folder" data-path="${esc(d.path)}">${icon("folder")}Folder</button>
      <button class="btn ghost" data-act="snapshot" data-path="${esc(d.path)}">${icon("shield")}Snapshot</button></div>`;
  b += `<div class="sect">Protection</div>` + kv([
    ["Status", SHIELD_LABEL[cls] + (r ? " — " + r.why : "")],
    ["Last snapshot", r && r.last_snapshot ? `${ago(r.last_snapshot)} (${r.size_mb} MB, ${r.snapshots} kept)` : "never"],
  ]);
  b += `<div class="sect">Git</div>` + (d.branch !== undefined ? kv([
    ["Branch", d.branch], ["Remote", d.remote || "none — history exists only on this disk"],
    ["Uncommitted", d.dirty_files ? d.dirty_files + " files" : "clean"],
    ["Unpushed", d.ahead ? d.ahead + " commits" : d.upstream ? "none" : "—"],
  ]) : `<div class="muted" style="margin-bottom:8px">Not a git repository.</div>`);
  if (d.commits && d.commits.length) {
    b += d.commits.map((c) => `<div class="commit"><code>${esc(c.hash)}</code><span title="${esc(c.subject)}">${esc(c.subject)}</span><span>${esc(c.when)}</span></div>`).join("");
  }
  b += `<div class="sect">Project</div>` + kv([
    ["Kind", d.kind], ["Files", d.file_count != null ? d.file_count + (d.file_count >= 20000 ? "+" : "") : "—"],
    ["Virtualenv", d.has_venv ? "yes" : "no"],
    ["Package", d.pyproject ? `${d.pyproject.name || "?"} ${d.pyproject.version || ""}` : ""],
    ["Python", d.pyproject ? d.pyproject.requires_python : ""],
  ]);
  if (d.pyproject && d.pyproject.deps && d.pyproject.deps.length) {
    b += `<div class="chips">` + d.pyproject.deps.slice(0, 30).map((x) => `<span>${esc(x)}</span>`).join("") + `</div>`;
  }
  if (d.subprojects && d.subprojects.length) {
    b += `<div class="sect">Sub-projects (${d.subprojects.length})</div>` + d.subprojects.map((sp) =>
      `<div class="doc" style="cursor:default"><span class="led ${sp.runnable ? "on" : ""}"></span><span class="dn">${esc(sp.name)}</span>
         <span class="acts">${sp.runnable ? btn("run", { act: "run-app", path: sp.path, label: sp.name }) : ""}
           ${btn("code", { act: "open-code", path: sp.path })}${btn("folder", { act: "open-folder", path: sp.path })}</span></div>`).join("");
  }
  if (d.docs && d.docs.length) {
    b += `<div class="sect">Documents (${d.docs.length})${d.docs_source ? ` <span class="muted" style="text-transform:none;letter-spacing:0">from ${esc(d.docs_source)}</span>` : ""}</div>` +
      d.docs.map((doc) => `<div class="doc" data-act="open-doc" data-path="${esc(doc.path)}" title="${esc(doc.rel)}">
        <span class="ext ${esc(doc.ext)}">${esc(doc.ext)}</span><span class="dn">${esc(doc.name)}</span></div>`).join("");
  }
  if (d.readme) b += `<div class="sect">README</div><div class="md">${renderMarkdown(d.readme)}</div>`;
  openDrawer(d.name, d.path, b);
}

const LAUNCHABLE = new Set(["python", "node", "claude", "uv", "gh"]);

/** Details for anything that isn't a project: a tool, an MCP server, a connector. */
export function openDetail(ds) {
  const s = S.state;
  if (!s) return;
  if (ds.kind === "tool") {
    const all = [...s.inventory.runtimes, ...s.inventory.dev_tools];
    const it = all.find((x) => x.name === ds.name && (!ds.label || (x.label || x.name) === ds.label)) || all.find((x) => x.name === ds.name);
    if (!it) return;
    const w = (s.updates && s.updates.winget && s.updates.winget.items) || [];
    const hit = w.find((i) => (i.label || i.name).toLowerCase().includes((it.label || "").toLowerCase().replace(/ \(active\)$/, "")));
    let b = kv([["Version", it.version || "—"], ["Status", it.present ? "installed" : "not installed"], ["Path", it.path || "—"]]);
    b += `<div class="act-row">`;
    if (it.present && LAUNCHABLE.has(it.name)) b += `<button class="btn" data-act="launch-term" data-target="${esc(it.name)}">${icon("terminal")}Open in a terminal</button>`;
    if (it.present && it.name === "code") b += `<button class="btn" data-act="launch-term" data-target="code">${icon("code")}Open VS Code</button>`;
    b += `</div>`;
    if (hit) b += `<div class="sect">Update available</div><div class="upd-item"><span>${esc(hit.label || hit.name)}</span>
      <span class="ver">${esc(hit.current)} → <b>${esc(hit.latest)}</b></span>${btn("update", { act: "winget", id: hit.id })}</div>`;
    return openDrawer(it.label || it.name, it.present ? "installed" : "not installed", b);
  }
  if (ds.kind === "mcp") {
    const m = (s.connectors.mcp_servers || []).find((x) => x.name === ds.name);
    if (!m) return;
    const cmd = m.command ? `${m.command} ${(m.args || []).join(" ")}` : m.url || "—";
    return openDrawer("MCP · " + m.name, "model context protocol server",
      kv([["Status", m.status], ["Transport", m.transport], ["Scope", m.scope]]) +
      `<div class="sect">Launch / endpoint</div><div class="codeblock">${esc(cmd)}</div>` +
      (m.status === "error" ? `<p class="muted" style="margin-top:10px">This server is failing. Check its command or token with <code>claude mcp list</code> — for the GitHub server, a rotated <code>gh</code> token is the usual cause.</p>` : "") +
      `<div class="act-row">${btn("re-check MCP health", { act: "refresh-sections", sections: "mcp" })}</div>`);
  }
  if (ds.kind === "conn") {
    const c = s.connectors;
    if (ds.name === "claude") {
      const d = c.claude.detail || {}, auth = c.claude.auth || {};
      return openDrawer("Claude Code", "AI CLI", kv([
        ["Version", c.claude.version ? "v" + c.claude.version : "—"], ["Path", c.claude.path],
        ["CLI sign-in", auth.logged_in ? `signed in (${auth.method || "ok"})` : "signed OUT"],
        ["MCP servers", `${d.mcp_online ?? 0}/${d.mcp_count ?? 0} online`],
      ]) + (!auth.logged_in && c.claude.present ? `<p class="muted">The standalone <code>claude</code> CLI isn't signed in, so JARVIS can't use your Claude subscription as its brain (it is using a fallback engine). Open a terminal and run <code>claude auth login</code> once.</p>` : "") +
        `<div class="act-row"><button class="btn" data-act="launch-term" data-target="claude">${icon("terminal")}Open a Claude terminal</button>
          ${btn("re-check", { act: "refresh-sections", sections: "claude" })}</div>`);
    }
    if (ds.name === "hermes_chat") {
      const hc = c.hermes_chat || {}, online = hc.status === "online", u = hc.usage || {};
      const provs = (hc.providers || []).map((p) =>
        `<div class="row"><span class="led ${p.available ? "on" : ""}"></span><span class="name grow"><b>${esc(p.label || p.id)}</b>
           <span class="muted"> ${esc(p.available ? (p.models || []).slice(0, 6).join(", ") + ((p.model_count || 0) > 6 ? ` +${p.model_count - 6}` : "") : p.reason)}</span></span></div>`).join("");
      return openDrawer("Hermes", "open-LLM chat relay", kv([
        ["Status", hc.status], ["URL", hc.url], ["Version", hc.version ? "v" + hc.version : "—"], ["Folder", hc.path || "—"],
        ["Spent today", online ? "$" + num(u.spent_today || 0, 4) + (u.daily_budget_usd ? ` of $${num(u.daily_budget_usd, 2)}` : "") : ""],
        ["Tokens today", online ? String(u.tokens_today || 0) : ""],
      ]) + (provs ? `<div class="sect">Providers</div>${provs}` : "") +
        `<div class="act-row"><button class="btn" data-act="app-open" data-id="hermes">${icon("play")}${online ? "Open Hermes" : "Start Hermes"}</button>
          ${hc.path ? `<button class="btn ghost" data-act="open-code" data-path="${esc(hc.path)}">${icon("code")}VS Code</button>` : ""}</div>`);
    }
    if (ds.name === "hermes") {
      const h = c.hermes || {};
      return openDrawer(h.name || "Hermes Skills", "reusable agent skills",
        kv([["Status", h.status], ["Branch", h.branch], ["Path", h.path]]) +
        ((h.skills || []).length ? `<div class="sect">Skills</div>` + h.skills.map((sk) =>
          `<div class="row"><span class="led on"></span><span class="name grow">${esc(sk.name)}</span><span class="val">${esc(sk.category)}</span></div>`).join("") : "") +
        (h.path ? `<div class="act-row"><button class="btn ghost" data-act="open-code" data-path="${esc(h.path)}">${icon("code")}VS Code</button>
          <button class="btn ghost" data-act="open-folder" data-path="${esc(h.path)}">${icon("folder")}Folder</button></div>` : ""));
    }
    if (ds.name === "openclaw") {
      const o = c.openclaw || {};
      return openDrawer("OpenClaw", "AI framework", kv([["Status", o.status], ["Path", o.path || "—"]]) +
        `<p class="muted">Not detected. Install an <code>openclaw</code> CLI and JARVIS lights this up automatically.</p>`);
    }
  }
  if (ds.kind === "launch") {
    return openDrawer(ds.name || "Launch", "library shortcut", kv([["File", ds.path]]) +
      `<div class="act-row"><button class="btn" data-act="launch-file" data-path="${esc(ds.path)}" data-label="${esc(ds.name || "")}">${icon("play")}Launch</button></div>`);
  }
}

export async function openWingetAll() {
  openDrawer("All system updates", "winget", `<div class="skeleton-lines"><i></i><i></i><i></i></div>`);
  try {
    const rows = await api("/api/winget/all");
    setHtml($("drawerBody"), rows.length ? rows.map((r) =>
      `<div class="upd-item"><span>${esc(r.name)}</span><span class="ver">${esc(r.current)} → <b>${esc(r.latest)}</b></span>${btn("update", { act: "winget", id: r.id })}</div>`).join("")
      : `<div class="empty">Everything is up to date.</div>`);
    $("drawerSub").textContent = `${rows.length} packages`;
  } catch { setHtml($("drawerBody"), `<div class="empty">winget didn't answer.</div>`); }
}

bus.on("tel", () => { if (S.view === "systems") renderStorage(); });
