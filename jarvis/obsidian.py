"""Obsidian vault connector: status, search, and writing daily system snapshots."""

from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import Any


class Obsidian:
    def __init__(self, vault: str, log_folder: str = "JARVIS Logs"):
        self.vault = Path(vault) if vault else None
        self.log_folder = log_folder

    @property
    def available(self) -> bool:
        return bool(self.vault and self.vault.exists())

    def status(self) -> dict[str, Any]:
        if not self.available:
            return {
                "available": False,
                "vault": str(self.vault) if self.vault else None,
                "notes": 0,
                "recent": [],
            }
        notes = list(self.vault.rglob("*.md"))
        recent = sorted(notes, key=lambda p: p.stat().st_mtime, reverse=True)[:6]
        return {
            "available": True,
            "vault": str(self.vault),
            "notes": len(notes),
            "recent": [
                {
                    "title": p.stem,
                    "rel": str(p.relative_to(self.vault)),
                    "mtime": p.stat().st_mtime,
                }
                for p in recent
            ],
        }

    def search(self, query: str, limit: int = 12) -> list[dict[str, Any]]:
        if not self.available or not query.strip():
            return []
        q = query.lower()
        hits: list[dict[str, Any]] = []
        for p in self.vault.rglob("*.md"):
            try:
                text = p.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            if q in p.stem.lower() or q in text.lower():
                idx = text.lower().find(q)
                snippet = ""
                if idx >= 0:
                    start = max(0, idx - 60)
                    snippet = text[start : idx + 80].replace("\n", " ").strip()
                hits.append(
                    {"title": p.stem, "rel": str(p.relative_to(self.vault)), "snippet": snippet}
                )
            if len(hits) >= limit:
                break
        return hits

    def write_snapshot(self, summary_md: str) -> dict[str, Any]:
        """Write a dated system snapshot note into the vault's log folder."""
        if not self.available:
            return {"ok": False, "error": "vault not available"}
        folder = self.vault / self.log_folder
        folder.mkdir(parents=True, exist_ok=True)
        today = _dt.date.today().isoformat()
        note = folder / f"JARVIS Snapshot {today}.md"
        stamp = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
        body = f"---\ntags: [jarvis, snapshot]\ncreated: {stamp}\n---\n\n# JARVIS System Snapshot — {today}\n\n*Generated {stamp}*\n\n{summary_md}\n"
        note.write_text(body, encoding="utf-8")
        return {"ok": True, "path": str(note), "rel": str(note.relative_to(self.vault))}
