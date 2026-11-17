// The command dock: ask JARVIS (typed or spoken), streamed answers, actions.
//
// Answers stream in token by token and are spoken sentence by sentence; the
// conversation is multi-turn (follow-up questions work) and survives a reload.

import { $, S, bus, esc, icon, prefs, rafThrottle, savePrefs, sseEvents, toast } from "./core.js";
import { renderMarkdown } from "./md.js";
import { mic, voice } from "./voice.js";

const KEY = "jarvis.convo";
const MAX_KEEP = 40;

let history = [];            // [{role, content, source?, acts?}]
let asked = [];              // typed questions, for ↑ recall
let recall = -1;
let abort = null;
export let busy = false;

function load() {
  try { history = JSON.parse(localStorage.getItem(KEY) || "[]").slice(-MAX_KEEP); } catch { history = []; }
  asked = history.filter((m) => m.role === "user").map((m) => m.content);
}
function save() {
  try { localStorage.setItem(KEY, JSON.stringify(history.slice(-MAX_KEEP))); } catch { /* quota */ }
}

function setOpen(open) {
  $("convo").hidden = !open || !history.length;
  prefs.convoOpen = open;
  savePrefs();
}
export function toggleConvo(force) {
  const open = force ?? $("convo").hidden;
  if (open && !history.length) return;
  setOpen(open);
  if (open) scrollEnd();
}
function scrollEnd() { const log = $("convoLog"); log.scrollTop = log.scrollHeight; }

function bubble(m) {
  const el = document.createElement("div");
  el.className = "msg " + (m.role === "user" ? "you" : "jarvis") + (m.error ? " err" : "");
  const acts = (m.acts || []).map((a) =>
    `<span class="acted ${a.ok ? "" : "fail"}">${icon(a.ok ? "check" : "x")}${esc(a.label)}</span>`).join("");
  el.innerHTML = m.role === "user"
    ? `<span class="av">YOU</span><div class="mb"><div class="mt">${esc(m.content)}</div></div>`
    : `<span class="av">J</span><div class="mb"><div class="mm">${esc(m.source || "")}</div>
         <div class="mt md">${renderMarkdown(m.content || "")}</div>${acts}</div>`;
  return el;
}

function renderAll() {
  const log = $("convoLog");
  log.innerHTML = "";
  for (const m of history) log.appendChild(bubble(m));
  $("convoHint").textContent = history.length ? `${Math.ceil(history.length / 2)} exchange${history.length > 2 ? "s" : ""}` : "";
  scrollEnd();
}

export function clearConvo() {
  stop();
  history = [];
  save();
  renderAll();
  $("convo").hidden = true;
}

function actLabel(name, arg, res) {
  const what = { open: "opened", stop: "stopped", code: "VS Code", run: "ran", project: "showed", note: "noted",
    remind: "reminder set", snapshot: "snapshot", updates: "checking updates", brief: "briefing", view: "view",
    vault: "vault search", folder: "opened folder", claude: "Claude terminal", refresh: "rescanning" }[name] || name;
  return res.ok ? `${what}${arg && !["note", "remind"].includes(name) ? " · " + arg : ""}` : `${name} failed`;
}

function setBusy(on) {
  busy = on;
  const b = $("askBtn");
  b.classList.toggle("stop", on);
  b.title = on ? "Stop (Esc)" : "Send (Enter)";
  b.innerHTML = icon(on ? "stop" : "send");
  $("brainChip").classList.toggle("busy", on);
  bus.emit("thinking", on);
}

export function stop() {
  if (abort) abort.abort();
  voice.stop();
}

/** Ask the brain. Resolves when the answer has finished streaming. */
export async function ask(question, { spoken = false } = {}) {
  question = (question || "").trim();
  if (!question) return;
  if (busy) stop();
  asked.push(question);
  recall = -1;

  const prior = history.slice(-12).map((m) => ({ role: m.role, content: m.content }));
  const userMsg = { role: "user", content: question };
  history.push(userMsg);
  const log = $("convoLog");
  log.appendChild(bubble(userMsg));
  const reply = { role: "assistant", content: "", source: "", acts: [] };
  const el = bubble(reply);
  const meta = el.querySelector(".mm"), body = el.querySelector(".mt");
  body.innerHTML = `<span class="thinking"><i></i><i></i><i></i></span>`;
  log.appendChild(el);
  $("convo").hidden = false;
  prefs.convoOpen = true;
  scrollEnd();

  setBusy(true);
  voice.stop();
  voice.begin();
  const paint = rafThrottle(() => {
    const stick = log.scrollHeight - log.scrollTop - log.clientHeight < 60;
    body.innerHTML = renderMarkdown(reply.content) + '<span class="caret"></span>';
    if (stick) scrollEnd();
  });
  abort = new AbortController();
  let failed = "";
  try {
    const resp = await fetch("/api/ask/stream", {
      method: "POST", headers: { "Content-Type": "application/json" }, signal: abort.signal,
      body: JSON.stringify({ question, history: prior }),
    });
    if (!resp.ok || !resp.body) throw new Error("HTTP " + resp.status);
    for await (const ev of sseEvents(resp)) {
      if (ev.t === "start") {
        reply.source = ev.label === "instant" ? "" : ev.label;
        meta.textContent = reply.source;
      } else if (ev.t === "reasoning") {
        if (!reply.content) body.innerHTML = `<span class="thinking"><i></i><i></i><i></i>&nbsp;reasoning…</span>`;
      } else if (ev.t === "delta") {
        reply.content += ev.d;
        voice.feed(ev.d);
        paint();
      } else if (ev.t === "fallback") {
        meta.textContent = `${ev.label} unavailable (${ev.reason}) — trying the next engine…`;
        reply.content = "";
      } else if (ev.t === "action") {
        const res = ev.result || {};
        reply.acts.push({ ok: !!res.ok, label: actLabel(ev.name, ev.arg, res) });
        if (res.ui) bus.emit("ui", res.ui);
      } else if (ev.t === "done") {
        reply.content = ev.answer || reply.content;
        if (ev.source && ev.source !== "instant") reply.source = `${ev.source} · ${(ev.ms / 1000).toFixed(1)}s`;
      } else if (ev.t === "error") {
        failed = ev.error;
      }
    }
  } catch (e) {
    if (e.name === "AbortError") reply.content += reply.content ? " — stopped." : "Stopped.";
    else failed = "I can't reach the server — is JARVIS still running?";
  }
  if (failed) {
    reply.content = reply.content ? reply.content + "\n\n⚠ " + failed : "⚠ " + failed;
    reply.error = true;
    voice.stop();
  } else {
    voice.end();
  }
  history.push(reply);
  save();
  const final = bubble(reply);
  el.replaceWith(final);
  $("convoHint").textContent = `${Math.ceil(history.length / 2)} exchange${history.length > 2 ? "s" : ""}`;
  scrollEnd();
  setBusy(false);
  abort = null;
  if (failed && !spoken) toast("The brain couldn't answer", failed.slice(0, 160), "warn");
  bus.emit("asked");
}

export function initConsole() {
  load();
  renderAll();
  if (history.length && prefs.convoOpen !== false) $("convo").hidden = false;

  const input = $("askInput");
  $("askForm").addEventListener("submit", (e) => {
    e.preventDefault();
    const q = input.value.trim();
    if (!q) return;
    input.value = "";
    ask(q);
  });
  $("askBtn").addEventListener("click", () => {
    if (busy) return stop();
    $("askForm").requestSubmit();
  });
  input.addEventListener("keydown", (e) => {
    if (e.key === "ArrowUp" && (input.value === "" || recall >= 0) && asked.length) {
      e.preventDefault();
      recall = recall < 0 ? asked.length - 1 : Math.max(0, recall - 1);
      input.value = asked[recall];
    } else if (e.key === "ArrowDown" && recall >= 0) {
      e.preventDefault();
      recall++;
      if (recall >= asked.length) { recall = -1; input.value = ""; } else input.value = asked[recall];
    }
  });
  input.addEventListener("focus", () => { $("askHint").textContent = history.length ? "↑ recalls · Esc hides" : ""; });
  input.addEventListener("blur", () => { $("askHint").innerHTML = "<kbd>/</kbd> to focus"; });

  // voice in
  bus.on("mic:text", (text) => ask(text, { spoken: true }));
  bus.on("mic:error", (msg) => toast("Voice input", msg, "warn"));
  bus.on("mic", () => {
    const b = $("micBtn");
    b.hidden = mic.device === "off";
    b.classList.toggle("listening", mic.recording);
    b.classList.toggle("busy", mic.busy);
    b.title = mic.recording ? "Listening — it sends when you stop talking (Esc cancels)"
      : mic.busy ? "Transcribing…" : "Click and speak";
  });
  $("micBtn").addEventListener("click", () => mic.toggle());
  $("dockOrbBtn").addEventListener("click", () => {
    if (voice.blocked) return voice.unlock();
    if (mic.device !== "off") return mic.toggle();
    if (history.length) return toggleConvo();
    input.focus();
  });
  bus.emit("mic", mic);
  S.convo = { ask };
}
