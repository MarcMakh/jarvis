"""Checks for and applies updates across the toolchain.

Apply actions are whitelisted: the UI only ever names a known target, and each
target maps to a hard-coded command here. Nothing user-supplied is executed —
package ids/names are validated against winget's and npm's own grammars.

What is checked (in parallel, in the background — see state.py):
  winget  the dev toolchain (Python, Git, gh, Node, uv, VS Code, LM Studio…);
          everything else winget can upgrade is counted as "others"
  npm     global packages, upgradable ONE AT A TIME (a blanket `npm update -g`
          would also bump tools you pinned on purpose)
  env     JARVIS's own direct dependencies (information only: a uv-managed
          environment is upgraded with `uv lock --upgrade` while JARVIS is
          stopped, not from inside the running server)
  claude  Claude Code self-update
"""

from __future__ import annotations

import json
import re
import time
import tomllib
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from .util import ROOT, clean_env, run, which

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
    "ElementLabs.LMStudio": "LM Studio",
    "Obsidian.Obsidian": "Obsidian",
    "Microsoft.PowerShell": "PowerShell",
    "Microsoft.WindowsTerminal": "Windows Terminal",
}
_WINGET_ID_RE = re.compile(r"^[A-Za-z0-9.+_-]+$")
_NPM_NAME_RE = re.compile(r"^(?:@[a-z0-9-~][a-z0-9-._~]*/)?[a-z0-9-~][a-z0-9-._~]*$")


def check_updates() -> dict[str, Any]:
    """Return available updates without changing anything."""
    with ThreadPoolExecutor(max_workers=4) as ex:
        f_wg = ex.submit(_winget_check)
        f_npm = ex.submit(_npm_outdated)
        f_env = ex.submit(_env_outdated)
        f_cl = ex.submit(_claude_check)
        return {
            "winget": f_wg.result(),
            "npm": f_npm.result(),
            "env": f_env.result(),
            "claude": f_cl.result(),
            "checked": time.time(),
        }


def _parse_winget_table(output: str) -> list[dict[str, str]]:
    lines = [ln for ln in output.splitlines() if "█" not in ln and "▒" not in ln]
    hdr = idx = None
    for i, ln in enumerate(lines):
        if ln.startswith("Name") and "Id" in ln and "Available" in ln:
            hdr, idx = ln, i
            break
    if hdr is None or idx is None:
        return []
    iId, iVer, iAvail = hdr.index("Id"), hdr.index("Version"), hdr.index("Available")
    iSrc = hdr.index("Source") if "Source" in hdr else len(hdr) + 40
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
        "label": "Dev toolchain",
        "count": len(dev),
        "items": dev,
        "others": len(rows) - len(dev),
        "available": bool(which("winget")),
    }


def lmstudio_update() -> dict[str, Any] | None:
    """Kept for older callers: LM Studio's entry from the toolchain check."""
    for row in _winget_check()["items"]:
        if row["id"].lower() == "elementlabs.lmstudio":
            return {"id": row["id"], "current": row["current"], "latest": row["latest"]}
    return None


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


def _direct_deps() -> set[str]:
    try:
        data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        deps = data.get("project", {}).get("dependencies", [])
    except Exception:
        return set()
    return {re.split(r"[<>=!~\[; ]", d, maxsplit=1)[0].strip().lower().replace("_", "-") for d in deps}


def _env_outdated() -> dict[str, Any]:
    """JARVIS's own direct dependencies that have a newer release (via uv)."""
    uv = which("uv")
    pkgs: list[dict[str, Any]] = []
    if uv:
        py = ROOT / ".venv" / "Scripts" / "python.exe"
        cmd = [uv, "pip", "list", "--outdated", "--format", "json"]
        if py.exists():
            cmd += ["--python", str(py)]
        res = run(cmd, timeout=90, cwd=str(ROOT))
        direct = _direct_deps()
        if res.ok and res.out.strip():
            try:
                for p in json.loads(res.out):
                    if p["name"].lower().replace("_", "-") in direct:
                        pkgs.append({"name": p["name"], "current": p["version"],
                                     "latest": p["latest_version"]})
            except Exception:
                pass
    return {"target": "env", "label": "JARVIS environment", "count": len(pkgs), "items": pkgs,
            "note": "Upgrade with `uv lock --upgrade` while JARVIS is stopped."}


def _npm_outdated() -> dict[str, Any]:
    npm = which("npm")
    pkgs: list[dict[str, Any]] = []
    if npm:
        res = run([npm, "outdated", "-g", "--json"], timeout=90)
        # npm exits non-zero when packages are outdated; parse regardless.
        raw = res.out.strip()
        if raw:
            try:
                for name, info in json.loads(raw).items():
                    if not isinstance(info, dict) or info.get("current") == info.get("latest"):
                        continue
                    pkgs.append({"name": name, "current": info.get("current"),
                                 "latest": info.get("latest")})
            except Exception:
                pass
    return {"target": "npm", "label": "npm global packages", "count": len(pkgs), "items": pkgs}


def _claude_check() -> dict[str, Any]:
    exe = which("claude")
    current = None
    if exe:
        res = run([exe, "--version"], timeout=12, env=clean_env())
        m = re.search(r"\d+\.\d+\.\d+", res.text)
        current = m.group(0) if m else None
    return {
        "target": "claude",
        "label": "Claude Code",
        "count": 0,
        "current": current,
        "items": [],
        "available": bool(exe),
        "note": "Self-updating; “update” fetches the latest build now.",
    }


def apply_update(target: str, package: str | None = None) -> dict[str, Any]:
    """Apply an update for a whitelisted target. Returns command output."""
    if target == "claude":
        exe = which("claude")
        if not exe:
            return {"ok": False, "target": target, "error": "claude CLI not found"}
        cmd = [exe, "update"]
        env = clean_env()
    elif target == "npm":
        npm = which("npm")
        if not npm:
            return {"ok": False, "target": target, "error": "npm not found"}
        name = (package or "").strip()
        if not _NPM_NAME_RE.match(name):
            return {"ok": False, "target": target, "error": "name one valid npm package to update"}
        cmd = [npm, "install", "-g", f"{name}@latest"]
        env = None
    else:
        return {"ok": False, "target": target, "error": "unknown update target"}

    res = run(cmd, timeout=600, env=env)
    return {
        "ok": res.ok,
        "target": target,
        "package": package,
        "command": " ".join(cmd),
        "code": res.code,
        "output": (res.out or res.err).strip()[-4000:],
    }
