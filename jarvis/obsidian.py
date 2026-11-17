"""Obsidian vault connector: status, search, quick capture, and writing notes
(daily system snapshots, exported Hermes chats)."""

from __future__ import annotations

import datetime as _dt
import re
from pathlib import Path
from typing import Any

from .util import is_under, load_config

_SKIP_DIRS = {".obsidian", ".trash", ".git"}
_BAD_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def from_config() -> "Obsidian":
    cfg = load_config().get("obsidian", {}) or {}
    return Obsidian(cfg.get("vault", ""), cfg.get("log_folder", "JARVIS Logs"),
                    cfg.get("inbox", ""))


class Obsidian:
    def __init__(self, vault: str, log_folder: str = "JARVIS Logs", inbox: str = ""):
        self.vault = Path(vault) if vault else None
        self.log_folder = log_folder
        # Quick-capture target, relative to the vault.
        self.inbox = inbox or f"{log_folder}/Inbox.md"

    @property
    def available(self) -> bool:
        return bool(self.vault and self.vault.exists())

    def _notes(self) -> list[Path]:
        assert self.vault is not None
        return [p for p in self.vault.rglob("*.md")
                if not any(part in _SKIP_DIRS for part in p.parts)]

    def status(self) -> dict[str, Any]:
        if not self.available:
            return {
                "available": False,
                "vault": str(self.vault) if self.vault else None,
                "notes": 0,
                "recent": [],
            }
        assert self.vault is not None
        notes = self._notes()
        stamped = []
        for p in notes:
            try:
                stamped.append((p.stat().st_mtime, p))
            except OSError:
                continue
        stamped.sort(key=lambda t: t[0], reverse=True)
        return {
            "available": True,
            "vault": str(self.vault),
            "name": self.vault.name,
            "notes": len(notes),
            "inbox": self.inbox,
            "recent": [
                {"title": p.stem, "rel": str(p.relative_to(self.vault)), "mtime": mt}
                for mt, p in stamped[:12]
            ],
        }

    def search(self, query: str, limit: int = 12) -> list[dict[str, Any]]:
        """Keyword search. A note matches when it contains every word of the
        query (anywhere in its title or body — not necessarily side by side).
        Ranked: exact phrase in the title, all words in the title, then by how
        often the words occur; each hit carries a snippet around the first one."""
        if not self.available or not query.strip():
            return []
        assert self.vault is not None
        q = " ".join(query.lower().split())
        words = [w for w in q.split(" ") if w]
        scored: list[tuple[float, dict[str, Any]]] = []
        for p in self._notes():
            try:
                text = p.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            title, low = p.stem.lower(), text.lower()
            if not all(w in title or w in low for w in words):
                continue
            score = float(sum(low.count(w) for w in words))
            if q in title:
                score += 10_000
            elif all(w in title for w in words):
                score += 5_000
            if q in low:
                score += 500                     # the exact phrase appears somewhere
            idx = low.find(q)
            if idx < 0:
                idx = min((low.find(w) for w in words if w in low), default=-1)
            if idx >= 0:
                start = max(0, idx - 70)
                snippet = text[start: idx + len(q) + 110].replace("\n", " ").strip()
            else:
                snippet = text[:160].replace("\n", " ").strip()
            scored.append((score, {"title": p.stem, "rel": str(p.relative_to(self.vault)),
                                   "snippet": snippet}))
        scored.sort(key=lambda t: t[0], reverse=True)
        return [h for _, h in scored[:limit]]

    # ── writing ──────────────────────────────────────────────────────────

    def write_snapshot(self, summary_md: str) -> dict[str, Any]:
        """Write a dated system snapshot note into the vault's log folder."""
        if not self.available:
            return {"ok": False, "error": "vault not available"}
        assert self.vault is not None
        folder = self.vault / self.log_folder
        folder.mkdir(parents=True, exist_ok=True)
        today = _dt.date.today().isoformat()
        note = folder / f"JARVIS Snapshot {today}.md"
        stamp = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
        body = (f"---\ntags: [jarvis, snapshot]\ncreated: {stamp}\n---\n\n"
                f"# JARVIS System Snapshot — {today}\n\n*Generated {stamp}*\n\n{summary_md}\n")
        note.write_text(body, encoding="utf-8")
        return {"ok": True, "path": str(note), "rel": str(note.relative_to(self.vault))}

    def capture(self, text: str) -> dict[str, Any]:
        """Quick capture: append one timestamped line to the inbox note. Only
        ever appends — the note's existing content is never rewritten."""
        text = " ".join(str(text or "").split())
        if not text:
            return {"ok": False, "error": "nothing to capture"}
        if not self.available:
            return {"ok": False, "error": "vault not available"}
        assert self.vault is not None
        note = self.vault / self.inbox
        if not is_under(note.parent, [self.vault]) or note.suffix.lower() != ".md":
            return {"ok": False, "error": "inbox must be a .md note inside the vault"}
        note.parent.mkdir(parents=True, exist_ok=True)
        stamp = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
        new = not note.exists()
        with open(note, "a", encoding="utf-8") as fh:
            if new:
                fh.write("---\ntags: [jarvis, inbox]\n---\n\n# Inbox\n\n"
                         "Quick captures from JARVIS — newest at the bottom.\n\n")
            fh.write(f"- [ ] {stamp} — {text}\n")
        return {"ok": True, "rel": str(note.relative_to(self.vault)), "text": text}

    def write_note(self, folder: str, title: str, markdown: str,
                   tags: list[str] | None = None) -> dict[str, Any]:
        """Create a note (never overwrites: a numeric suffix is added when the
        title is taken). Used for exported Hermes conversations."""
        if not self.available:
            return {"ok": False, "error": "vault not available"}
        assert self.vault is not None
        safe = _BAD_NAME.sub(" ", str(title or "")).strip().rstrip(".")[:90] or "Untitled"
        dest = self.vault / (folder or "").strip("/\\")
        if not is_under(dest, [self.vault]):
            return {"ok": False, "error": "folder must be inside the vault"}
        dest.mkdir(parents=True, exist_ok=True)
        note = dest / f"{safe}.md"
        n = 2
        while note.exists():
            note = dest / f"{safe} ({n}).md"
            n += 1
        stamp = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
        tag_line = ", ".join(tags or [])
        front = f"---\ntags: [{tag_line}]\ncreated: {stamp}\n---\n\n" if tag_line else ""
        note.write_text(front + markdown.rstrip() + "\n", encoding="utf-8")
        return {"ok": True, "path": str(note), "rel": str(note.relative_to(self.vault))}
