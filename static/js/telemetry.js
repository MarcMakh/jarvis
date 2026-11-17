// Live vitals: CPU, memory, GPU, VRAM, storage, network — one sample a second
// from the server's event stream, each tile with a two-minute sparkline.

import { $, S, esc, level, num, rate, setHtml } from "./core.js";

const MAX_POINTS = 120;
const tiles = {};

const DEFS = [
  { id: "cpu", label: "CPU" },
  { id: "mem", label: "Memory" },
  { id: "gpu", label: "GPU" },
  { id: "vram", label: "VRAM" },
  { id: "disk", label: "Storage" },
  { id: "net", label: "Network" },
];

export function initVitals() {
  const host = $("vitals");
  host.innerHTML = DEFS.map((d) =>
    `<div class="vital" id="v-${d.id}">
       <div class="vl"><span>${d.label}</span><span data-r="tag"></span></div>
       <div class="vv" data-r="val">—</div>
       <div class="vs" data-r="sub">&nbsp;</div>
       ${d.id === "cpu" ? '<div class="cores" data-r="cores"></div>' : ""}
       ${d.id === "disk" ? '<div class="drives" data-r="drives"></div>' : '<canvas class="spark" data-r="spark"></canvas>'}
     </div>`).join("");
  for (const d of DEFS) {
    const el = $("v-" + d.id);
    const ref = {};
    el.querySelectorAll("[data-r]").forEach((n) => { ref[n.dataset.r] = n; });
    tiles[d.id] = { el, ...ref };
  }
}

function tone(el, lvl) {
  el.classList.toggle("warn", lvl === "warn");
  el.classList.toggle("crit", lvl === "crit");
}
function text(node, value) { if (node && node.textContent !== value) node.textContent = value; }
function html(node, value) { if (node && node.__h !== value) { node.__h = value; node.innerHTML = value; } }

function spark(canvas, data, max, rgbVar = "--tone-rgb") {
  if (!canvas || canvas.offsetParent === null) return;      // hidden view: skip the draw
  const dpr = Math.min(2, window.devicePixelRatio || 1);
  const w = canvas.clientWidth, h = canvas.clientHeight;
  if (!w || !h) return;
  if (canvas.width !== Math.round(w * dpr)) { canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr); }
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  const pts = data.slice(-90);
  if (pts.length < 2) return;
  const rgb = getComputedStyle(canvas.parentElement).getPropertyValue(rgbVar).trim() || "76,216,255";
  const top = max || Math.max(1, ...pts) * 1.15;
  const step = w / (90 - 1);
  const x0 = w - (pts.length - 1) * step;
  const y = (v) => h - 2 - (Math.min(v, top) / top) * (h - 6);
  ctx.beginPath();
  pts.forEach((v, i) => { const x = x0 + i * step; i ? ctx.lineTo(x, y(v)) : ctx.moveTo(x, y(v)); });
  ctx.strokeStyle = `rgba(${rgb}, 0.95)`;
  ctx.lineWidth = 1.5;
  ctx.lineJoin = "round";
  ctx.stroke();
  ctx.lineTo(w, h); ctx.lineTo(x0, h); ctx.closePath();
  const g = ctx.createLinearGradient(0, 0, 0, h);
  g.addColorStop(0, `rgba(${rgb}, 0.32)`);
  g.addColorStop(1, `rgba(${rgb}, 0)`);
  ctx.fillStyle = g;
  ctx.fill();
}

/** Seed the sparklines with the server's rolling history (on load). */
export function seedHistory(hist) {
  for (const k of Object.keys(S.hist)) S.hist[k] = (hist[k] || []).slice(-MAX_POINTS);
}

function push(key, v) {
  const a = S.hist[key];
  a.push(v);
  if (a.length > MAX_POINTS) a.shift();
}

export function updateVitals(t, record = true) {
  if (!t || !tiles.cpu) return;
  S.tel = t;
  const gpu = t.gpu;
  if (record) {
    push("cpu", t.cpu); push("mem", t.mem.pct);
    push("gpu", gpu ? gpu.util : 0); push("vram", gpu ? gpu.vram_pct : 0);
    push("net_down", t.net.down); push("net_up", t.net.up);
  }

  // CPU
  let T = tiles.cpu;
  html(T.val, `${Math.round(t.cpu)}<small>%</small>`);
  text(T.sub, `${t.cpu_count} threads · up ${t.uptime_h >= 48 ? Math.round(t.uptime_h / 24) + " d" : num(t.uptime_h, 0) + " h"}`);
  tone(T.el, level(t.cpu, 85, 96));
  if (T.cores.childElementCount !== t.cores.length) T.cores.innerHTML = t.cores.map(() => "<i></i>").join("");
  t.cores.forEach((c, i) => { T.cores.children[i].style.height = Math.max(8, c) + "%"; });
  spark(T.spark, S.hist.cpu, 100);

  // memory
  T = tiles.mem;
  html(T.val, `${Math.round(t.mem.pct)}<small>%</small>`);
  text(T.sub, `${num(t.mem.used_gb, 1)} / ${num(t.mem.total_gb, 0)} GB`);
  tone(T.el, level(t.mem.pct, 85, 93));
  spark(T.spark, S.hist.mem, 100);

  // GPU + VRAM
  T = tiles.gpu;
  const V = tiles.vram;
  if (gpu) {
    html(T.val, `${gpu.util}<small>%</small>`);
    text(T.tag, gpu.name.replace(/^GeForce /, ""));
    text(T.sub, [gpu.temp != null ? gpu.temp + "°C" : "", gpu.power_w != null ? gpu.power_w + " W" : "",
      gpu.fan != null ? "fan " + gpu.fan + "%" : ""].filter(Boolean).join(" · "));
    tone(T.el, gpu.temp >= 87 ? "crit" : gpu.temp >= 80 ? "warn" : "");
    spark(T.spark, S.hist.gpu, 100);
    html(V.val, `${Math.round(gpu.vram_pct)}<small>%</small>`);
    text(V.sub, `${num(gpu.vram_used_gb, 1)} / ${num(gpu.vram_total_gb, 1)} GB`);
    tone(V.el, level(gpu.vram_pct, 88, 96));
    spark(V.spark, S.hist.vram, 100);
  } else {
    html(T.val, "—"); text(T.sub, "no NVIDIA GPU");
    html(V.val, "—"); text(V.sub, " ");
  }

  // storage: system drive headline + the fullest other drives
  T = tiles.disk;
  const disks = t.disks || [];
  const sys = disks.find((d) => d.system) || disks[0];
  if (sys) {
    html(T.val, `${Math.round(sys.pct)}<small>%</small>`);
    text(T.tag, `${sys.mount} · ${Math.round(sys.free_gb)} GB free`);
    tone(T.el, level(sys.pct, 90, 95));
    const rest = disks.filter((d) => d !== sys).sort((a, b) => b.pct - a.pct).slice(0, 3);
    html(T.drives, rest.map((d) =>
      `<div class="drive ${level(d.pct, 95, 98)}" title="${esc(d.mount)} — ${num(d.free_gb, 1)} GB free of ${Math.round(d.total_gb)} GB">
         <span>${esc(d.mount)}</span><span class="bar"><i style="width:${Math.min(100, d.pct)}%"></i></span><span>${Math.round(d.pct)}%</span></div>`).join(""));
    text(T.sub, "");
  }

  // network
  T = tiles.net;
  html(T.val, `<span style="font-size:19px">↓ ${esc(rate(t.net.down))}</span>`);
  text(T.sub, `↑ ${rate(t.net.up)}`);
  spark(T.spark, S.hist.net_down, 0);
}

export function renderProcs(p) {
  S.procs = p || S.procs;
  const { cpu = [], mem = [] } = S.procs;
  const maxMem = Math.max(0.1, ...mem.map((r) => r.mem_gb));
  const maxCpu = Math.max(5, ...cpu.map((r) => r.cpu));
  const col = (title, rows, val, frac) =>
    `<div class="proc-col"><div class="sect">${title}</div>` +
    (rows.length ? rows.map((r) =>
      `<div class="proc" title="${esc(r.name)}${r.n > 1 ? ` — ${r.n} processes` : ""}">
         <span class="pn">${esc(r.name)}${r.n > 1 ? ` <span class="muted">×${r.n}</span>` : ""}</span>
         <span class="pv">${val(r)}</span>
         <span class="pb"><i style="width:${Math.round(frac(r) * 100)}%"></i></span></div>`).join("")
      : `<div class="muted" style="font-size:12px">idle</div>`) + `</div>`;
  setHtml($("procs"),
    col("CPU", cpu, (r) => num(r.cpu, 1) + "%", (r) => r.cpu / maxCpu) +
    col("Memory", mem, (r) => num(r.mem_gb, r.mem_gb >= 10 ? 0 : 1) + " GB", (r) => r.mem_gb / maxMem));
}
