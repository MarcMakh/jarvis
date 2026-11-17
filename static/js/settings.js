// Settings panel: which engine is JARVIS's brain, voice + microphone, look.
// Server-side choices go to /api/settings (data/settings.json); look-and-feel
// choices stay in this browser profile.

import { $, S, api, bus, esc, prefs, savePrefs, setHtml, toast } from "./core.js";
import { setSpeakPolicy } from "./alerts.js";
import { mic, voice } from "./voice.js";

const THEMES = [["arc", "#4cd8ff", "Arc"], ["mark", "#ff8a4c", "Mark"], ["emerald", "#3ddc97", "Emerald"],
  ["violet", "#a78bfa", "Violet"], ["stealth", "#cdd7e3", "Stealth"]];
const ENGINE_NAMES = { "claude-cli": "Claude (your subscription, via the CLI)", sdk: "Claude API (ANTHROPIC_API_KEY)",
  hermes: "Hermes (any model it can reach)", local: "Local model (LM Studio, direct)" };

let settings = {}, options = null;

export async function loadSettings() {
  try { settings = await api("/api/settings"); setSpeakPolicy(settings.speak_alerts); } catch { /* offline */ }
  return settings;
}

export async function refreshBrain() {
  try { S.brain = await api("/api/brain/status"); } catch { S.brain = null; }
  const chip = $("brainChip");
  const b = S.brain;
  chip.classList.remove("ready", "fallback", "down");
  if (!b || !b.ready) {
    chip.classList.add("down");
    chip.querySelector("span").textContent = b ? "no brain engine" : "brain offline";
    chip.title = b ? "No engine can answer right now — click to see why" : "Can't reach the server";
  } else {
    chip.classList.add(b.fallback ? "fallback" : "ready");
    chip.querySelector("span").textContent = b.label;
    const first = b.engines[0];
    const active = b.engines.find((e) => e.id === b.engine) || {};
    if (active.cold) chip.title = "The model isn't loaded yet — the first answer takes about a minute while it loads. ";
    else chip.title = "";
    chip.title += b.fallback
      ? `Using a fallback — ${first.label} is unavailable: ${first.reason}. Click for brain settings.`
      : "This engine answers your questions. Click for brain settings.";
  }
  bus.emit("brain", b);
}

export async function refreshRag() {
  try { S.rag = await api("/api/rag/status"); } catch { S.rag = null; }
  const chip = $("vaultChip"), r = S.rag;
  chip.classList.remove("ready", "fallback", "down");
  const p = (r && r.progress) || {};
  const running = ["scanning", "converting", "embedding"].includes(p.state);
  let label = "vault offline";
  if (r) {
    if (running) { label = `indexing ${p.done_chunks || 0}/${p.new_chunks || "…"}`; chip.classList.add("fallback"); }
    else if (r.indexed) { label = `vault · ${r.chunks} chunks`; chip.classList.add(r.available ? "ready" : "fallback"); }
    else { label = r.available ? "vault · not indexed" : "vault · embedder offline"; chip.classList.add("down"); }
  }
  chip.querySelector("span").textContent = label;
  bus.emit("rag", r);
  return running;
}

function brainSection() {
  const b = (options && options.status) || S.brain || { engines: [] };
  const rows = (b.engines || []).map((e) =>
    `<div class="engine ${e.id === b.engine ? "active" : ""}"><span class="led ${e.ready ? "on" : ""}"></span>
       <span><b>${esc(e.label)}</b>${e.id === b.engine ? ' <span class="tag accent">answering now</span>' : ""}</span>
       <span class="er">${esc(e.ready ? "ready" : e.reason)}</span></div>`).join("");
  const provs = (options && options.hermes) || [];
  const curP = settings.brain_hermes_provider || "";
  const prov = provs.find((p) => p.id === curP);
  const models = prov ? prov.models : [];
  return `<div class="sect">Brain</div>
    <div class="field"><label>Answer with</label>
      <select id="setEngine">
        <option value="auto">Automatic — first engine that is ready (recommended)</option>
        ${Object.entries(ENGINE_NAMES).map(([id, name]) => `<option value="${id}">${esc(name)} first</option>`).join("")}
      </select>
      <div class="help">JARVIS tries the engines in order and falls through to the next when one can't answer.</div></div>
    ${rows}
    <div class="field-row" style="margin-top:10px">
      <div class="field"><label>Hermes provider</label>
        <select id="setHProv"><option value="">Automatic</option>
          ${provs.map((p) => `<option value="${esc(p.id)}" ${p.available ? "" : "disabled"}>${esc(p.label || p.id)}${p.available ? "" : " — unavailable"}</option>`).join("")}</select></div>
      <div class="field"><label>Hermes model</label>
        <select id="setHModel" ${prov ? "" : "disabled"}><option value="">Provider default</option>
          ${models.map((m) => `<option value="${esc(m)}">${esc(m)}${(prov.loaded || []).includes(m) ? " · loaded" : ""}</option>`).join("")}</select></div>
    </div>
    <div class="help" style="margin:-4px 0 10px">Local models are free and private; one that is already loaded answers in seconds, a cold one takes about a minute to load first.</div>
    <div class="act-row"><button class="btn ghost" id="setTest">Test the brain</button><span class="muted" id="setTestOut" style="align-self:center;font-size:12.5px"></span></div>`;
}

async function voiceSection() {
  const devs = await mic.devices();
  return `<div class="sect">Voice</div>
    <label class="switch"><input type="checkbox" id="setVoice" ${voice.on ? "checked" : ""}>Speak replies and alerts</label>
    <label class="switch"><input type="checkbox" id="setGreet" ${settings.greet_on_start !== false ? "checked" : ""}>Greet me with a briefing when JARVIS opens</label>
    <div class="field-row" style="margin-top:8px">
      <div class="field"><label>Say alerts aloud</label>
        <select id="setSpeak"><option value="none">Never</option><option value="critical">Critical only</option><option value="all">All alerts</option></select></div>
      <div class="field"><label>Microphone</label>
        <select id="setMic"><option value="off">Off — typing only</option>
          ${devs.map((d, i) => `<option value="${esc(d.deviceId || "default")}">${esc(d.label || "Microphone " + (i + 1))}</option>`).join("")}</select></div>
    </div>
    <label class="switch"><input type="checkbox" id="setAutoStop" ${prefs.autoStop !== false ? "checked" : ""}>Send automatically when I stop talking</label>`;
}

function lookSection() {
  return `<div class="sect">Appearance</div>
    <div class="field"><label>Accent</label><div class="swatches">
      ${THEMES.map(([id, col, name]) => `<button class="swatch ${prefs.theme === id ? "on" : ""}" data-theme="${id}" title="${name}" style="background:${col};color:${col}"></button>`).join("")}</div></div>
    <label class="switch"><input type="checkbox" id="setFloat" ${prefs.float !== false ? "checked" : ""}>Ambient motion (cards gently float)</label>
    <div class="sect">Shortcuts</div>
    <div class="keys">
      <span><kbd>Ctrl</kbd> <kbd>K</kbd></span><span>Command palette — open anything, or ask</span>
      <span><kbd>/</kbd></span><span>Focus the ask bar</span>
      <span><kbd>1</kbd>–<kbd>5</kbd></span><span>Switch view</span>
      <span><kbd>Ctrl</kbd> <kbd>M</kbd></span><span>Mute / unmute voice</span>
      <span><kbd>Esc</kbd></span><span>Close a panel · stop an answer · cancel the mic</span>
      <span><kbd>↑</kbd></span><span>Recall previous questions (in the ask bar)</span>
    </div>
    <div class="sect">About</div>
    <div class="help">JARVIS v${esc(S.state ? S.state.version : "?")} · settings saved to <code>data/settings.json</code> · everything else lives in <code>config.toml</code>.</div>`;
}

async function save(patch) {
  try { settings = await api("/api/settings", patch); setSpeakPolicy(settings.speak_alerts); }
  catch { return toast("Couldn't save that setting", "", "warn"); }
  await refreshBrain();
}

async function render() {
  const body = $("settingsBody");
  body.__html = null;
  setHtml(body, brainSection() + await voiceSection() + lookSection());
  $("setEngine").value = settings.brain_engine || "auto";
  $("setHProv").value = settings.brain_hermes_provider || "";
  $("setHModel").value = settings.brain_hermes_model || "";
  $("setSpeak").value = settings.speak_alerts || "critical";
  $("setMic").value = [...$("setMic").options].some((o) => o.value === mic.device) ? mic.device : "off";
}

export async function openSettings() {
  $("settingsModal").hidden = false;
  await loadSettings();
  await render();
  try { options = await api("/api/brain/options"); } catch { options = null; }
  if (!$("settingsModal").hidden) await render();
}
export function closeSettings() { $("settingsModal").hidden = true; }

export function applyTheme(id) {
  prefs.theme = id;
  document.documentElement.dataset.theme = id;
  savePrefs();
}

export function initSettings() {
  const body = $("settingsBody");
  body.addEventListener("change", async (e) => {
    const t = e.target;
    if (t.id === "setEngine") { await save({ brain_engine: t.value }); options = null; openSettings(); }
    else if (t.id === "setHProv") { await save({ brain_hermes_provider: t.value, brain_hermes_model: "" }); openSettings(); }
    else if (t.id === "setHModel") { await save({ brain_hermes_model: t.value }); openSettings(); }
    else if (t.id === "setSpeak") save({ speak_alerts: t.value });
    else if (t.id === "setGreet") save({ greet_on_start: t.checked });
    else if (t.id === "setVoice") voice.setOn(t.checked);
    else if (t.id === "setAutoStop") { prefs.autoStop = t.checked; savePrefs(); }
    else if (t.id === "setFloat") {
      prefs.float = t.checked; savePrefs();
      if (t.checked) delete document.documentElement.dataset.float; else document.documentElement.dataset.float = "off";
    } else if (t.id === "setMic") {
      const res = await mic.setDevice(t.value);
      if (!res.ok) { toast("Microphone", res.error, "warn"); t.value = "off"; }
      else if (t.value !== "off") { render(); toast("Microphone ready", "Click the mic or the orb and speak.", "ok"); }
    }
  });
  body.addEventListener("click", async (e) => {
    const sw = e.target.closest(".swatch");
    if (sw) { applyTheme(sw.dataset.theme); body.querySelectorAll(".swatch").forEach((n) => n.classList.toggle("on", n === sw)); return; }
    if (e.target.id === "setTest") {
      const out = $("setTestOut");
      e.target.disabled = true;
      out.textContent = "asking…";
      try {
        const r = await api("/api/brain/test", {});
        out.textContent = r.ok ? `✓ ${r.source} answered in ${(r.ms / 1000).toFixed(1)}s` : `✕ ${r.answer}`;
        if (r.skipped && r.skipped.length) out.title = "Skipped: " + r.skipped.join(" | ");
      } catch { out.textContent = "✕ couldn't reach the server"; }
      e.target.disabled = false;
      refreshBrain();
    }
  });
  $("settingsModal").addEventListener("mousedown", (e) => { if (e.target === $("settingsModal")) closeSettings(); });
}
