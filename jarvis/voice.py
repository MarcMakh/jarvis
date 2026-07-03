"""JARVIS voice. Synthesizes MP3 bytes the browser plays (no server audio device).

Two backends, chosen by config [voice].provider:
  - "elevenlabs": high-quality / character voices (needs ELEVENLABS_API_KEY + a
    voice_id).
  - "edge": free Microsoft neural TTS (edge-tts), no key. JARVIS's classic voice.
  - "auto" (default): ElevenLabs when a key + voice_id are set, else edge.
ElevenLabs failures fall back to edge, so JARVIS always has a voice.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

_CACHE = Path(__file__).resolve().parent.parent / "data" / "voice_cache"


class Voice:
    def __init__(self, cfg: dict[str, Any]):
        v = cfg.get("voice", {})
        self.enabled: bool = v.get("enabled", True)
        self.provider: str = v.get("provider", "auto")  # auto | edge | elevenlabs
        # edge-tts
        self.voice: str = v.get("voice", "en-GB-RyanNeural")
        self.rate: str = v.get("rate", "-4%")
        self.pitch: str = v.get("pitch", "-2Hz")
        self.greet_on_start: bool = v.get("greet_on_start", True)
        # elevenlabs
        self.el_voice_id: str = v.get("elevenlabs_voice_id", "")
        self.el_model: str = v.get("elevenlabs_model", "eleven_turbo_v2_5")
        self.el_stability: float = float(v.get("elevenlabs_stability", 0.4))
        self.el_similarity: float = float(v.get("elevenlabs_similarity", 0.75))
        _CACHE.mkdir(parents=True, exist_ok=True)

    def resolved_provider(self) -> str:
        have_el = bool(os.environ.get("ELEVENLABS_API_KEY") and self.el_voice_id)
        if self.provider == "elevenlabs":
            return "elevenlabs" if have_el else "edge"
        if self.provider == "edge":
            return "edge"
        return "elevenlabs" if have_el else "edge"  # auto

    def status(self) -> dict[str, Any]:
        prov = self.resolved_provider()
        return {
            "enabled": self.enabled,
            "provider": prov,
            "voice": self.el_voice_id if prov == "elevenlabs" else self.voice,
            "elevenlabs_ready": bool(os.environ.get("ELEVENLABS_API_KEY")),
            "elevenlabs_voice_set": bool(self.el_voice_id),
        }

    def _cache_path(self, text: str, prov: str) -> Path:
        if prov == "elevenlabs":
            key = f"el|{self.el_voice_id}|{self.el_model}|{text}"
        else:
            key = f"edge|{self.voice}|{self.rate}|{self.pitch}|{text}"
        return _CACHE / f"{hashlib.sha1(key.encode()).hexdigest()}.mp3"

    async def synthesize(self, text: str) -> bytes:
        """Return MP3 bytes for `text`, cached on disk by content + voice."""
        if not text.strip():
            return b""
        prov = self.resolved_provider()
        cached = self._cache_path(text, prov)
        if cached.exists():
            return cached.read_bytes()

        data = b""
        if prov == "elevenlabs":
            data = await self._synth_elevenlabs(text)
            if not data:  # graceful fallback so JARVIS still speaks
                prov = "edge"
                cached = self._cache_path(text, "edge")
                if cached.exists():
                    return cached.read_bytes()
        if not data:
            data = await self._synth_edge(text)

        if data:
            cached.write_bytes(data)
        return data

    async def _synth_edge(self, text: str) -> bytes:
        import edge_tts

        communicate = edge_tts.Communicate(text, voice=self.voice, rate=self.rate, pitch=self.pitch)
        buf = bytearray()
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                buf.extend(chunk["data"])
        return bytes(buf)

    async def _synth_elevenlabs(self, text: str) -> bytes:
        import httpx

        key = os.environ.get("ELEVENLABS_API_KEY")
        if not key or not self.el_voice_id:
            return b""
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{self.el_voice_id}"
        payload = {
            "text": text,
            "model_id": self.el_model,
            "voice_settings": {"stability": self.el_stability, "similarity_boost": self.el_similarity},
        }
        headers = {"xi-api-key": key, "accept": "audio/mpeg", "content-type": "application/json"}
        try:
            async with httpx.AsyncClient(timeout=30) as c:
                r = await c.post(url, json=payload, headers=headers)
                r.raise_for_status()
                return r.content
        except Exception:
            return b""

    @staticmethod
    async def list_voices() -> list[dict[str, str]]:
        import edge_tts

        voices = await edge_tts.list_voices()
        return [
            {"name": v["ShortName"], "gender": v["Gender"], "locale": v["Locale"]}
            for v in voices
            if v["Locale"].startswith("en")
        ]
