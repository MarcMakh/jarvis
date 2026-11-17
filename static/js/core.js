// JARVIS HUD — shared helpers: DOM, API, formatting, icons, toasts, prefs, events.

export const $ = (id) => document.getElementById(id);
export const qs = (sel, root = document) => root.querySelector(sel);
export const qsa = (sel, root = document) => [...root.querySelectorAll(sel)];

const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
export const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ESC[c]);

/** GET when `body` is undefined, otherwise POST JSON. Always resolves to parsed
 *  JSON; network/HTTP failures reject so callers can show a real error. */
export async function api(path, body, method) {
  const opt = body === undefined && !method
    ? {}
    : { method: method || "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body ?? {}) };
  const r = await fetch(path, opt);
  if (!r.ok && r.status !== 422) throw new Error(`${path} → HTTP ${r.status}`);
  const text = await r.text();
  return text ? JSON.parse(text) : {};
}

// ── tiny event bus ────────────────────────────────────────────────────────
const listeners = new Map();
export const bus = {
  on(ev, fn) { (listeners.get(ev) || listeners.set(ev, []).get(ev)).push(fn); },
  emit(ev, data) { for (const fn of listeners.get(ev) || []) { try { fn(data); } catch (e) { console.error(ev, e); } } },
};

// ── shared live data ──────────────────────────────────────────────────────
export const S = {
  state: null,                 // last /api/state
  tel: null,                   // latest telemetry sample
  hist: { cpu: [], mem: [], gpu: [], vram: [], net_down: [], net_up: [] },
  procs: { cpu: [], mem: [] },
  feed: { events: [], active: [], reminders: [], last: 0, muted: [] },
  brain: null,
  rag: null,
  view: "overview",
};

// ── preferences (this browser profile) ────────────────────────────────────
const PREF_KEY = "jarvis.prefs";
const PREF_DEFAULTS = { theme: "arc", float: true, voice: true, mic: "off", convoOpen: true, autoStop: true };
export const prefs = (() => {
  try { return { ...PREF_DEFAULTS, ...JSON.parse(localStorage.getItem(PREF_KEY) || "{}") }; }
  catch { return { ...PREF_DEFAULTS }; }
})();
export function savePrefs() {
  try { localStorage.setItem(PREF_KEY, JSON.stringify(prefs)); } catch { /* private mode */ }
}

// ── formatting ────────────────────────────────────────────────────────────
export function ago(ts) {
  if (!ts) return "—";
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 45) return "just now";
  if (s < 3600) return Math.round(s / 60) + "m ago";
  if (s < 86400) return Math.round(s / 3600) + "h ago";
  if (s < 86400 * 60) return Math.round(s / 86400) + "d ago";
  return Math.round(s / (86400 * 30)) + "mo ago";
}
export function rate(bps) {
  if (bps >= 1048576) return (bps / 1048576).toFixed(bps >= 10485760 ? 0 : 1) + " MB/s";
  if (bps >= 1024) return Math.round(bps / 1024) + " KB/s";
  return Math.round(bps || 0) + " B/s";
}
export const num = (n, d = 0) => (n == null || Number.isNaN(+n) ? "—" : (+n).toFixed(d));
export const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
export function clock(ts) {
  return new Date(ts * 1000).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
}
export function until(ts) {
  const s = ts - Date.now() / 1000;
  if (s <= 0) return "now";
  if (s < 90) return Math.round(s) + "s";
  if (s < 5400) return Math.round(s / 60) + "m";
  return (s / 3600).toFixed(1) + "h";
}
/** "2026-11-17T17:15:00" -> "tomorrow 17:15" / "today 17:15" / "Nov 21 09:00" */
export function when(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(+d)) return String(iso);
  const day = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const diff = Math.round((day(d) - day(new Date())) / 86400000);
  const hm = d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
  if (diff === 0) return "today " + hm;
  if (diff === 1) return "tomorrow " + hm;
  if (diff === -1) return "yesterday " + hm;
  return d.toLocaleDateString("en-GB", { day: "numeric", month: "short" }) + " " + hm;
}
export const level = (pct, warn = 80, crit = 92) => (pct >= crit ? "crit" : pct >= warn ? "warn" : "");

/** innerHTML that skips the DOM write when nothing changed — keeps scroll
 *  position and hover state stable across the frequent state refreshes. */
export function setHtml(el, html) {
  if (!el || el.__html === html) return false;
  el.__html = html;
  el.innerHTML = html;
  hydrateIcons(el);
  return true;
}

// ── icons (inline SVG, stroke = currentColor) ─────────────────────────────
const ICONS = {
  search: '<circle cx="11" cy="11" r="7"/><path d="M16.5 16.5 21 21"/>',
  bell: '<path d="M6 16v-5a6 6 0 1 1 12 0v5l1.6 2.2H4.4L6 16z"/><path d="M10 20.5a2 2 0 0 0 4 0"/>',
  sliders: '<path d="M4 6h9M17 6h3M4 12h3M11 12h9M4 18h11M19 18h1"/><circle cx="15" cy="6" r="2"/><circle cx="9" cy="12" r="2"/><circle cx="17" cy="18" r="2"/>',
  mic: '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/>',
  send: '<path d="M4 12 20 4l-6 16-3-6.5L4 12z"/>',
  stop: '<rect x="6.5" y="6.5" width="11" height="11" rx="2"/>',
  volume: '<path d="M4 9.5v5h3.5L12 18.5v-13L7.5 9.5H4z"/><path d="M15.5 9a4 4 0 0 1 0 6M18 6.5a8 8 0 0 1 0 11"/>',
  mute: '<path d="M4 9.5v5h3.5L12 18.5v-13L7.5 9.5H4z"/><path d="m16 9.5 5 5M21 9.5l-5 5"/>',
  refresh: '<path d="M20 11a8 8 0 1 0-2.4 6"/><path d="M20 4.5V11h-6.5"/>',
  play: '<path d="M8 5.5v13l11-6.5-11-6.5z"/>',
  folder: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2.5h8a2 2 0 0 1 2 2V17a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7z"/>',
  code: '<path d="m8.5 7-5 5 5 5M15.5 7l5 5-5 5"/>',
  x: '<path d="M6 6l12 12M18 6 6 18"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  expand: '<path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/>',
  shield: '<path d="M12 3.2 19.5 6v5.6c0 4.6-3.1 7.7-7.5 9.2-4.4-1.5-7.5-4.6-7.5-9.2V6L12 3.2z"/>',
  terminal: '<path d="m5 8 4 4-4 4M12 16h7"/>',
  clock: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
  grid: '<rect x="4" y="4" width="7" height="7" rx="1.5"/><rect x="13" y="4" width="7" height="7" rx="1.5"/><rect x="4" y="13" width="7" height="7" rx="1.5"/><rect x="13" y="13" width="7" height="7" rx="1.5"/>',
  server: '<rect x="4" y="4.5" width="16" height="6" rx="1.5"/><rect x="4" y="13.5" width="16" height="6" rx="1.5"/><path d="M7.5 7.5h.01M7.5 16.5h.01"/>',
  nodes: '<circle cx="12" cy="12" r="2.6"/><circle cx="5" cy="6" r="1.8"/><circle cx="19" cy="6" r="1.8"/><circle cx="6" cy="18.5" r="1.8"/><circle cx="18.5" cy="18" r="1.8"/><path d="m10 10.2-3.6-3M14 10.2l3.6-3M10.3 14l-3 3.2M13.8 14l3.4 2.8"/>',
  book: '<path d="M5 5.5A2.5 2.5 0 0 1 7.5 3H19v15H7.5A2.5 2.5 0 0 0 5 20.5v-15z"/><path d="M5 20.5A2.5 2.5 0 0 0 7.5 21H19"/>',
  spark: '<path d="M12 3.5 13.8 10l6.7 2-6.7 2L12 20.5 10.2 14l-6.7-2 6.7-2L12 3.5z"/>',
  cpu: '<rect x="7" y="7" width="10" height="10" rx="1.5"/><path d="M10 3.5V7M14 3.5V7M10 17v3.5M14 17v3.5M3.5 10H7M3.5 14H7M17 10h3.5M17 14h3.5"/>',
  wrench: '<path d="M14.5 6.5a4.2 4.2 0 0 0 5 5L11 20a2.1 2.1 0 0 1-3-3l8.5-8.5a4.2 4.2 0 0 0-2-2z"/>',
  download: '<path d="M12 4v11M7.5 11 12 15.5 16.5 11M5 19.5h14"/>',
  disk: '<ellipse cx="12" cy="6.5" rx="7.5" ry="3"/><path d="M4.5 6.5v11c0 1.7 3.4 3 7.5 3s7.5-1.3 7.5-3v-11M4.5 12c0 1.7 3.4 3 7.5 3s7.5-1.3 7.5-3"/>',
  link: '<path d="M14 4.5h5.5V10M19.5 4.5 11 13M17.5 14v4.5a1 1 0 0 1-1 1h-11a1 1 0 0 1-1-1v-11a1 1 0 0 1 1-1H10"/>',
  check: '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
  trash: '<path d="M5 7h14M10 7V4.5h4V7M7 7l.8 12.5h8.4L17 7"/>',
  copy: '<rect x="8.5" y="8.5" width="11" height="11" rx="2"/><path d="M5.5 15.5h-1v-11h11v1"/>',
};
export function icon(name, cls = "") {
  const body = ICONS[name];
  return body ? `<svg class="ic ${cls}" viewBox="0 0 24 24" aria-hidden="true">${body}</svg>` : "";
}
/** Replace every <span data-icon="x"> placeholder under `root` with its SVG. */
export function hydrateIcons(root = document) {
  for (const el of root.querySelectorAll("[data-icon]")) {
    const svg = icon(el.dataset.icon);
    if (svg) el.outerHTML = svg;
  }
}

// ── toasts ────────────────────────────────────────────────────────────────
export function toast(title, detail = "", kind = "") {
  const box = $("toasts");
  if (!box) return;
  const t = document.createElement("div");
  t.className = "toast " + kind;
  t.innerHTML = `<b>${esc(title)}</b>${detail ? `<small>${esc(detail)}</small>` : ""}`;
  box.appendChild(t);
  while (box.children.length > 4) box.firstChild.remove();
  setTimeout(() => { t.classList.add("out"); setTimeout(() => t.remove(), 280); }, kind === "crit" ? 9000 : 4800);
}

/** Run `fn` at most once per animation frame (latest args win). */
export function rafThrottle(fn) {
  let queued = false, lastArgs;
  return (...args) => {
    lastArgs = args;
    if (queued) return;
    queued = true;
    requestAnimationFrame(() => { queued = false; fn(...lastArgs); });
  };
}
export function debounce(fn, ms) {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

/** Read `data: {json}` events off a streaming fetch Response. */
export async function* sseEvents(resp) {
  const reader = resp.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let i;
    while ((i = buf.indexOf("\n\n")) >= 0) {
      const block = buf.slice(0, i);
      buf = buf.slice(i + 2);
      for (const line of block.split("\n")) {
        if (!line.startsWith("data:")) continue;
        try { yield JSON.parse(line.slice(5).trim()); } catch { /* keep-alive / partial */ }
      }
    }
  }
}
