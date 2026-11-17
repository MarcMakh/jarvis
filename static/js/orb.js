// The reactor: an audio-reactive orb. It breathes when idle, spins up while
// JARVIS is thinking, and its ring follows the actual sound of his voice (or of
// yours, while the microphone is open).

const TAU = Math.PI * 2;

function accent() {
  const cs = getComputedStyle(document.documentElement);
  return {
    rgb: (cs.getPropertyValue("--accent-rgb") || "76, 216, 255").trim(),
    hot: (cs.getPropertyValue("--accent-2") || "#9be9ff").trim(),
  };
}

export class Orb {
  /** @param {HTMLCanvasElement} canvas
   *  @param {{level: () => number, spectrum?: () => (Uint8Array|null)}} src */
  constructor(canvas, src) {
    this.c = canvas;
    this.ctx = canvas.getContext("2d");
    this.src = src;
    this.mode = "idle";              // idle | thinking | speaking | listening
    this.t = 0;
    this.lv = 0;                     // smoothed level
    this.spin = 0;
    this.bars = new Float32Array(56);
    this.col = accent();
    this.visible = true;
    this._last = 0;
    this._frame = this._frame.bind(this);
    new IntersectionObserver((es) => { this.visible = es[0].isIntersecting; }).observe(canvas);
    new MutationObserver(() => { this.col = accent(); })
      .observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    requestAnimationFrame(this._frame);
  }

  setMode(mode) { this.mode = mode; }

  _frame(now) {
    requestAnimationFrame(this._frame);
    if (!this.visible || document.hidden) return;
    const active = this.mode !== "idle";
    // idle orbs only need ~30 fps
    if (!active && now - this._last < 32) return;
    const dt = Math.min(0.05, (now - (this._last || now)) / 1000);
    this._last = now;
    this.t += dt;
    this._draw(dt);
  }

  _draw(dt) {
    const { c, ctx } = this;
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    const size = c.clientWidth || 110;
    if (c.width !== Math.round(size * dpr)) { c.width = c.height = Math.round(size * dpr); }
    const R = c.width / 2;
    ctx.clearRect(0, 0, c.width, c.height);
    ctx.save();
    ctx.translate(R, R);

    const rgb = this.col.rgb;
    const target = this.mode === "speaking" || this.mode === "listening" ? this.src.level()
      : this.mode === "thinking" ? 0.22 + 0.12 * Math.sin(this.t * 6) : 0.06 + 0.05 * Math.sin(this.t * 1.6);
    this.lv += (target - this.lv) * (target > this.lv ? 0.45 : 0.12);
    const lv = this.lv;
    this.spin += dt * (this.mode === "thinking" ? 2.6 : this.mode === "idle" ? 0.25 : 0.7);

    // outer glow
    const glow = ctx.createRadialGradient(0, 0, R * 0.2, 0, 0, R);
    glow.addColorStop(0, `rgba(${rgb}, ${0.16 + lv * 0.3})`);
    glow.addColorStop(1, `rgba(${rgb}, 0)`);
    ctx.fillStyle = glow;
    ctx.beginPath(); ctx.arc(0, 0, R, 0, TAU); ctx.fill();

    // audio ring: bars around the core
    const spec = this.mode === "speaking" && this.src.spectrum ? this.src.spectrum() : null;
    const n = this.bars.length;
    const inner = R * 0.6;
    ctx.lineCap = "round";
    ctx.lineWidth = Math.max(1.5, R * 0.03);
    for (let i = 0; i < n; i++) {
      let v;
      if (spec) {
        // mirror the spectrum so the ring is symmetric
        const k = i < n / 2 ? i : n - 1 - i;
        v = spec[2 + Math.floor((k / (n / 2)) * 40)] / 255;
      } else if (this.mode === "listening" || this.mode === "speaking") {
        v = lv * (0.55 + 0.45 * Math.sin(i * 1.7 + this.t * 9));
      } else if (this.mode === "thinking") {
        v = 0.25 + 0.25 * Math.sin(i * 0.45 - this.t * 7);
      } else {
        v = 0.07 + 0.05 * Math.sin(i * 0.6 + this.t * 1.4);
      }
      this.bars[i] += (v - this.bars[i]) * 0.35;
      const len = R * (0.04 + this.bars[i] * 0.3);
      const a = (i / n) * TAU + this.spin * 0.15;
      ctx.strokeStyle = `rgba(${rgb}, ${0.35 + this.bars[i] * 0.65})`;
      ctx.beginPath();
      ctx.moveTo(Math.cos(a) * inner, Math.sin(a) * inner);
      ctx.lineTo(Math.cos(a) * (inner + len), Math.sin(a) * (inner + len));
      ctx.stroke();
    }

    // segmented ring (the "reactor")
    ctx.lineWidth = Math.max(2, R * 0.05);
    const segs = 10;
    for (let i = 0; i < segs; i++) {
      const a0 = this.spin + (i / segs) * TAU;
      ctx.strokeStyle = `rgba(${rgb}, ${i % 2 ? 0.28 : 0.75})`;
      ctx.beginPath(); ctx.arc(0, 0, R * 0.48, a0, a0 + (TAU / segs) * 0.72); ctx.stroke();
    }
    ctx.lineWidth = Math.max(1, R * 0.014);
    ctx.strokeStyle = `rgba(${rgb}, 0.4)`;
    ctx.beginPath(); ctx.arc(0, 0, R * 0.38, 0, TAU); ctx.stroke();

    // core
    const coreR = R * (0.22 + lv * 0.1);
    const core = ctx.createRadialGradient(0, 0, 0, 0, 0, coreR * 1.5);
    core.addColorStop(0, "#ffffff");
    core.addColorStop(0.3, this.col.hot);
    core.addColorStop(0.62, `rgba(${rgb}, 0.9)`);
    core.addColorStop(1, `rgba(${rgb}, 0)`);
    ctx.fillStyle = core;
    ctx.shadowColor = `rgba(${rgb}, 0.9)`;
    ctx.shadowBlur = R * (0.25 + lv * 0.4);
    ctx.beginPath(); ctx.arc(0, 0, coreR * 1.5, 0, TAU); ctx.fill();
    ctx.restore();
  }
}
