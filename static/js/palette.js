// Command palette (Ctrl+K): one box to open any app or project, jump to a
// note, run an action — or, when nothing matches, hand the text to JARVIS.

import { $, S, api, bus, esc } from "./core.js";

let items = [], shown = [], sel = 0, loadedAt = 0;

const LOCAL = [
  { title: "Overview", sub: "go to view", icon: "▦", group: "Go to", run: { act: "view", view: "overview" }, keywords: "home dashboard" },
  { title: "Projects", sub: "go to view", icon: "▣", group: "Go to", run: { act: "view", view: "projects" }, keywords: "repos code" },
  { title: "Systems", sub: "go to view", icon: "⚙", group: "Go to", run: { act: "view", view: "systems" }, keywords: "runtimes tools updates mcp" },
  { title: "Ecosystem map", sub: "go to view", icon: "◉", group: "Go to", run: { act: "view", view: "map" }, keywords: "graph" },
  { title: "Vault", sub: "go to view", icon: "✎", group: "Go to", run: { act: "view", view: "vault" }, keywords: "notes obsidian search" },
  { title: "Brief me", sub: "spoken status report", icon: "◈", group: "Actions", run: { act: "brief" }, keywords: "briefing status report sitrep" },
  { title: "Rescan everything", sub: "refresh all sections now", icon: "⟳", group: "Actions", run: { act: "refresh" }, keywords: "refresh reload" },
  { title: "Check for updates", sub: "winget · npm · uv", icon: "⬆", group: "Actions", run: { act: "updates-check" }, keywords: "upgrade" },
  { title: "Snapshot every project at risk", sub: "zip backups of unprotected work", icon: "🛡", group: "Actions", run: { act: "snapshot-all" }, keywords: "backup shield protect" },
  { title: "Open the backups folder", sub: "project snapshots", icon: "🗀", group: "Actions", run: { act: "shield-open" }, keywords: "snapshots zip" },
  { title: "Quick note", sub: "capture to your Obsidian inbox", icon: "＋", group: "Actions", run: { act: "capture" }, keywords: "capture jot inbox" },
  { title: "Write a system snapshot note", sub: "dated report into the vault", icon: "✎", group: "Actions", run: { act: "ob-snapshot" }, keywords: "obsidian log" },
  { title: "Reindex vault memory", sub: "rebuild the local search index", icon: "⟳", group: "Actions", run: { act: "rag-reindex" }, keywords: "rag embeddings" },
  { title: "Toggle voice", sub: "mute / unmute JARVIS", icon: "🔊", group: "Actions", run: { act: "mute-toggle" }, keywords: "mute speak sound" },
  { title: "Alerts & activity", sub: "open the bell panel", icon: "🔔", group: "Actions", run: { act: "bell" }, keywords: "notifications" },
  { title: "Settings", sub: "brain, voice, appearance", icon: "⚙", group: "Actions", run: { act: "settings" }, keywords: "preferences theme model engine" },
  { title: "Clear the conversation", sub: "forget this chat with JARVIS", icon: "✕", group: "Actions", run: { act: "convo-clear" }, keywords: "reset" },
  { title: "Open JARVIS as an app window", sub: "voice without clicking first", icon: "⇱", group: "Actions", run: { act: "appwindow" }, keywords: "autoplay" },
];

function score(item, q) {
  if (!q) return 1;
  const title = item.title.toLowerCase();
  const hay = `${title} ${(item.keywords || "").toLowerCase()} ${(item.sub || "").toLowerCase()}`;
  if (title === q) return 100;
  if (title.startsWith(q)) return 80;
  if (title.split(/[\s\-_/]+/).some((w) => w.startsWith(q))) return 65;
  if (title.includes(q)) return 55;
  // every word of the query appears somewhere
  const words = q.split(/\s+/).filter(Boolean);
  if (words.length > 1 && words.every((w) => hay.includes(w))) return 45;
  if (hay.includes(q)) return 35;
  // in-order subsequence of the title ("qfn" → QuantFin)
  let i = 0;
  for (const ch of title) if (ch === q[i]) i++;
  return i === q.length && q.length >= 2 ? 20 : 0;
}

function render() {
  const q = $("paletteInput").value.trim().toLowerCase();
  const ranked = items.map((it) => [score(it, q), it]).filter(([s]) => s > 0);
  if (q) ranked.sort((a, b) => b[0] - a[0]);
  shown = ranked.slice(0, q ? 14 : 40).map(([, it]) => it);
  const raw = $("paletteInput").value.trim();
  if (raw) shown.push({ title: `Ask JARVIS: “${raw}”`, sub: "send this to the brain", icon: "✦", group: "Ask", ask: raw });
  sel = Math.min(sel, Math.max(0, shown.length - 1));
  let html = "", lastGroup = null;
  shown.forEach((it, i) => {
    if (!q && it.group !== lastGroup) { html += `<div class="pg">${esc(it.group)}</div>`; lastGroup = it.group; }
    if (q && it.ask && shown.length > 1) html += `<div class="pg">or</div>`;
    html += `<div class="pi ${i === sel ? "sel" : ""}" data-i="${i}">
      <span class="pico">${esc(it.icon || "•")}</span>
      <span class="ptx"><div class="ptl">${esc(it.title)}</div>${it.sub ? `<div class="psb">${esc(it.sub)}</div>` : ""}</span>
      ${it.running ? '<span class="led on" title="running"></span>' : ""}</div>`;
  });
  const list = $("paletteList");
  list.innerHTML = html || `<div class="empty">Type to search.</div>`;
  const cur = list.querySelector(".pi.sel");
  if (cur) cur.scrollIntoView({ block: "nearest" });
}

function run(it) {
  close();
  if (!it) return;
  if (it.ask) return S.convo.ask(it.ask);
  const r = it.run || {};
  if (r.kind) bus.emit("drill", r);
  else bus.emit("act", r);
}

export async function open(prefill = "") {
  $("palette").hidden = false;
  const input = $("paletteInput");
  input.value = prefill;
  sel = 0;
  if (!items.length) items = LOCAL.slice();
  render();
  input.focus();
  // server-side catalogue (apps, projects, notes): refresh when stale
  if (Date.now() - loadedAt > 15000) {
    try {
      const remote = (await api("/api/commands")).items || [];
      items = [...remote.filter((x) => x.group === "Apps"), ...LOCAL.filter((x) => x.group === "Go to"),
        ...LOCAL.filter((x) => x.group === "Actions"), ...remote.filter((x) => x.group !== "Apps")];
      loadedAt = Date.now();
      if (!$("palette").hidden) render();
    } catch { /* local commands still work */ }
  }
}
export function close() { $("palette").hidden = true; }
export const isOpen = () => !$("palette").hidden;

export function initPalette() {
  const input = $("paletteInput");
  input.addEventListener("input", () => { sel = 0; render(); });
  input.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown") { e.preventDefault(); sel = Math.min(sel + 1, shown.length - 1); render(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); sel = Math.max(sel - 1, 0); render(); }
    else if (e.key === "Enter") { e.preventDefault(); run(shown[sel]); }
    else if (e.key === "Tab") {
      e.preventDefault();
      const raw = input.value.trim();
      if (raw) { close(); S.convo.ask(raw); }
    } else if (e.key === "Escape") { e.preventDefault(); close(); }
  });
  $("paletteList").addEventListener("click", (e) => {
    const row = e.target.closest(".pi");
    if (row) run(shown[+row.dataset.i]);
  });
  $("paletteList").addEventListener("mousemove", (e) => {
    const row = e.target.closest(".pi");
    if (row && +row.dataset.i !== sel) {
      sel = +row.dataset.i;
      $("paletteList").querySelectorAll(".pi").forEach((n) => n.classList.toggle("sel", +n.dataset.i === sel));
    }
  });
  $("palette").addEventListener("mousedown", (e) => { if (e.target === $("palette")) close(); });
  bus.on("state", () => { loadedAt = 0; });       // apps/projects changed: refetch next time
}
