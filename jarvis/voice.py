"""JARVIS voice via edge-tts (free Microsoft neural TTS). Synthesizes MP3 bytes
that the browser plays, so no audio device handling is needed server-side."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

_CACHE = Path(__file__).resolve().parent.parent / "data" / "voice_cache"


class Voice:
    def __init__(self, cfg: dict[str, Any]):
        v = cfg.get("voice", {})
        self.enabled: bool = v.get("enabled", True)
        self.voice: str = v.get("voice", "en-GB-RyanNeural")
        self.rate: str = v.get("rate", "-4%")
        self.pitch: str = v.get("pitch", "-2Hz")
        self.greet_on_start: bool = v.get("greet_on_start", True)
        _CACHE.mkdir(parents=True, exist_ok=True)

    def _cache_path(self, text: str) -> Path:
        key = hashlib.sha1(f"{self.voice}|{self.rate}|{self.pitch}|{text}".encode()).hexdigest()
        return _CACHE / f"{key}.mp3"

    async def synthesize(self, text: str) -> bytes:
        """Return MP3 bytes for the given text, cached on disk by content."""
        if not text.strip():
            return b""
        cached = self._cache_path(text)
        if cached.exists():
            return cached.read_bytes()

        import edge_tts

        communicate = edge_tts.Communicate(
            text, voice=self.voice, rate=self.rate, pitch=self.pitch
        )
        buf = bytearray()
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                buf.extend(chunk["data"])
        data = bytes(buf)
        if data:
            cached.write_bytes(data)
        return data

    @staticmethod
    async def list_voices() -> list[dict[str, str]]:
        import edge_tts

        voices = await edge_tts.list_voices()
        return [
            {"name": v["ShortName"], "gender": v["Gender"], "locale": v["Locale"]}
            for v in voices
            if v["Locale"].startswith("en")
        ]
