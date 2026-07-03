"""Scan configured 'library' roots (games, emulators, apps) for launchable
shortcuts, so JARVIS can surface them on the ecosystem map and launch them.

Only top-level launchable files (.lnk/.url/.exe/.bat/.cmd) are listed; obvious
non-game utilities (uninstallers, redistributables) are skipped.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .util import load_config

_LAUNCH_EXTS = {".lnk", ".url", ".exe", ".bat", ".cmd"}
_SKIP = ("unins", "uninstall", "setup", "redist", "vcredist", "directx", "dxsetup", "7z", "winrar")


def roots() -> list[dict[str, str]]:
    """Configured library roots that actually exist on disk."""
    cfg = (load_config().get("library", {}) or {}).get("roots") or []
    out: list[dict[str, str]] = []
    for r in cfg:
        if isinstance(r, dict):
            name, path = r.get("name"), r.get("path")
        else:
            path, name = r, Path(r).name
        if path and Path(path).exists():
            out.append({"name": name or Path(path).name, "path": path})
    return out


def scan(limit_per_root: int = 60) -> list[dict[str, Any]]:
    """List launchable shortcuts under each library root (capped, alphabetised)."""
    result: list[dict[str, Any]] = []
    for root in roots():
        base = Path(root["path"])
        items: list[dict[str, str]] = []
        try:
            for f in sorted(base.iterdir(), key=lambda p: p.name.lower()):
                if not f.is_file() or f.suffix.lower() not in _LAUNCH_EXTS:
                    continue
                if any(s in f.stem.lower() for s in _SKIP):
                    continue
                items.append({"name": f.stem, "path": str(f), "ext": f.suffix.lower().lstrip(".")})
        except Exception:
            pass
        result.append(
            {"name": root["name"], "path": root["path"], "count": len(items), "items": items[:limit_per_root]}
        )
    return result
