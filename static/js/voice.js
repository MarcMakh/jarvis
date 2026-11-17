// JARVIS voice: speech out (sentence-queued TTS) and speech in (mic -> Whisper).
//
// Speech out used to wait for the complete answer, then synthesise it in one
// request. Now text is cut into sentences as it streams in and each one is
// synthesised while the previous is playing — JARVIS starts talking after the
// first sentence instead of after the last.

import { $, api, bus, prefs, savePrefs } from "./core.js";
import { speakable } from "./md.js";

const player = $("player");
const SPEAK_CAP = 1400;            // characters spoken per answer (voice credits)

let actx = null, analyser = null, meter = null, micAnalyser = null;

function ensureAnalyser() {
  // Created only after audio has really started (so the context isn't born
  // suspended behind the autoplay policy, which would silence the element).
  if (actx || !window.AudioContext) return;
  try {
    actx = new AudioContext();
    const src = actx.createMediaElementSource(player);
    analyser = actx.createAnalyser();
    analyser.fftSize = 256;
    analyser.smoothingTimeConstant = 0.72;
    src.connect(analyser);
    analyser.connect(actx.destination);
    meter = new Uint8Array(analyser.frequencyBinCount);
  } catch { actx = null; analyser = null; }
}

function rms(node, buf) {
  node.getByteTimeDomainData(buf);
  let sum = 0;
  for (let i = 0; i < buf.length; i++) { const v = (buf[i] - 128) / 128; sum += v * v; }
  return Math.sqrt(sum / buf.length);
}

export const voice = {
  on: prefs.voice !== false,
  speaking: false,
  blocked: false,            // autoplay refused: waiting for a click
  _queue: [],                // [{text, blob: Promise<Blob|null>}]
  _playing: false,
  _raw: "", _idx: 0, _inFence: false, _pending: "", _first: true, _spoken: 0,

  /** 0..1 loudness of whatever is being spoken right now (drives the orb). */
  level() {
    if (!this.speaking) return 0;
    if (analyser) return Math.min(1, rms(analyser, meter) * 3.2);
    return 0.35 + 0.25 * Math.sin(performance.now() / 90);   // no analyser: fake a pulse
  },
  /** Frequency bins (0..255) for the orb's ring, or null. */
  spectrum() {
    if (!this.speaking || !analyser) return null;
    analyser.getByteFrequencyData(meter);
    return meter;
  },

  setOn(on) {
    this.on = !!on;
    prefs.voice = this.on;
    savePrefs();
    if (!this.on) this.stop();
    bus.emit("voice", this);
  },

  /** Speak a complete piece of text (replaces anything in progress). */
  say(text) {
    this.stop();
    this.begin();
    this.feed(text);
    this.end();
  },

  // ── streaming interface: begin() → feed(delta)… → end() ───────────────
  begin() {
    this._raw = ""; this._idx = 0; this._inFence = false; this._pending = "";
    this._first = true; this._spoken = 0;
  },
  feed(delta) {
    if (!this.on || !delta) return;
    this._raw += delta;
    const re = /[.!?…]+["')\]*_]*(?=\s)|\n+/g;
    re.lastIndex = this._idx;
    let m;
    while ((m = re.exec(this._raw))) {
      const end = m.index + m[0].length;
      this._segment(this._raw.slice(this._idx, end));
      this._idx = end;
    }
  },
  end() {
    if (!this.on) return;
    if (this._idx < this._raw.length) this._segment(this._raw.slice(this._idx));
    this._idx = this._raw.length;
    this._flush();
  },
  _segment(seg) {
    // skip anything inside a ``` fence — nobody wants code read aloud
    const parts = seg.split("```");
    let out = "";
    parts.forEach((part, i) => {
      if (!this._inFence) out += part;
      if (i < parts.length - 1) this._inFence = !this._inFence;
    });
    const clean = speakable(out);
    if (!clean) return;
    this._pending += (this._pending ? " " : "") + clean;
    // The first sentence goes out at once (fast start); later ones are grouped
    // into natural-length chunks so the voice doesn't sound choppy.
    if (this._first || this._pending.length >= 90) this._flush();
  },
  _flush() {
    const text = this._pending.trim();
    this._pending = "";
    if (!text || this._spoken >= SPEAK_CAP) return;
    this._first = false;
    const chunk = text.slice(0, SPEAK_CAP - this._spoken);
    this._spoken += chunk.length;
    this._enqueue(chunk);
  },

  _enqueue(text) {
    const item = { text, blob: null };
    this._queue.push(item);
    this._prefetch();
    this._pump();
  },
  _prefetch() {
    // synthesise up to two sentences ahead of the one playing
    for (const item of this._queue.slice(0, 3)) {
      if (item.blob) continue;
      item.blob = fetch("/api/speak", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: item.text }),
      }).then((r) => (r.status === 200 ? r.blob() : null)).catch(() => null);
    }
  },
  async _pump() {
    if (this._playing || !this._queue.length) return;
    this._playing = true;
    const item = this._queue[0];
    const blob = await item.blob;
    if (this._queue[0] !== item) { this._playing = false; return this._pump(); }   // stopped meanwhile
    if (!blob || !this.on) { this._queue.shift(); this._playing = false; return this._after(); }
    player.src = URL.createObjectURL(blob);
    try {
      await player.play();
      ensureAnalyser();
      if (actx && actx.state === "suspended") actx.resume().catch(() => {});
      this.blocked = false;
      if (!this.speaking) { this.speaking = true; bus.emit("voice", this); }
    } catch {
      // Autoplay blocked (a normal browser tab before any click): keep the
      // sentence queued and ask for a gesture.
      this._playing = false;
      if (!this.blocked) { this.blocked = true; bus.emit("voice", this); }
    }
  },
  _after() {
    this._prefetch();
    if (this._queue.length) return this._pump();
    if (this.speaking) { this.speaking = false; bus.emit("voice", this); }
  },
  /** Call from a user gesture to release speech the browser refused to autoplay. */
  unlock() {
    if (!this.blocked) return;
    this.blocked = false;
    bus.emit("voice", this);
    this._pump();
  },
  stop() {
    this._queue = [];
    this._pending = "";
    this._playing = false;
    try { player.pause(); } catch { /* nothing playing */ }
    if (this.speaking || this.blocked) { this.speaking = false; this.blocked = false; bus.emit("voice", this); }
  },
};

player.addEventListener("ended", () => {
  if (player.src.startsWith("blob:")) URL.revokeObjectURL(player.src);
  voice._queue.shift();
  voice._playing = false;
  voice._after();
});
player.addEventListener("error", () => {
  voice._queue.shift();
  voice._playing = false;
  voice._after();
});

// ── microphone ────────────────────────────────────────────────────────────
// The device choice lives in localStorage per browser profile (deviceIds are
// profile-scoped, so the app window and a normal tab each keep their own).
export const mic = {
  device: prefs.mic || "off",
  recording: false,
  busy: false,
  _stream: null, _rec: null, _chunks: [], _ctx: null, _buf: null, _watch: null,

  level() {
    return this.recording && micAnalyser ? Math.min(1, rms(micAnalyser, this._buf) * 4) : 0;
  },

  async devices() {
    try {
      return (await navigator.mediaDevices.enumerateDevices()).filter((d) => d.kind === "audioinput");
    } catch { return []; }
  },

  /** Pick a device ("off" disables). Returns {ok, error}. Asking for the
   *  stream once warms the permission so real device labels become visible. */
  async setDevice(id) {
    if (this.recording) this.cancel();
    if (id === "off") { this.device = "off"; prefs.mic = "off"; savePrefs(); bus.emit("mic", this); return { ok: true }; }
    try {
      const s = await navigator.mediaDevices.getUserMedia({ audio: id && id !== "default" ? { deviceId: { exact: id } } : true });
      s.getTracks().forEach((t) => t.stop());
    } catch {
      this.device = "off"; prefs.mic = "off"; savePrefs(); bus.emit("mic", this);
      return { ok: false, error: "Microphone access was denied." };
    }
    this.device = id || "default"; prefs.mic = this.device; savePrefs(); bus.emit("mic", this);
    return { ok: true };
  },

  async toggle() {
    if (this.busy || this.device === "off") return;
    if (this.recording) return this._rec.stop();
    voice.stop();                                   // don't record JARVIS talking over you
    try {
      this._stream = await navigator.mediaDevices.getUserMedia({
        audio: this.device !== "default" ? { deviceId: { exact: this.device } } : true,
      });
    } catch {
      bus.emit("mic:error", "I could not open that microphone.");
      return;
    }
    this._chunks = [];
    const mime = MediaRecorder.isTypeSupported("audio/webm;codecs=opus") ? "audio/webm;codecs=opus" : "";
    this._rec = new MediaRecorder(this._stream, mime ? { mimeType: mime } : undefined);
    this._rec.ondataavailable = (e) => { if (e.data.size) this._chunks.push(e.data); };
    this._rec.onstop = () => this._done();
    this._rec.start();
    this.recording = true;
    this._watchSilence();
    bus.emit("mic", this);
  },

  /** Stop by itself once you've spoken and then gone quiet for ~1.4 s. */
  _watchSilence() {
    try {
      this._ctx = new AudioContext();
      const src = this._ctx.createMediaStreamSource(this._stream);
      micAnalyser = this._ctx.createAnalyser();
      micAnalyser.fftSize = 512;
      src.connect(micAnalyser);
      this._buf = new Uint8Array(micAnalyser.fftSize);
    } catch { micAnalyser = null; return; }
    const started = performance.now();
    let spoke = 0, quietSince = 0;
    this._watch = setInterval(() => {
      if (!this.recording) return;
      const now = performance.now();
      const loud = rms(micAnalyser, this._buf) > 0.035;
      if (loud) { spoke += 100; quietSince = 0; }
      else if (!quietSince) quietSince = now;
      const done = prefs.autoStop !== false && spoke >= 250 && quietSince && now - quietSince > 1400;
      if (done || now - started > 60000) this._rec.stop();
    }, 100);
  },

  _cleanup() {
    clearInterval(this._watch);
    if (this._stream) this._stream.getTracks().forEach((t) => t.stop());
    if (this._ctx) this._ctx.close().catch(() => {});
    this._stream = null; this._rec = null; this._ctx = null; micAnalyser = null;
    this.recording = false;
  },

  cancel() {
    if (this._rec && this._rec.state === "recording") { this._rec.onstop = null; this._rec.stop(); }
    this._chunks = [];
    this._cleanup();
    bus.emit("mic", this);
  },

  async _done() {
    const blob = new Blob(this._chunks, { type: "audio/webm" });
    this._chunks = [];
    this._cleanup();
    if (blob.size < 2000) { bus.emit("mic", this); return; }     // a stray click, nothing said
    this.busy = true;
    bus.emit("mic", this);
    try {
      const r = await fetch("/api/transcribe", {
        method: "POST", headers: { "Content-Type": "application/octet-stream" }, body: blob,
      });
      const res = await r.json();
      if (res.ok && res.text) bus.emit("mic:text", res.text);
      else bus.emit("mic:error", res.ok ? "I didn't catch anything." : `Transcription failed: ${res.error || "unknown error"}`);
    } catch {
      bus.emit("mic:error", "Transcription failed — is the server up?");
    }
    this.busy = false;
    bus.emit("mic", this);
  },
};

if (navigator.mediaDevices) {
  navigator.mediaDevices.addEventListener("devicechange", () => bus.emit("mic", mic));
}

/** Ask the server to reopen JARVIS as its app window (where speech needs no click). */
export async function openAppWindow() {
  voice.setOn(false);
  try { return (await api("/api/appwindow", {})).ok; } catch { return false; }
}
