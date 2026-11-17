// JARVIS HUD — boot, routing, live data flow, and the one place every button
// lands (`act`). Buttons carry data-act / data-kind attributes instead of
// inline handlers, so Windows paths never pass through a JS string literal.

import { $, S, api, bus, debounce, esc, hydrateIcons, icon, qsa, toast } from "./core.js";
import * as alerts from "./alerts.js";
import * as convo from "./console.js";
import { initMap, loadMap, mapFind, mapFit, mapZoom, mountMap, setMapActive } from "./map.js";
import { Orb } from "./orb.js";
import * as palette from "./palette.js";
import * as settings from "./settings.js";
import { initVitals, renderProcs, seedHistory, updateVitals } from "./telemetry.js";
import * as views from "./views.js";
import { mic, openAppWindow, voice } from "./voice.js";

const VIEWS = ["overview", "projects", "systems", "map", "vault"];
let es = null, hiddenTimer = null, online = true, orbs = [];

// ── connection pill ───────────────────────────────────────────────────────
function setOnline(ok) {
  if (ok === online) return;
  online = ok;
  const pill = $("connPill");
  pill.classList.toggle("off", !ok);
  pill.querySelector("span").textContent = ok ? "online" : "reconnecting";
}

// ── views ─────────────────────────────────────────────────────────────────
function showView(name, opts = {}) {
  if (!VIEWS.includes(name)) return;
  S.view = name;
  qsa(".view").forEach((v) => {
    const on = v.dataset.view === name;
    v.hidden = !on;
    v.classList.toggle("on", on);
  });
  qsa("#tabs button").forEach((b) => b.classList.toggle("on", b.dataset.view === name));
  if (name === "map") mountMap("full");
  if (name === "overview") mountMap("mini");
  setMapActive(name === "map" || name === "overview");
  if (name === "map" || name === "overview") loadMap();
  if (name === "projects" && opts.filter) views.setProjectFilter(opts.filter);
  if (S.state) render(S.state);
  if (name === "vault") { settings.refreshRag(); setTimeout(() => $("vaultQuery").focus(), 60); }
  try { localStorage.setItem("jarvis.view", name); } catch { /* private mode */ }
}

// ── state ─────────────────────────────────────────────────────────────────
function render(s) {
  views.renderApps(s);
  views.renderRecent(s);
  views.renderStack(s);
  if (S.view === "projects") views.renderProjects(s);
  if (S.view === "systems") views.renderSystems(s);
  if (S.view === "vault") views.renderVault(s);
  $("refreshBtn").classList.toggle("spin", Object.values(s.meta).some((m) => m.refreshing && m.ts === null));
}

async function loadState() {
  try {
    S.state = await api("/api/state");
    setOnline(true);
    render(S.state);
    bus.emit("state", S.state);
  } catch { setOnline(false); }
}
const loadStateSoon = debounce(loadState, 180);
const briefingSoon = debounce(() => views.renderBriefing(0), 900);

// ── live stream ───────────────────────────────────────────────────────────
function connect() {
  if (es) es.close();
  es = new EventSource("/api/stream?alerts=" + (S.feed.last || 0));
  es.onopen = () => setOnline(true);
  es.onerror = () => setOnline(false);          // EventSource retries by itself
  es.onmessage = (m) => {
    let ev;
    try { ev = JSON.parse(m.data); } catch { return; }
    if (ev.t === "tel") { updateVitals(ev.d); bus.emit("tel", ev.d); }
    else if (ev.t === "procs") renderProcs(ev.d);
    else if (ev.t === "state") { loadStateSoon(); briefingSoon(); if (ev.sections.includes("claude") || ev.sections.includes("hermes_chat")) settings.refreshBrain(); }
    else if (ev.t === "alerts") alerts.ingest(ev.d);
  };
}

document.addEventListener("visibilitychange", () => {
  clearTimeout(hiddenTimer);
  if (document.hidden) {
    // A hidden window stops costing anything after half a minute.
    hiddenTimer = setTimeout(() => { if (es) { es.close(); es = null; } setMapActive(false); }, 30000);
  } else {
    if (!es) { loadState(); connect(); }
    setMapActive(S.view === "map" || S.view === "overview");
  }
});

// ── actions ───────────────────────────────────────────────────────────────
async function post(path, body, okMsg, el) {
  if (el) el.disabled = true;
  try {
    const res = await api(path, body || {});
    if (res.ok === false) toast("That didn't work", res.error || res.say || "", "warn");
    else if (okMsg) toast(okMsg, typeof res.plan === "string" ? res.plan : "", "ok");
    return res;
  } catch { toast("Couldn't reach the server", "", "warn"); return { ok: false }; }
  finally { if (el) el.disabled = false; }
}

async function longJob(el, label, fn) {
  const text = el ? el.innerHTML : "";
  if (el) { el.disabled = true; el.textContent = "working…"; }
  toast(label, "This can take a minute — I'll tell you when it's done.");
  let res;
  try { res = await fn(); } catch { res = { ok: false, error: "server unreachable" }; }
  if (el && el.isConnected) { el.disabled = false; el.innerHTML = text; }
  if (res.ok) { toast(label + " — done", "", "ok"); if (voice.on) voice.say("Update complete."); }
  else toast(label + " failed", String(res.error || res.output || "").slice(-240), "warn");
  loadState();
}

async function act(ds, el) {
  switch (ds.act) {
    case "view": return showView(ds.view, { filter: ds.filter });
    case "settings": return settings.openSettings();
    case "settings-close": return settings.closeSettings();
    case "bell": return alerts.toggleBell();
    case "drawer-close": return views.closeDrawer();
    case "convo-clear": return convo.clearConvo();
    case "convo-toggle": return convo.toggleConvo();
    case "feed-clear": return alerts.clearFeed();
    case "alert-mute": return alerts.muteAlert(ds.key, true);
    case "alert-unmute": return alerts.muteAlert(ds.key, false);
    case "reminder-cancel": return alerts.cancelReminder(ds.id);
    case "mute-toggle": return toggleMute();
    case "map-zoom": return mapZoom(+ds.dir);
    case "map-fit": return mapFit();
    case "map-reload": return loadMap(true);
    case "capture": showView("vault"); return setTimeout(() => $("captureText").focus(), 80);

    case "brief": {
      await views.renderBriefing(4);
      const b = S.briefing;
      if (!b) return;
      if (voice.on) voice.say(b.speech); else toast(b.headline, "Voice is muted — see the briefing on the Overview.");
      return;
    }
    case "refresh": {
      $("refreshBtn").classList.add("spin");
      await api("/api/refresh", {}).catch(() => {});
      toast("Rescanning everything", "Runtimes, MCP health, projects, apps…");
      setTimeout(() => $("refreshBtn").classList.remove("spin"), 2500);
      return;
    }
    case "refresh-sections":
      await api("/api/refresh", { sections: ds.sections.split(",") }).catch(() => {});
      return toast("Re-checking", ds.sections);

    case "app-open": {
      const app = ((S.state && S.state.apps) || []).find((a) => a.id === ds.id);
      const name = app ? app.name : ds.id;
      const tile = el && el.closest(".app");
      if (tile) tile.classList.add("busy");
      const res = await post(`/api/apps/${encodeURIComponent(ds.id)}/open`, {});
      if (tile) tile.classList.remove("busy");
      if (res.ok) toast(res.action === "starting" ? `${name} is starting` : `Opening ${name}`,
        res.action === "starting" ? "Its window will appear in a few seconds." : "", "ok");
      return;
    }
    case "app-stop": {
      if (!confirm(`Stop ${ds.name || ds.id}? Unsaved work in that app will be lost.`)) return;
      const res = await post(`/api/apps/${encodeURIComponent(ds.id)}/stop`, {});
      if (res.ok) toast(`${ds.name || ds.id} stopped`, "", "ok");
      return;
    }
    case "run-app": {
      const res = await post("/api/launch", { kind: "app", path: ds.path });
      if (res.ok) toast(`Running ${ds.label || "app"}`, res.plan || "", "ok");
      return;
    }
    case "open-code": return post("/api/open/code", { path: ds.path }, "Opening in VS Code", el);
    case "open-folder": return post("/api/open/folder", { path: ds.path }, "Opening the folder", el);
    case "open-doc": return post("/api/open/doc", { path: ds.path }, "Opening the document");
    case "launch-term": return post("/api/launch", { kind: "terminal", target: ds.target }, `Launching ${ds.target}`, el);
    case "launch-file": return post("/api/launch", { kind: "file", path: ds.path }, `Launching ${ds.label || "it"}`, el);
    case "vault-open": return post("/api/vault/open", { rel: ds.rel }, "Opening in Obsidian");
    case "appwindow": {
      const ok = await openAppWindow();
      refreshMuteBtn();
      return toast(ok ? "App window opened" : "No Chrome or Edge found", ok ? "Voice lives there — this tab is now muted." : "Opened in the default browser instead.", ok ? "ok" : "warn");
    }

    case "snapshot": {
      const res = await post("/api/shield/snapshot", { path: ds.path });
      if (res.ok) { toast(res.already ? "Already snapshotting" : `Snapshotting ${res.name}`, "I'll note it in Activity when it's saved.", "ok"); loadStateSoon(); }
      return;
    }
    case "snapshot-all": {
      const res = await post("/api/shield/snapshot", { all: true }, null, el);
      if (res.ok) { toast(`Snapshotting ${res.queued.length} project${res.queued.length === 1 ? "" : "s"}`, "Zips land in your backups folder.", "ok"); loadStateSoon(); }
      return;
    }
    case "shield-open": return post("/api/shield/open", {}, "Opening the backups folder");

    case "updates-check":
      await api("/api/refresh", { sections: ["updates"] }).catch(() => {});
      toast("Checking for updates", "winget, npm and uv — about a minute.");
      return loadStateSoon();
    case "winget": return longJob(el, `Updating ${ds.id.split(".").pop()}`, () => api("/api/tools/upgrade", { id: ds.id }));
    case "npm-update": return longJob(el, `Updating ${ds.pkg}`, () => api("/api/update", { target: "npm", package: ds.pkg }));
    case "claude-update": return longJob(el, "Updating Claude Code", () => api("/api/update", { target: "claude" }));
    case "winget-all": return views.openWingetAll();

    case "rag-reindex": {
      const res = await post("/api/rag/reindex", {});
      if (res.ok) { toast("Indexing your vaults", "Unchanged notes are skipped, so this is quick after the first run."); watchRag(); }
      return;
    }
    case "ob-snapshot": {
      const res = await post("/api/obsidian/snapshot", {}, null, el);
      if (res.ok) toast("Snapshot note saved", res.rel, "ok");
      return;
    }
    default: console.warn("unhandled action", ds);
  }
}

function drill(ds) {
  if (ds.kind === "project") return views.openProject(ds.path);
  if (ds.kind === "app") return act({ act: "app-open", id: ds.id });
  if (ds.kind === "note") return act({ act: "vault-open", rel: ds.rel });
  if (["gamecenter", "emucenter", "moviecenter"].includes(ds.kind)) return act({ act: "app-open", id: ds.kind });
  if (ds.kind === "applaunch") return act({ act: "app-open", id: ds.app });
  return views.openDetail(ds);
}

async function watchRag() {
  if (await settings.refreshRag()) setTimeout(watchRag, 1500);
  else { views.renderVaultFoot(); toast("Vault memory ready", S.rag && S.rag.indexed ? `${S.rag.chunks} chunks indexed` : "", "ok"); }
  views.renderVaultFoot();
}

// ── voice chrome ──────────────────────────────────────────────────────────
function refreshMuteBtn() {
  const b = $("muteBtn");
  b.innerHTML = icon(voice.on ? "volume" : "mute");
  b.classList.toggle("off", !voice.on);
  b.classList.toggle("attn", voice.blocked);
  b.title = voice.blocked ? "Click anywhere to let JARVIS speak" : voice.on ? "Voice on — click to mute (Ctrl+M)" : "Voice muted — click to unmute (Ctrl+M)";
}
function toggleMute() { voice.setOn(!voice.on); toast(voice.on ? "Voice on" : "Voice muted", "", ""); }

function voiceCue(show) {
  let cue = $("voiceCue");
  if (!show) { if (cue) cue.remove(); return; }
  if (cue) return;
  cue = document.createElement("div");
  cue.id = "voiceCue";
  cue.className = "voice-cue";
  cue.innerHTML = `${icon("volume")}<span>Click anywhere to let JARVIS speak</span><button data-act="appwindow" title="Reopen JARVIS in its app window, where voice needs no click">open as app window</button>`;
  document.body.appendChild(cue);
}

function orbMode() {
  const mode = mic.recording ? "listening" : voice.speaking ? "speaking" : convo.busy ? "thinking" : "idle";
  for (const o of orbs) o.setMode(mode);
}

// ── boot ──────────────────────────────────────────────────────────────────
function tickClock() {
  $("clock").textContent = new Date().toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
}

async function greet() {
  await views.renderBriefing(6);
  const b = S.briefing;
  let st = {};
  try { st = await settings.loadSettings(); } catch { /* defaults */ }
  if (!b || st.greet_on_start === false || !voice.on) return;
  // one spoken greeting per ten minutes — a quick reload stays quiet
  let last = 0;
  try { last = +localStorage.getItem("jarvis.greeted") || 0; } catch { /* private mode */ }
  if (Date.now() - last < 600000) return;
  try { localStorage.setItem("jarvis.greeted", String(Date.now())); } catch { /* private mode */ }
  voice.say(b.speech);
}

async function boot() {
  hydrateIcons();
  tickClock();
  setInterval(tickClock, 5000);
  initVitals();
  convo.initConsole();
  palette.initPalette();
  settings.initSettings();
  views.bindProjects();
  views.bindVault();
  alerts.renderFeed();

  const src = { level: () => (mic.recording ? mic.level() : voice.level()), spectrum: () => voice.spectrum() };
  orbs = [new Orb($("heroOrb"), src), new Orb($("dockOrb"), src)];
  bus.on("voice", () => { orbMode(); refreshMuteBtn(); voiceCue(voice.blocked); });
  bus.on("mic", orbMode);
  bus.on("thinking", orbMode);
  bus.on("act", (ds) => act(ds));
  bus.on("drill", drill);
  bus.on("briefing-stale", briefingSoon);
  bus.on("asked", () => { loadStateSoon(); });
  bus.on("ui", (ui) => {
    if (ui.do === "view") showView(ui.view);
    else if (ui.do === "project") views.openProject(ui.path);
    else if (ui.do === "vault") { showView("vault"); views.vaultQuery(ui.query); }
    else if (ui.do === "mute") voice.setOn(false);
    else if (ui.do === "unmute") voice.setOn(true);
  });
  bus.on("rag", () => { if (S.view === "vault") views.renderVaultFoot(); });
  refreshMuteBtn();

  // delegated clicks: actions first, then drill-down rows
  document.addEventListener("click", (e) => {
    voice.unlock();
    const a = e.target.closest("[data-act]");
    if (a) { e.preventDefault(); act({ ...a.dataset }, a); return; }
    const k = e.target.closest("[data-kind]");
    if (k) drill({ ...k.dataset });
  });
  document.addEventListener("click", (e) => {
    const copy = e.target.closest("[data-copy]");
    if (copy) {
      const code = copy.closest(".code")?.querySelector("code");
      if (code) navigator.clipboard.writeText(code.textContent).then(() => { copy.textContent = "copied"; setTimeout(() => { copy.textContent = "copy"; }, 1200); });
    }
  });
  $("tabs").addEventListener("click", (e) => { const b = e.target.closest("button[data-view]"); if (b) showView(b.dataset.view); });
  $("paletteBtn").addEventListener("click", () => palette.open());
  $("bellBtn").addEventListener("click", (e) => { e.stopPropagation(); alerts.toggleBell(); });
  $("muteBtn").addEventListener("click", toggleMute);
  $("refreshBtn").addEventListener("click", () => act({ act: "refresh" }));
  $("settingsBtn").addEventListener("click", () => settings.openSettings());
  $("brainChip").addEventListener("click", () => settings.openSettings());
  $("vaultChip").addEventListener("click", () => showView("vault"));
  $("scrim").addEventListener("click", () => { views.closeDrawer(); alerts.toggleBell(false); });
  $("mapSearch").addEventListener("input", debounce((e) => mapFind(e.target.value), 200));

  document.addEventListener("keydown", (e) => {
    const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName || "");
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); return palette.isOpen() ? palette.close() : palette.open(); }
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "m") { e.preventDefault(); return toggleMute(); }
    if (e.key === "Escape") {
      if (palette.isOpen()) return palette.close();
      if (!$("settingsModal").hidden) return settings.closeSettings();
      if (mic.recording) { mic.cancel(); return toast("Voice input cancelled"); }
      if (!$("drawer").hidden) return views.closeDrawer();
      if (!$("bellPanel").hidden) return alerts.toggleBell(false);
      if (convo.busy) return convo.stop();
      if (voice.speaking) return voice.stop();
      if (!$("convo").hidden) return convo.toggleConvo(false);
      if (typing) document.activeElement.blur();
      return;
    }
    if (typing || e.ctrlKey || e.metaKey || e.altKey) return;
    if (e.key === "/") { e.preventDefault(); $("askInput").focus(); }
    else if (e.key >= "1" && e.key <= "5") showView(VIEWS[+e.key - 1]);
    else if (e.key === "Enter" && document.activeElement?.classList.contains("pcard")) document.activeElement.click();
  });

  // first paint from whatever the server already knows (instant), then go live
  await loadState();
  try { const t = await api("/api/telemetry"); seedHistory(t.history); updateVitals(t.latest, false); renderProcs(t.procs); } catch { /* stream will fill in */ }
  try { alerts.ingest(await api("/api/alerts?since=0")); } catch { /* stream will fill in */ }
  connect();

  // the map script is deferred: wait for it, then mount
  const mapReady = () => {
    if (!initMap()) return false;
    let saved = "overview";
    try { saved = localStorage.getItem("jarvis.view") || "overview"; } catch { /* private mode */ }
    showView(VIEWS.includes(saved) ? saved : "overview");
    return true;
  };
  if (!mapReady()) {
    let tries = 0;
    const t = setInterval(() => {
      if (mapReady() || ++tries > 40) {
        clearInterval(t);
        if (tries > 40) $("mapHostMini").innerHTML = `<div class="empty">The map library couldn't load (no internet?). Everything else works.</div>`;
      }
    }, 150);
    showView("overview");
  }

  settings.refreshBrain();
  settings.refreshRag();
  greet();
  setInterval(settings.refreshBrain, 60000);
  window.addEventListener("resize", debounce(() => { if (S.tel) updateVitals(S.tel, false); }, 150));
}

window.addEventListener("error", (e) => console.error("HUD error:", e.message));
boot().catch((e) => {
  console.error(e);
  document.body.insertAdjacentHTML("beforeend",
    `<div class="toast crit" style="position:fixed;left:20px;bottom:90px;z-index:99"><b>JARVIS HUD failed to start</b><small>${esc(e.message)}</small></div>`);
});
