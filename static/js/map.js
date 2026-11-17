// Ecosystem map — a flat 2D force graph drawn as shaded 3D beads.
// Branch nodes start collapsed where they'd be clutter; click one to open it
// and fly in, click empty space to collapse everything and zoom back out.
//
// One graph instance lives in a holder element that is moved between the
// Overview card and the full Map view, so both show the same live layout.

import { $, S, bus, esc } from "./core.js";

export const GROUP_COLORS = {
  core: "#ff4d9d", ai: "#38bdf8", mcp: "#2dd4bf", mcp_err: "#f43f5e",
  brain: "#a78bfa", note: "#818cf8", skill: "#fbbf24", runtime: "#22d3ee",
  tool: "#fb923c", hub: "#fcd34d", project: "#f472b6", absent: "#4b6472",
  game: "#22c55e", emulator: "#c084fc", gamecenter: "#a3e635", emucenter: "#fbbf24",
  moviecenter: "#ff3d5c",
};
const LEGEND = [
  ["core", "JARVIS"], ["ai", "AI connectors"], ["mcp", "MCP servers"], ["brain", "Obsidian"],
  ["skill", "skills / models"], ["hub", "hubs"], ["project", "projects"], ["runtime", "runtimes"],
  ["tool", "dev tools"], ["absent", "not installed / offline"],
];

let MAP = null, holder = null, mode = "mini";
let rawNodes = [], rawLinks = [], childMap = {}, parentMap = {};
let initialCollapsed = new Set(), collapsed = new Set();
let hintTimer = null, sig = "", touched = false;

// The layout keeps spreading for a couple of seconds after data arrives, so a
// single early fit leaves the graph off-centre. Re-fit as it settles — unless
// you've already started panning or zooming it yourself.
let fitTimer = null;
function fitSoon() {
  touched = false;
  clearInterval(fitTimer);
  let n = 0;
  fitTimer = setInterval(() => {
    if (!MAP || touched || ++n > 12) return clearInterval(fitTimer);
    MAP.zoomToFit(450, mode === "full" ? 80 : 40);
  }, 700);
}

const rgbOf = (h) => { h = h.replace("#", ""); return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16)); };
const lighten = (h, a) => { const [r, g, b] = rgbOf(h); const f = (x) => Math.round(x + (255 - x) * a); return `rgb(${f(r)},${f(g)},${f(b)})`; };
const darken = (h, a) => { const [r, g, b] = rgbOf(h); const f = (x) => Math.round(x * (1 - a)); return `rgb(${f(r)},${f(g)},${f(b)})`; };
const hasKids = (id) => (childMap[id] || []).length > 0;

// Only show nodes reachable from the core through non-collapsed branches.
function visibleData() {
  const shown = new Set(["jarvis"]);
  const q = ["jarvis"];
  while (q.length) {
    const id = q.shift();
    if (collapsed.has(id)) continue;
    for (const c of childMap[id] || []) if (!shown.has(c)) { shown.add(c); q.push(c); }
  }
  return {
    nodes: rawNodes.filter((n) => shown.has(n.id)),
    links: rawLinks.filter((l) => shown.has(l.s) && shown.has(l.t)).map((l) => ({ source: l.s, target: l.t })),
  };
}

// Lazily fetch shortcut icons, a few at a time (each is a PowerShell extraction server-side).
const iconQueue = [];
let iconActive = 0;
function pumpIcons() {
  while (iconActive < 6 && iconQueue.length) {
    const n = iconQueue.shift();
    iconActive++;
    const img = new Image();
    const done = () => { iconActive--; pumpIcons(); };
    img.onload = () => { n._icon = img; done(); };
    img.onerror = done;
    img.src = "/api/icon?path=" + encodeURIComponent(n.drill.path);
  }
}
function ensureIcons(nodes) {
  for (const n of nodes) {
    if ((n.group === "game" || n.group === "emulator") && !n._iconReq && n.drill && n.drill.path) {
      n._iconReq = true;
      iconQueue.push(n);
    }
  }
  pumpIcons();
}

function render() {
  const vis = visibleData();
  MAP.graphData(vis);
  ensureIcons(vis.nodes);
}

// A node is a shaded bead (radial gradient + glow) with a label; apps that are
// running right now get a live green ring.
function drawNode(node, ctx, scale) {
  const base = GROUP_COLORS[node.group] || "#9fb8c8";
  const r = node.r || 6;
  const kids = hasKids(node.id);
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
  if (kids && collapsed.has(node.id)) {           // collapsed branch → ring: "click to open"
    ctx.lineWidth = 1.6 / scale; ctx.strokeStyle = lighten(base, 0.4);
    ctx.beginPath(); ctx.arc(node.x, node.y, r + 3 / scale, 0, 6.2832); ctx.stroke();
  }
  if (node.live) {                                 // running right now
    const pulse = 0.5 + 0.5 * Math.sin(performance.now() / 420);
    ctx.lineWidth = 1.8 / scale; ctx.strokeStyle = `rgba(61,220,151,${0.5 + pulse * 0.5})`;
    ctx.beginPath(); ctx.arc(node.x, node.y, r + (4 + pulse * 2) / scale, 0, 6.2832); ctx.stroke();
  }
  const always = ["core", "hub", "ai", "brain", "gamecenter", "emucenter", "moviecenter"].includes(node.group) || kids;
  if (always || node === hovered || scale > 2.1 || node._found) {
    const fs = Math.max(3.4, (node.group === "core" ? 14 : 11.5) / scale);
    ctx.font = `${node.group === "core" ? "600 " : ""}${fs}px "Segoe UI Variable Text","Segoe UI",sans-serif`;
    ctx.fillStyle = node._found ? "#ffffff" : "#dce9f5";
    ctx.textAlign = "center"; ctx.textBaseline = "bottom";
    ctx.fillText(node.label, node.x, node.y - r - 2.5 / scale);
  }
}
let hovered = null;
function nodeHit(node, color, ctx) {
  const r = (node.r || 6) + 2;                     // a touch larger than drawn, for easier clicking
  ctx.fillStyle = color; ctx.beginPath(); ctx.arc(node.x, node.y, r, 0, 6.2832); ctx.fill();
}

function size() {
  if (!MAP || !holder || !holder.parentElement) return;
  const host = holder.parentElement;
  const w = host.clientWidth, h = host.clientHeight;
  if (w > 0 && h > 0) MAP.width(w).height(h);
}

function showHint(text) {
  let el = holder.querySelector(".map-zoomhint");
  if (!el) { el = document.createElement("div"); el.className = "map-zoomhint"; holder.appendChild(el); }
  el.textContent = text;
  el.classList.add("show");
  clearTimeout(hintTimer);
  hintTimer = setTimeout(() => el.classList.remove("show"), 1300);
}

export function initMap() {
  if (MAP || typeof ForceGraph === "undefined") return false;
  holder = document.createElement("div");
  holder.id = "graphmap";
  holder.style.cssText = "position:absolute;inset:0";
  $("mapHostMini").appendChild(holder);

  MAP = ForceGraph()(holder)
    .backgroundColor("rgba(0,0,0,0)")
    .linkColor(() => "rgba(140,170,195,0.26)")
    .linkWidth(0.9)
    .nodeLabel((n) => esc(n.label) + (n.live ? " · running" : ""))
    .nodeCanvasObject(drawNode)
    .nodePointerAreaPaint(nodeHit)
    .onNodeHover((n) => { hovered = n || null; holder.style.cursor = n ? "pointer" : "grab"; })
    .onNodeClick((node) => {
      if (hasKids(node.id)) {
        const opening = collapsed.has(node.id);
        if (opening) collapsed.delete(node.id); else collapsed.add(node.id);
        render();
        setTimeout(() => {
          if (opening) { MAP.centerAt(node.x, node.y, 700); MAP.zoom(2.6, 700); }
          else {
            const par = MAP.graphData().nodes.find((n) => n.id === parentMap[node.id]);
            if (par && par.id !== "jarvis") { MAP.centerAt(par.x, par.y, 700); MAP.zoom(2.0, 700); }
            else MAP.zoomToFit(700, 50);
          }
        }, 60);
      } else if (node.drill) {
        bus.emit("drill", node.drill);             // leaf → open details / launch
      }
    })
    .onBackgroundClick(() => {
      collapsed = new Set(initialCollapsed);       // click away → collapse everything
      render();
      MAP.zoomToFit(700, 50);
    });
  MAP.d3Force("charge").strength(-70);
  if (MAP.d3Force("link")) {
    MAP.d3Force("link").distance((l) => {
      const s = typeof l.source === "object" ? l.source.id : l.source;
      const t = typeof l.target === "object" ? l.target.id : l.target;
      return s === "jarvis" || t === "jarvis" ? 95 : 34;   // push hubs out from the core
    });
  }
  MAP.d3VelocityDecay(0.72);
  MAP.cooldownTicks(Infinity);
  MAP.cooldownTime(Infinity);      // never stop ticking, so it keeps floating
  // Gentle perpetual float: each node drifts but springs back toward a slowly
  // following "home", so the map breathes without flying apart or freezing.
  MAP.d3Force("float", () => {
    for (const n of MAP.graphData().nodes) {
      if (n.hx === undefined) { n.hx = n.x; n.hy = n.y; }
      n.hx += (n.x - n.hx) * 0.05;
      n.hy += (n.y - n.hy) * 0.05;
      n.vx += (Math.random() - 0.5) * 0.6 + (n.hx - n.x) * 0.05;
      n.vy += (Math.random() - 0.5) * 0.6 + (n.hy - n.y) * 0.05;
    }
  });

  // The Overview map sits in a scrolling page: a plain wheel must scroll the
  // page, not zoom the graph to a dot. Zoom needs Ctrl there; the full Map
  // view (which doesn't scroll) zooms freely.
  holder.addEventListener("wheel", (e) => {
    if (mode === "mini" && !e.ctrlKey) {
      e.stopPropagation();
      showHint("Hold Ctrl and scroll to zoom");
    } else touched = true;
  }, { capture: true, passive: true });
  holder.addEventListener("pointerdown", () => { touched = true; }, { capture: true });

  new ResizeObserver(size).observe($("mapHostMini"));
  new ResizeObserver(size).observe($("mapHostFull"));
  $("mapLegend").innerHTML = LEGEND.map(([g, label]) =>
    `<span><i style="background:${GROUP_COLORS[g]}"></i>${label}</span>`).join("") +
    `<span><i style="background:transparent;box-shadow:0 0 0 2px #3ddc97"></i>running</span>`;
  size();
  return true;
}

/** Move the live graph into the Overview card ("mini") or the Map view ("full"). */
export function mountMap(where) {
  if (!MAP) return;
  mode = where;
  const host = where === "full" ? $("mapHostFull") : $("mapHostMini");
  const moved = holder.parentElement !== host;
  if (moved) host.appendChild(holder);
  size();
  if (moved) fitSoon();
}

/** Pause the simulation while no map is on screen (it otherwise redraws at 60 fps forever). */
export function setMapActive(on) {
  if (!MAP) return;
  if (on) MAP.resumeAnimation(); else MAP.pauseAnimation();
}

export async function loadMap(force = false) {
  if (!MAP) return;
  let data;
  try { data = await (await fetch("/api/graph")).json(); } catch { return; }
  // Rebuild the layout only when the graph's shape changed; a refresh that
  // merely flips a "running" ring keeps every bead where it is.
  const nextSig = data.nodes.map((n) => n.id + n.group).join("|") + "#" + data.links.length;
  if (!force && nextSig === sig) {
    const live = new Map(data.nodes.map((n) => [n.id, n]));
    for (const n of rawNodes) { const fresh = live.get(n.id); if (fresh) { n.live = fresh.live; n.label = fresh.label; } }
    return;
  }
  sig = nextSig;
  rawNodes = data.nodes;
  rawLinks = data.links.map((l) => ({ s: l.source, t: l.target }));
  childMap = {}; parentMap = {};
  for (const l of rawLinks) { (childMap[l.s] = childMap[l.s] || []).push(l.t); parentMap[l.t] = l.s; }
  // One level below the core is open by default; only the big shortcut
  // libraries start collapsed, so you drill into those to avoid clutter.
  initialCollapsed = new Set(rawNodes.filter((n) => String(n.id).startsWith("lib:") && hasKids(n.id)).map((n) => n.id));
  collapsed = new Set(initialCollapsed);
  render();
  fitSoon();
}

export function mapZoom(dir) { if (MAP) MAP.zoom(MAP.zoom() * (dir > 0 ? 1.35 : 0.74), 250); }
export function mapFit() { if (MAP) MAP.zoomToFit(500, mode === "full" ? 80 : 40); }

/** Find a node by name: open the branches above it, fly to it, light its label. */
export function mapFind(query) {
  if (!MAP) return;
  for (const n of rawNodes) n._found = false;
  const q = query.trim().toLowerCase();
  if (!q) return;
  const hit = rawNodes.find((n) => n.label.toLowerCase().startsWith(q)) ||
              rawNodes.find((n) => n.label.toLowerCase().includes(q));
  if (!hit) return;
  for (let id = parentMap[hit.id]; id; id = parentMap[id]) collapsed.delete(id);
  render();
  hit._found = true;
  setTimeout(() => {
    const live = MAP.graphData().nodes.find((n) => n.id === hit.id);
    if (live && live.x !== undefined) { MAP.centerAt(live.x, live.y, 600); MAP.zoom(3, 600); }
  }, 350);
}

bus.on("state", () => { if (S.view === "overview" || S.view === "map") loadMap(); });
