"""Launch programs from JARVIS.

Everything here is whitelisted or derived server-side:
  * launch_terminal() only accepts known target names mapped to fixed commands.
  * launch_app() derives the run command from the project's own files and only
    runs projects that live under a configured project root.
Nothing executes a string supplied verbatim by the browser.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from .util import ROOT, load_config, which

_NEW_CONSOLE = getattr(subprocess, "CREATE_NEW_CONSOLE", 0) if sys.platform == "win32" else 0
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0

# Interactive targets → the command to open in a fresh terminal window.
_TERMINAL_TARGETS = {
    "python": ["python"],
    "node": ["node"],
    "claude": ["claude"],
    "uv": ["uv", "--help"],
    "gh": ["gh", "status"],
}


def _resolve(arg0: str, cwd: str | None = None) -> str | None:
    """Resolve a command's first token: an existing file path (e.g. a .bat in the
    project) is used as-is; otherwise look it up on PATH."""
    p = Path(arg0)
    if p.exists():
        return str(p)
    if cwd and (Path(cwd) / arg0).exists():
        return str(Path(cwd) / arg0)
    return which(arg0)


def _spawn_console(cmd: list[str], cwd: str | None = None) -> dict[str, Any]:
    """Open a new, visible console window running `cmd` (kept open with cmd /k on Windows)."""
    exe = _resolve(cmd[0], cwd)
    if not exe:
        return {"ok": False, "error": f"{cmd[0]} not found"}
    try:
        if sys.platform == "win32":
            # cmd /k keeps the window open after the program exits.
            full = ["cmd", "/k", exe, *cmd[1:]]
            subprocess.Popen(full, cwd=cwd, creationflags=_NEW_CONSOLE)
        else:
            subprocess.Popen([exe, *cmd[1:]], cwd=cwd)
        return {"ok": True, "launched": " ".join(cmd), "cwd": cwd}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# GUI apps launch detached (no console window).
_GUI_TARGETS = {"code": ["code"]}


def launch_terminal(target: str) -> dict[str, Any]:
    if target in _GUI_TARGETS:
        cmd = _GUI_TARGETS[target]
        exe = which(cmd[0])
        if not exe:
            return {"ok": False, "error": f"{cmd[0]} not found"}
        try:
            subprocess.Popen([exe, *cmd[1:]], creationflags=_NO_WINDOW)
            return {"ok": True, "launched": " ".join(cmd)}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
    cmd = _TERMINAL_TARGETS.get(target)
    if not cmd:
        return {"ok": False, "error": f"'{target}' is not launchable"}
    return _spawn_console(cmd)


# Launcher .bat/.cmd stems in priority order (most app-like first). Many of
# Mark's projects ship a desktop/app launcher script — prefer it over guessing.
_LAUNCHER_PRIORITY = [
    "run_desktop", "run_app", "rundesktop", "runapp", "run_gui", "rungui",
    "start", "run", "launch", "gui", "app", "main",
]


def _find_launcher(project: Path) -> Path | None:
    scripts: dict[str, Path] = {}
    for ext in ("*.bat", "*.cmd"):
        for p in project.glob(ext):
            scripts.setdefault(p.stem.lower(), p)
    for name in _LAUNCHER_PRIORITY:
        if name in scripts:
            return scripts[name]
    # Fall back to any run*/start*/launch* script (but not install/build/setup).
    for stem, p in scripts.items():
        if stem.startswith(("run", "start", "launch")):
            return p
    return None


def detect_run(project: Path) -> dict[str, Any] | None:
    """Figure out how to run a project's app from its files. Returns a launch
    plan {cmd, label} or None if nothing obvious is runnable."""
    # 0. An explicit launcher script wins — that's the project's intended entry.
    bat = _find_launcher(project)
    if bat is not None:
        return {"cmd": [str(bat)], "label": bat.name}

    # 1. pyproject console script → `uv run <script>`
    py = project / "pyproject.toml"
    if py.exists():
        try:
            import tomllib

            data = tomllib.loads(py.read_text(encoding="utf-8"))
            scripts = data.get("project", {}).get("scripts", {})
            if scripts:
                name = next(iter(scripts))
                return {"cmd": ["uv", "run", name], "label": f"uv run {name}"}
            deps = " ".join(data.get("project", {}).get("dependencies", [])).lower()
            if "streamlit" in deps:
                for f in ("app.py", "streamlit_app.py", "main.py"):
                    if (project / f).exists():
                        return {"cmd": ["uv", "run", "streamlit", "run", f], "label": f"streamlit run {f}"}
        except Exception:
            pass

    # 2. Node project with a start/dev script
    pkg = project / "package.json"
    if pkg.exists():
        try:
            import json

            scripts = json.loads(pkg.read_text(encoding="utf-8")).get("scripts", {})
            for s in ("start", "dev", "serve"):
                if s in scripts:
                    return {"cmd": ["npm", "run", s] if s != "start" else ["npm", "start"], "label": f"npm {s}"}
        except Exception:
            pass

    # 3. Common entry-point files
    if (project / "manage.py").exists():
        return {"cmd": ["uv", "run", "python", "manage.py", "runserver"], "label": "django runserver"}
    for f in ("app.py", "main.py", "run.py", "__main__.py"):
        if (project / f).exists():
            runner = ["uv", "run", "python", f] if (project / "pyproject.toml").exists() else ["python", f]
            return {"cmd": runner, "label": f"python {f}"}

    # 4. Package with __main__ (uv run python -m <pkg>)
    if py.exists():
        for child in project.iterdir():
            if child.is_dir() and (child / "__main__.py").exists():
                return {"cmd": ["uv", "run", "python", "-m", child.name], "label": f"python -m {child.name}"}
    return None


def _under_roots(p: Path) -> bool:
    cfg = (load_config().get("projects", {}) or {}).get("roots") or []
    roots = [Path(r) for r in cfg] or [Path.home() / "Documents" / "PythonProjects"]
    rp = str(p.resolve()).lower()
    return any(rp.startswith(str(Path(r).resolve()).lower()) for r in roots)


def launch_app(path: str) -> dict[str, Any]:
    p = Path(path)
    if not p.exists() or not p.is_dir():
        return {"ok": False, "error": "project not found"}
    if not _under_roots(p):
        return {"ok": False, "error": "path outside project roots"}
    plan = detect_run(p)
    if not plan:
        return {"ok": False, "error": "no runnable app detected"}
    res = _spawn_console(plan["cmd"], cwd=str(p))
    res["plan"] = plan["label"]
    return res


_CHROMIUM_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]


def find_chromium() -> str | None:
    for c in _CHROMIUM_CANDIDATES:
        if c and Path(c).exists():
            return c
    return which("chrome") or which("msedge")


def open_app_window(url: str) -> bool:
    """Open JARVIS as a chromeless Chromium app window with autoplay enabled, so
    the voice greets on load with no click. Uses a dedicated user-data-dir so the
    --autoplay-policy flag actually applies (Chrome ignores flags when joining an
    existing instance). Falls back to the default browser if no Chromium is found."""
    exe = find_chromium()
    if exe:
        profile = ROOT / "data" / "app_profile"
        profile.mkdir(parents=True, exist_ok=True)
        try:
            subprocess.Popen(
                [
                    exe,
                    f"--app={url}",
                    "--autoplay-policy=no-user-gesture-required",
                    f"--user-data-dir={profile}",
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--window-size=1500,950",
                ],
                creationflags=_NO_WINDOW,
            )
            return True
        except Exception:
            pass
    import webbrowser

    webbrowser.open(url)
    return False


def _library_roots() -> list[Path]:
    cfg = (load_config().get("library", {}) or {}).get("roots") or []
    out: list[Path] = []
    for r in cfg:
        p = r.get("path") if isinstance(r, dict) else r
        if p:
            out.append(Path(p))
    return out


def launch_file(path: str) -> dict[str, Any]:
    """Launch a shortcut/executable from a configured library or project root.
    Guarded: the file must live under an allowed root and be a launchable type."""
    p = Path(path)
    if not p.exists() or not p.is_file():
        return {"ok": False, "error": "file not found"}
    if p.suffix.lower() not in {".lnk", ".url", ".exe", ".bat", ".cmd"}:
        return {"ok": False, "error": "not a launchable file"}
    roots = _library_roots() + [Path(r) for r in ((load_config().get("projects", {}) or {}).get("roots") or [])]
    rp = str(p.resolve()).lower()
    if not any(rp.startswith(str(root.resolve()).lower()) for root in roots):
        return {"ok": False, "error": "path outside allowed roots"}
    try:
        if sys.platform == "win32":
            os.startfile(str(p))  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", str(p)])
        return {"ok": True, "launched": p.name}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _is_up(url: str) -> bool:
    import urllib.request

    try:
        with urllib.request.urlopen(url.rstrip("/") + "/api/health", timeout=1.2):
            return True
    except Exception:
        return False


def launch_hermes() -> dict[str, Any]:
    """Open HERMES as a chromeless app window; start its server first if needed."""
    conn = load_config().get("connectors", {}) or {}
    url = str(conn.get("hermes_chat_url") or "http://127.0.0.1:7718").rstrip("/")
    path = conn.get("hermes_chat_path") or ""
    if not (path and Path(path).exists()):
        for r in (load_config().get("projects", {}) or {}).get("roots") or []:
            cand = Path(r) / "Hermes"
            if cand.exists():
                path = str(cand)
                break
    if _is_up(url):
        open_app_window(url)
        return {"ok": True, "action": "opened", "url": url}
    p = Path(path) if path else None
    if not p or not p.exists():
        return {"ok": False, "error": "Hermes project not found"}
    try:
        subprocess.Popen([which("uv") or "uv", "run", "hermes"], cwd=str(p), creationflags=_NO_WINDOW)
        return {"ok": True, "action": "starting", "url": url}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def launch_obsidian() -> dict[str, Any]:
    vault = (load_config().get("obsidian", {}) or {}).get("vault", "")
    if not vault or not Path(vault).exists():
        return {"ok": False, "error": "vault not configured"}
    name = Path(vault).name
    try:
        if sys.platform == "win32":
            os.startfile(f"obsidian://open?vault={name}")  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", f"obsidian://open?vault={name}"])
        return {"ok": True, "launched": f"obsidian://open?vault={name}"}
    except Exception as exc:
        # Fall back to opening the vault folder.
        try:
            if sys.platform == "win32":
                os.startfile(vault)  # type: ignore[attr-defined]
            return {"ok": True, "launched": vault}
        except Exception:
            return {"ok": False, "error": str(exc)}
