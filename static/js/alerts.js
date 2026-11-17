// Alerts & activity: the bell, its panel, the Overview feed card, toasts, and
// which alerts JARVIS says out loud.

import { $, S, ago, api, bus, clock, esc, setHtml, toast, until } from "./core.js";
import { voice } from "./voice.js";

let speakPolicy = "critical";      // none | critical | all  (⚙ → Voice)
let primed = false;                // the first batch is history, not news

export function setSpeakPolicy(p) { speakPolicy = p || "critical"; }

function actButton(a) {
  if (!a || !a.label) return "";
  const data = a.act ? `data-act="${esc(a.act)}" data-id="${esc(a.id || "")}"`
    : a.ui === "view" ? `data-act="view" data-view="${esc(a.view)}" data-filter="${esc(a.filter || "")}"`
    : a.ui === "settings" ? `data-act="settings"`
    : a.kind ? `data-kind="${esc(a.kind)}" data-name="${esc(a.name || "")}"` : "";
  return data ? `<button class="mini" ${data}>${esc(a.label)}</button>` : "";
}

function row(e, active) {
  const mute = e.key && ["warn", "crit", "info"].includes(e.level)
    ? `<button class="mini" data-act="alert-mute" data-key="${esc(e.key)}" title="Never show this alert again">mute</button>` : "";
  const acts = actButton(e.act) + (active ? mute : "");
  return `<div class="fi ${esc(e.level)} ${active ? "active" : ""}">
    <span class="dot"></span>
    <span class="ft"><b>${esc(e.title)}</b>${e.detail ? `<small>${esc(e.detail)}</small>` : ""}${acts ? `<span class="fa">${acts}</span>` : ""}</span>
    <span class="fw" title="${new Date(e.ts * 1000).toLocaleString()}">${Date.now() / 1000 - e.ts < 86400 ? clock(e.ts) : ago(e.ts)}</span></div>`;
}

export function renderFeed() {
  const f = S.feed;
  const activeKeys = new Set(f.active.map((a) => a.key));
  const urgent = f.active.filter((a) => a.level === "warn" || a.level === "crit");

  // bell
  const count = $("bellCount");
  count.hidden = urgent.length === 0;
  count.textContent = urgent.length;
  count.classList.toggle("crit", urgent.some((a) => a.level === "crit"));

  // Overview card: what is true now first, then what happened
  const history = f.events.filter((e) => !(e.key && activeKeys.has(e.key) && e.level !== "ok")).slice(-40).reverse();
  const reminders = f.reminders.map((r) =>
    `<div class="fi reminder"><span class="dot"></span><span class="ft"><b>${esc(r.text)}</b><small>reminder in ${until(r.due)}</small>
       <span class="fa"><button class="mini" data-act="reminder-cancel" data-id="${r.id}">cancel</button></span></span>
     <span class="fw">${clock(r.due)}</span></div>`).join("");
  const html = reminders + f.active.map((a) => row(a, true)).join("") + history.map((e) => row(e, false)).join("");
  const empty = `<div class="empty">All quiet. Alerts, finished jobs and reminders appear here.</div>`;
  setHtml($("feed"), html || empty);
  $("feedHint").textContent = urgent.length ? `${urgent.length} need attention` : f.active.length ? `${f.active.length} active` : "";
  if (!$("bellPanel").hidden) renderPanel();
}

function renderPanel() {
  const f = S.feed;
  const muted = f.muted || [];
  const activeKeys = new Set(f.active.map((a) => a.key));
  const history = f.events.filter((e) => !(e.key && activeKeys.has(e.key) && e.level !== "ok")).slice(-60).reverse();
  let html = "";
  if (f.active.length) html += `<div class="sect">Active now</div>` + f.active.map((a) => row(a, true)).join("");
  if (f.reminders.length) {
    html += `<div class="sect">Reminders</div>` + f.reminders.map((r) =>
      `<div class="fi reminder"><span class="dot"></span><span class="ft"><b>${esc(r.text)}</b><small>in ${until(r.due)}</small></span>
         <span class="fw"><button class="mini" data-act="reminder-cancel" data-id="${r.id}">cancel</button></span></div>`).join("");
  }
  html += `<div class="sect">Activity</div>` + (history.map((e) => row(e, false)).join("") || `<div class="muted" style="font-size:12.5px">Nothing yet.</div>`);
  if (muted.length) {
    html += `<div class="sect">Muted</div>` + muted.map((k) =>
      `<div class="row"><span class="name grow mono" style="font-size:12px">${esc(k)}</span>
         <button class="mini" data-act="alert-unmute" data-key="${esc(k)}">unmute</button></div>`).join("");
  }
  setHtml($("bellBody"), html);
}

export function toggleBell(force) {
  const panel = $("bellPanel");
  const open = force ?? panel.hidden;
  panel.hidden = !open;
  if (open) { renderPanel(); $("scrim").hidden = false; }
  else if ($("drawer").hidden) $("scrim").hidden = true;
}

/** Merge a feed update from the stream; toast + speak what is genuinely new. */
export function ingest(d) {
  const f = S.feed;
  const fresh = d.events || [];
  const known = new Set(f.events.map((e) => e.id));
  for (const e of fresh) if (!known.has(e.id)) f.events.push(e);
  if (f.events.length > 200) f.events = f.events.slice(-200);
  f.active = d.active || [];
  f.reminders = d.reminders || [];
  f.muted = d.muted || [];
  f.last = d.last ?? f.last;
  if (primed) {
    for (const e of fresh) {
      if (known.has(e.id)) continue;
      if (e.level === "info" && !e.detail && /is up$|stopped$/.test(e.title)) continue;   // app chatter: feed only
      toast(e.title, e.detail, e.level);
      const say = e.speak && (e.level === "reminder" || speakPolicy === "all" || (speakPolicy === "critical" && e.level === "crit"));
      if (say && voice.on) voice.say(e.speak);
    }
  }
  primed = true;
  renderFeed();
}

export async function muteAlert(key, muted = true) {
  try { ingest({ ...(await api("/api/alerts/mute", { key, muted })), events: [] }); }
  catch { toast("Couldn't change that alert", "", "warn"); }
  bus.emit("briefing-stale");
}

export async function clearFeed() {
  try { await api("/api/alerts/clear", {}); } catch { /* offline */ }
  S.feed.events = [];
  renderFeed();
}

export async function cancelReminder(id) {
  try { await api("/api/reminders/" + id, undefined, "DELETE"); } catch { /* offline */ }
  S.feed.reminders = S.feed.reminders.filter((r) => String(r.id) !== String(id));
  renderFeed();
}

// keep "in 4m" style countdowns honest between stream updates
setInterval(() => { if (S.feed.reminders.length) renderFeed(); }, 15000);
