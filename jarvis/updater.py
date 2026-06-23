"""Checks for and applies updates across the toolchain.

Apply actions are whitelisted: the UI only ever names a known target, and each
target maps to a hard-coded command here. Nothing user-supplied is ever executed.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from .util import run, which


import re

# winget IDs JARVIS treats as "dev toolchain" (everything else winget can upgrade
# — Chrome, Zoom, games — is hidden from the main view, available under "all").
_DEV_WINGET_IDS = {
    "Python.Python.3.14": "Python 3.14",
    "Python.Python.3.13": "Python 3.13",
    "Python.Python.3.12": "Python 3.12",
    "Git.Git": "Git",
    "GitHub.cli": "GitHub CLI",
    "BurntSushi.ripgrep.MSVC": "ripgrep",
    "JohnMacFarlane.Pandoc": "Pandoc",
    "OpenJS.NodeJS": "Node.js",
    "OpenJS.NodeJS.LTS": "Node.js LTS",
    "Microsoft.VisualStudioCode": "VS Code",
    "Rustlang.Rustup": "Rust (rustup)",
    "astral-sh.uv": "uv",
    "Docker.DockerDesktop": "Docker Desktop",
}
_WINGET_ID_RE = re.compile(r"^[A-Za-z0-9.+_-]+$")


def check_updates() -> dict[str, Any]:
    """Return available updates without changing anything."""
    return {
        "pip": _pip_outdated(),
        "npm": _npm_outdated(),
        "uv": _uv_check(),
        "claude": _claude_check(),
        "winget": _winget_check(),
    }


def _parse_winget_table(output: str) -> list[dict[str, str]]:
    lines = [ln for ln in output.splitlines() if "█" not in ln and "▒" not in ln]
    hdr = idx = None
    for i, ln in enumerate(lines):
        if ln.startswith("Name") and "Id" in ln and "Available" in ln:
            hdr, idx = ln, i
            break
    if hdr is None:
        return []
    iId, iVer, iAvail, iSrc = hdr.index("Id"), hdr.index("Version"), hdr.index("Available"), hdr.index("Source")
    rows: list[dict[str, str]] = []
    started = False
    for ln in lines[idx + 1:]:
        if set(ln.strip()) <= set("-") and ln.strip():
            continue  # separator
        if not ln.strip():
            if started:
                break
            continue
        try:
            name, pid = ln[:iId].strip(), ln[iId:iVer].strip()
            cur, avail = ln[iVer:iAvail].strip(), ln[iAvail:iSrc].strip()
        except Exception:
            continue
        if pid and avail and _WINGET_ID_RE.match(pid):
            rows.append({"name": name, "id": pid, "current": cur, "latest": avail})
            started = True
    return rows


def winget_all() -> list[dict[str, str]]:
    """Every package winget can upgrade (for the 'all system updates' drill-down)."""
    wg = which("winget")
    if not wg:
        return []
    res = run(
        [wg, "upgrade", "--include-unknown", "--disable-interactivity", "--accept-source-agreements"],
        timeout=150,
    )
    return _parse_winget_table(res.out)


def _winget_check() -> dict[str, Any]:
    rows = winget_all()
    dev = [r for r in rows if r["id"] in _DEV_WINGET_IDS or r["id"].startswith("Python.Python")]
    for r in dev:
        r["label"] = _DEV_WINGET_IDS.get(r["id"], r["name"])
    return {
        "target": "winget",
        "label": "Dev toolchain (winget)",
        "count": len(dev),
        "items": dev,
        "others": len(rows) - len(dev),
    }


def winget_upgrade(pkg_id: str) -> dict[str, Any]:
    """Upgrade a single winget package by exact ID (validated against winget's
    own ID grammar). Used for Python and the other toolchain installs."""
    wg = which("winget")
    if not wg:
        return {"ok": False, "error": "winget not found"}
    if not _WINGET_ID_RE.match(pkg_id or ""):
        return {"ok": False, "error": "invalid package id"}
    cmd = [
        wg, "upgrade", "--id", pkg_id, "--exact", "--silent",
        "--accept-package-agreements", "--accept-source-agreements", "--disable-interactivity",
    ]
    res = run(cmd, timeout=900)
    return {"ok": res.ok, "id": pkg_id, "code": res.code, "output": (res.out or res.err).strip()[-4000:]}


def _pip_outdated() -> dict[str, Any]:
    res = run([sys.executable, "-m", "pip", "list", "--outdated", "--format=json"], timeout=60)
    pkgs: list[dict[str, Any]] = []
    if res.ok and res.out.strip():
        try:
            for p in json.loads(res.out):
                pkgs.append(
                    {"name": p["name"], "current": p["version"], "latest": p["latest_version"]}
                )
        except Exception:
            pass
    return {"target": "pip", "label": "Python packages (this env)", "count": len(pkgs), "items": pkgs}


def _npm_outdated() -> dict[str, Any]:
    npm = which("npm")
    pkgs: list[dict[str, Any]] = []
    if npm:
        res = run([npm, "outdated", "-g", "--json"], timeout=60)
        # npm exits non-zero when packages are outdated; parse regardless.
        raw = res.out.strip()
        if raw:
            try:
                for name, info in json.loads(raw).items():
                    pkgs.append(
                        {
                            "name": name,
                            "current": info.get("current"),
                            "latest": info.get("latest"),
                        }
                    )
            except Exception:
                pass
    return {"target": "npm", "label": "npm global packages", "count": len(pkgs), "items": pkgs}


def _uv_check() -> dict[str, Any]:
    uv = which("uv")
    current = None
    if uv:
        res = run([uv, "--version"], timeout=10)
        current = res.text if res.ok else None
    return {
        "target": "uv",
        "label": "uv (self-update)",
        "count": 0,
        "current": current,
        "items": [],
        "note": "Self-update available; latest version is checked on apply.",
    }


def _claude_check() -> dict[str, Any]:
    exe = which("claude")
    current = None
    if exe:
        res = run([exe, "--version"], timeout=12)
        current = res.text if res.ok else None
    return {
        "target": "claude",
        "label": "Claude Code",
        "count": 0,
        "current": current,
        "items": [],
        "note": "Run update to fetch the latest Claude Code build.",
    }


def apply_update(target: str) -> dict[str, Any]:
    """Apply an update for a whitelisted target. Returns command output."""
    npm = which("npm")
    uv = which("uv")
    claude = which("claude")

    plans: dict[str, list[str] | None] = {
        "pip": [sys.executable, "-m", "pip", "install", "--upgrade", "pip"],
        "uv": [uv, "self", "update"] if uv else None,
        "npm": [npm, "update", "-g"] if npm else None,
        "claude": [claude, "update"] if claude else None,
    }

    if target not in plans:
        return {"ok": False, "target": target, "error": "unknown update target"}
    cmd = plans[target]
    if cmd is None:
        return {"ok": False, "target": target, "error": f"{target} executable not found"}

    res = run(cmd, timeout=300)
    return {
        "ok": res.ok,
        "target": target,
        "command": " ".join(cmd),
        "code": res.code,
        "output": (res.out or res.err).strip()[-4000:],
    }


def upgrade_package(name: str) -> dict[str, Any]:
    """Upgrade a single pip package in the current environment."""
    safe = name.strip()
    if not safe or not all(c.isalnum() or c in "-_.[]" for c in safe):
        return {"ok": False, "error": "invalid package name"}
    res = run([sys.executable, "-m", "pip", "install", "--upgrade", safe], timeout=300)
    return {
        "ok": res.ok,
        "package": safe,
        "code": res.code,
        "output": (res.out or res.err).strip()[-4000:],
    }
