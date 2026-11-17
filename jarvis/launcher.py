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

from .util import data_dir, is_under, load_config, project_roots, which

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
    """Inside a configured project root? (component-wise — see util.is_under)"""
    return is_under(p, project_roots())


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
        profile = data_dir() / "app_profile"
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
    if not is_under(p, [*_library_roots(), *project_roots()]):
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
        for r in project_roots():
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


def _project_dir(*subpath: str) -> Path | None:
    """First existing <root>/<subpath...> under the configured project roots."""
    for r in project_roots():
        cand = Path(r).joinpath(*subpath)
        if cand.exists():
            return cand
    return None


def _launch_trading(subpath: tuple[str, ...], port: int, name: str,
                    script: str = "run_app.py") -> dict[str, Any]:
    """Open a local-web-app project (QuantFin family, ChessTraining web UI):
    if its server is already up just attach a chromeless window; otherwise
    fire the project's launcher script, which starts the server and opens its
    own native window. Uses the py -3.13 launcher — these projects' packages
    live there, not in the default python."""
    url = f"http://127.0.0.1:{port}"
    try:
        import urllib.request

        with urllib.request.urlopen(url, timeout=1.2):
            up = True
    except Exception:
        up = False
    if up:
        open_app_window(url)
        return {"ok": True, "action": "opened", "url": url}
    folder = _project_dir(*subpath)
    if folder is None or not (folder / script).exists():
        return {"ok": False, "error": f"{name} project not found"}
    try:
        subprocess.Popen([which("py") or "py", "-3.13", script],
                         cwd=str(folder), creationflags=_NO_WINDOW)
        return {"ok": True, "action": "starting", "url": url}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def launch_quantflow() -> dict[str, Any]:
    """QuantFlow — intraday volume system (Streamlit on :8502)."""
    return _launch_trading(("QuantFlow",), 8502, "QuantFlow")


def launch_quantfin() -> dict[str, Any]:
    """QuantFin — end-of-day multi-strategy system (Streamlit on :8501)."""
    return _launch_trading(("QuantFin", "trading_architecture"), 8501, "QuantFin")


def launch_chess() -> dict[str, Any]:
    """ChessTraining — the new web UI (pywebview shell on :8140), not the
    legacy Tkinter app."""
    return _launch_trading(("ChessTraining",), 8140, "ChessTraining",
                           script="run_web.py")


def launch_pantry() -> dict[str, Any]:
    """Pantry — local pantry / meal-planner / grocery-price app (FastAPI on
    :8137, its own native window). Like the trading apps, attach a window if the
    server is already up; otherwise fire its run_app.py, which builds the UI if
    needed, starts the server and opens a native window. Pantry is uv-managed, so
    it launches via `uv run` (not the py -3.13 launcher)."""
    url = "http://127.0.0.1:8137"
    if _is_up(url):
        open_app_window(url)
        return {"ok": True, "action": "opened", "url": url}
    folder = _project_dir("Pantry App")
    if folder is None or not (folder / "run_app.py").exists():
        return {"ok": False, "error": "Pantry project not found"}
    try:
        subprocess.Popen([which("uv") or "uv", "run", "python", "run_app.py"],
                         cwd=str(folder), creationflags=_NO_WINDOW)
        return {"ok": True, "action": "starting", "url": url}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _center_dir(cfg_key: str, folder_name: str) -> Path | None:
    """Locate a Center-family app folder (Game Center, Emu Center): an explicit
    config path, else a folder of that name under any configured project root,
    else the default location."""
    conn = load_config().get("connectors", {}) or {}
    path = conn.get(cfg_key) or ""
    if path and Path(path).exists():
        return Path(path)
    for r in project_roots():
        cand = Path(r) / folder_name
        if cand.exists():
            return cand
    return None


def gamecenter_dir() -> Path | None:
    return _center_dir("gamecenter_path", "GameCenter")


def emucenter_dir() -> Path | None:
    return _center_dir("emucenter_path", "EmuCenter")


def moviecenter_dir() -> Path | None:
    return _center_dir("moviecenter_path", "MovieCenter")


def _center_target(folder: Path, exe_name: str, bat_name: str) -> Path | None:
    """The best entry point to launch, most self-contained first: the packaged
    exe, then a start .bat, then the source app.py."""
    exe = folder / exe_name
    if exe.exists():
        return exe
    for name in (bat_name, "start.bat", "run.bat"):
        b = folder / name
        if b.exists():
            return b
    app = folder / "app.py"
    return app if app.exists() else None


def _launch_center(folder: Path | None, exe_name: str, bat_name: str, label: str,
                   args: tuple[str, ...] = ()) -> dict[str, Any]:
    """Open a Center-family library app. The packaged exe starts its own server
    and native window; if it's already running, a fresh launch just attaches a
    new window to the existing server, so JARVIS only has to fire it off."""
    if folder is None:
        return {"ok": False, "error": f"{label} project not found"}
    target = _center_target(folder, exe_name, bat_name)
    if target is None:
        return {"ok": False, "error": f"no {label} launcher found"}
    try:
        if target.suffix.lower() == ".py":
            # Source fallback: needs pywebview for a native window, else the app
            # falls back to a Chrome app window on its own.
            subprocess.Popen(
                [which("python") or "python", str(target), *args],
                cwd=str(folder),
                creationflags=_NO_WINDOW,
            )
        elif sys.platform == "win32":
            # detached, like a double-click
            os.startfile(str(target), arguments=" ".join(args))  # type: ignore[attr-defined]
        else:
            subprocess.Popen([str(target), *args], cwd=str(folder))
        return {"ok": True, "launched": target.name}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def launch_gamecenter() -> dict[str, Any]:
    """Open the Game Center app (PC game library on :8130, its own native window)."""
    return _launch_center(gamecenter_dir(), "Game Center.exe", "Start Game Center.bat", "Game Center")


def launch_emucenter() -> dict[str, Any]:
    """Open the emulator/ROM library — now Game Center's Emulation tab (`--emu`).
    Falls back to the old standalone Emu Center app (:8131) if Game Center isn't there."""
    gc = gamecenter_dir()
    if gc is not None and (gc / "emu.py").exists():
        return _launch_center(gc, "Game Center.exe", "Start Game Center.bat", "Game Center", ("--emu",))
    return _launch_center(emucenter_dir(), "Emu Center.exe", "Start Emu Center.bat", "Emu Center")


def launch_moviecenter() -> dict[str, Any]:
    """Open the Movie Center app (movie/TV library and player on :8132, its own native window)."""
    return _launch_center(moviecenter_dir(), "Movie Center.exe", "Start Movie Center.bat", "Movie Center")


def launch_obsidian(rel: str = "") -> dict[str, Any]:
    """Open the vault in Obsidian — at a specific note when `rel` (a path
    relative to the vault) is given and really exists inside it."""
    from urllib.parse import quote

    vault = (load_config().get("obsidian", {}) or {}).get("vault", "")
    if not vault or not Path(vault).exists():
        return {"ok": False, "error": "vault not configured"}
    name = Path(vault).name
    uri = f"obsidian://open?vault={quote(name)}"
    if rel:
        note = Path(vault) / rel
        if not note.is_file() or not is_under(note, [vault]):
            return {"ok": False, "error": "note not found in the vault"}
        uri += "&file=" + quote(str(Path(rel).with_suffix("")).replace("\\", "/"))
    try:
        if sys.platform == "win32":
            os.startfile(uri)  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", uri])
        return {"ok": True, "launched": uri}
    except Exception as exc:
        # Fall back to opening the vault folder.
        try:
            if sys.platform == "win32":
                os.startfile(vault)  # type: ignore[attr-defined]
            return {"ok": True, "launched": vault}
        except Exception:
            return {"ok": False, "error": str(exc)}
