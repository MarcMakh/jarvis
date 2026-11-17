"""JARVIS ears. Transcribes mic audio the browser records (speech-to-text).

Local-only: faster-whisper (CTranslate2 Whisper) runs on CPU in-process, so
audio never leaves this machine — no key, no quota. The model downloads once
into data/whisper/ on first use (~145 MB for base.en) and stays cached.

Config [ears]: model ("base.en" = fast English; "small.en" = more accurate;
multilingual "base"/"small" + language = "" for auto-detect e.g. French),
language, compute_type ("int8" is the fast CPU path).
"""

from __future__ import annotations

import io
import threading
import time
from typing import Any

from .util import ROOT

# The Whisper model is a ~145 MB download: keep it in the real data folder even
# when a dev/test instance redirects its data dir, so it is never fetched twice.
_MODELS_DIR = ROOT / "data" / "whisper"

# One model held per process (loads in ~1-2 s from disk, so never per request).
_lock = threading.Lock()
_model: Any = None
_model_key: tuple[str, str] | None = None


def _get_model(name: str, compute_type: str) -> Any:
    global _model, _model_key
    with _lock:
        if _model is None or _model_key != (name, compute_type):
            from faster_whisper import WhisperModel

            _MODELS_DIR.mkdir(parents=True, exist_ok=True)
            _model = WhisperModel(
                name, device="cpu", compute_type=compute_type, download_root=str(_MODELS_DIR)
            )
            _model_key = (name, compute_type)
    return _model


class Ears:
    def __init__(self, cfg: dict[str, Any]):
        e = cfg.get("ears", {})
        self.enabled: bool = e.get("enabled", True)
        self.model_name: str = e.get("model", "base.en")
        self.language: str = e.get("language", "en")
        self.compute_type: str = e.get("compute_type", "int8")

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "model": self.model_name,
            "language": self.language,
            "loaded": _model is not None and _model_key == (self.model_name, self.compute_type),
        }

    def transcribe(self, audio: bytes) -> dict[str, Any]:
        """Audio bytes (webm/opus from MediaRecorder, or anything PyAV decodes) -> text."""
        if not self.enabled:
            return {"ok": False, "error": "ears disabled in config.toml"}
        if not audio:
            return {"ok": False, "error": "empty audio"}
        t0 = time.time()
        try:
            model = _get_model(self.model_name, self.compute_type)
            segments, info = model.transcribe(
                io.BytesIO(audio),
                language=self.language or None,
                beam_size=5,
                vad_filter=True,  # trims leading/trailing silence around the utterance
            )
            text = " ".join(s.text.strip() for s in segments).strip()
            return {
                "ok": True,
                "text": text,
                "language": info.language,
                "seconds": round(time.time() - t0, 2),
            }
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
