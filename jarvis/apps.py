"""The app registry: every program JARVIS can open, in one place, with live
running status.

Before this, each app was a hand-written row in index.html plus its own
`/api/<app>/open` route, and nothing knew whether an app was actually up.
Now the registry drives the launcher tiles, the command palette, the brain's
"open X" action and the ecosystem map — and each entry reports `running`:

  * apps with a port  -> a TCP connect to 127.0.0.1:<port> (≈1 ms when up)
  * apps with a folder -> any process whose executable lives in that folder
    (catches the desktop apps that have no port, e.g. the Delivery Systems)

Add your own in config.toml:

    [[apps]]
    id = "notebook"
    name = "My Notebook"
    icon = "🧪"
    group = "Custom"
    path = 'C:\\Users\\me\\Documents\\PythonProjects\\Notebook'   # launched like a project
    port = 8899                                                 # optional: status + app window
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from . import launcher
from .util import is_under, load_config, project_roots, which

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0

_LMSTUDIO_EXE = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "LM Studio" / "LM Studio.exe"


def _proj(*sub: str) -> Path | None:
    for r in project_roots():
        cand = Path(r).joinpath(*sub)
        if cand.exists():
            return cand
    return None


def _run_project(folder: Path) -> Callable[[], dict[str, Any]]:
    return lambda: launcher.launch_app(str(folder))


def _open_lmstudio() -> dict[str, Any]:
    try:
        os.startfile(str(_LMSTUDIO_EXE))  # type: ignore[attr-defined]
        return {"ok": True, "launched": "LM Studio"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _builtin() -> list[dict[str, Any]]:
    """Mark's apps. An entry is listed only when its folder/exe really exists,
    so a fresh machine doesn't show dead tiles."""
    out: list[dict[str, Any]] = []

    def add(app_id: str, name: str, desc: str, icon: str, group: str, *,
            folder: Path | None = None, port: int | None = None,
            fn: Callable[[], dict[str, Any]] | None = None,
            proc: str | None = None, health: str = "") -> None:
        out.append({"id": app_id, "name": name, "desc": desc, "icon": icon, "group": group,
                    "folder": folder, "port": port, "fn": fn, "proc": proc, "health": health})

    hermes = _proj("Hermes")
    if hermes:
        add("hermes", "Hermes", "Open-LLM chat relay", "🜁", "AI", folder=hermes, port=7718,
            fn=launcher.launch_hermes, health="/api/health")
    if _LMSTUDIO_EXE.exists():
        add("lmstudio", "LM Studio", "Local model server", "🧠", "AI", port=1234,
            fn=_open_lmstudio, proc="lm studio.exe")
    vault = (load_config().get("obsidian", {}) or {}).get("vault", "")
    if vault and Path(vault).exists():
        add("obsidian", "Obsidian", "Knowledge vault", "📓", "AI",
            fn=launcher.launch_obsidian, proc="obsidian.exe")

    qflow = _proj("QuantFlow")
    if qflow:
        add("quantflow", "QuantFlow", "Intraday volume scanner + backtest", "🌊", "Trading",
            folder=qflow, port=8502, fn=launcher.launch_quantflow)
    qfin = _proj("QuantFin", "trading_architecture")
    if qfin:
        add("quantfin", "QuantFin", "End-of-day multi-strategy", "📈", "Trading",
            folder=qfin, port=8501, fn=launcher.launch_quantfin)

    for app_id, name, desc, icon, sub in (
        ("ai-delivery", "AI Delivery", "Agentic studies", "🤖",
         ("AIDeliverySystem", "Tier 2", "tier2_orchestrator_scaffold")),
        ("powerbi-delivery", "Power BI Delivery", "BI reports", "📊", ("PowerBIDeliverySystem",)),
        ("engineering-delivery", "Engineering Delivery", "Code studies + DXF", "🏗",
         ("EngineeringDeliverySystem",)),
        ("translation-delivery", "Translation Delivery", "EN⇄FR translation QA", "🌐",
         ("TranslationDeliverySystem",)),
    ):
        folder = _proj(*sub)
        if folder and launcher.detect_run(folder):
            add(app_id, name, desc, icon, "Delivery", folder=folder, fn=_run_project(folder))

    chess = _proj("ChessTraining")
    if chess:
        add("chess", "Chess Trainer", "Chess coach", "♟", "Play", folder=chess, port=8140,
            fn=launcher.launch_chess)
    gc = launcher.gamecenter_dir()
    if gc:
        add("gamecenter", "Game Center", "PC games library", "🎮", "Play", folder=gc, port=8130,
            fn=launcher.launch_gamecenter)
        if (gc / "emu.py").exists():
            add("emucenter", "Emulation", "Emulators & ROMs", "👾", "Play", folder=gc, port=8130,
                fn=launcher.launch_emucenter)
    if not any(a["id"] == "emucenter" for a in out):
        ec = launcher.emucenter_dir()
        if ec:
            add("emucenter", "Emu Center", "Emulators & ROMs", "👾", "Play", folder=ec, port=8131,
                fn=launcher.launch_emucenter)
    mc = launcher.moviecenter_dir()
    if mc:
        add("moviecenter", "Movie Center", "Movies, shows & player", "🎬", "Play", folder=mc,
            port=8132, fn=launcher.launch_moviecenter)
    pantry = _proj("Pantry App")
    if pantry:
        add("pantry", "Pantry", "Recipes, meal plan & prices", "🥫", "Home", folder=pantry,
            port=8137, fn=launcher.launch_pantry)
    return out


def _custom() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in load_config().get("apps", []) or []:
        if not isinstance(row, dict) or not row.get("id"):
            continue
        folder = Path(row["path"]) if row.get("path") else None
        if folder is not None and not (folder.is_dir() and is_under(folder, project_roots())):
            continue  # only folders inside your project roots may be launched
        port = int(row["port"]) if row.get("port") else None
        url = str(row.get("url") or (f"http://127.0.0.1:{port}" if port else ""))

        def _open(folder: Path | None = folder, port: int | None = port,
                  url: str = url) -> dict[str, Any]:
            if port and _port_open(port) and url:
                launcher.open_app_window(url)
                return {"ok": True, "action": "opened", "url": url}
            if folder is not None:
                res = launcher.launch_app(str(folder))
                if res.get("ok"):
                    res["action"] = "starting"
                return res
            if url:
                launcher.open_app_window(url)
                return {"ok": True, "action": "opened", "url": url}
            return {"ok": False, "error": "nothing to launch (set path or url)"}

        out.append({"id": str(row["id"]), "name": str(row.get("name") or row["id"]),
                    "desc": str(row.get("desc") or ""), "icon": str(row.get("icon") or "◆"),
                    "group": str(row.get("group") or "Custom"), "folder": folder, "port": port,
                    "fn": _open, "proc": None, "health": ""})
    return out


def registry() -> list[dict[str, Any]]:
    apps = _builtin()
    have = {a["id"] for a in apps}
    apps += [a for a in _custom() if a["id"] not in have]
    return apps


def get(app_id: str) -> dict[str, Any] | None:
    return next((a for a in registry() if a["id"] == app_id), None)


# ── running status ───────────────────────────────────────────────────────

def _port_open(port: int, timeout: float = 0.25) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def _process_table() -> list[tuple[int, str, str]]:
    """(pid, lowercase name, lowercase exe path) for every process we may read."""
    import psutil

    rows: list[tuple[int, str, str]] = []
    for p in psutil.process_iter(["pid", "name", "exe"]):
        try:
            rows.append((p.info["pid"], (p.info.get("name") or "").lower(),
                         (p.info.get("exe") or "").lower()))
        except Exception:
            continue
    return rows


def _pids_for(app: dict[str, Any], table: list[tuple[int, str, str]]) -> list[int]:
    pids: list[int] = []
    folder = app.get("folder")
    prefix = (str(Path(folder).resolve()).lower() + os.sep) if folder else None
    proc = (app.get("proc") or "").lower()
    for pid, name, exe in table:
        if (prefix and exe.startswith(prefix)) or (proc and name == proc):
            pids.append(pid)
    return pids


def status() -> list[dict[str, Any]]:
    """Registry + `running` for each app (the "apps" state section)."""
    apps = registry()
    ports = sorted({a["port"] for a in apps if a.get("port")})
    with ThreadPoolExecutor(max_workers=max(1, len(ports))) as ex:
        up = dict(zip(ports, ex.map(_port_open, ports)))
    try:
        table = _process_table()
    except Exception:
        table = []
    out: list[dict[str, Any]] = []
    for a in apps:
        by_port = up.get(a["port"]) if a.get("port") else None
        by_proc = bool(_pids_for(a, table)) if (a.get("folder") or a.get("proc")) else None
        running: bool | None
        if by_port is None and by_proc is None:
            running = None
        else:
            running = bool(by_port) or bool(by_proc)
        out.append({
            "id": a["id"], "name": a["name"], "desc": a["desc"], "icon": a["icon"],
            "group": a["group"], "port": a.get("port"),
            "path": str(a["folder"]) if a.get("folder") else None,
            "running": running,
            # JARVIS may stop what it can positively identify by folder; never
            # Hermes' sibling server by accident, and never itself.
            "can_stop": bool(a.get("folder")) and a["id"] not in ("hermes",),
        })
    return out


def open_app(app_id: str) -> dict[str, Any]:
    app = get(app_id)
    if app is None:
        return {"ok": False, "error": f"unknown app '{app_id}'"}
    try:
        res = app["fn"]()
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    res.setdefault("name", app["name"])
    return res


def stop_app(app_id: str) -> dict[str, Any]:
    """Stop an app by ending the processes that run out of its folder (and
    whatever listens on its port, if that process also belongs to the folder).
    Anything that can't be tied to the app's own folder is left alone."""
    import psutil

    app = get(app_id)
    if app is None:
        return {"ok": False, "error": f"unknown app '{app_id}'"}
    if not app.get("folder") or app_id == "hermes":
        return {"ok": False, "error": f"{app['name']} can't be stopped from here"}
    table = _process_table()
    pids = set(_pids_for(app, table))
    # Apps started with a system interpreter (`py -3.13 run_app.py`) don't run
    # an exe from their own folder — find them through their port instead, and
    # accept the listener only if its working directory / command line is the
    # app's folder (so an unrelated program that grabbed the port is safe).
    if app.get("port"):
        folder_l = str(Path(app["folder"]).resolve()).lower()
        try:
            for c in psutil.net_connections(kind="tcp"):
                if c.status == psutil.CONN_LISTEN and c.laddr and c.laddr.port == app["port"] and c.pid:
                    try:
                        proc = psutil.Process(c.pid)
                        cwd = (proc.cwd() or "").lower()
                        cmd = " ".join(proc.cmdline()).lower()
                    except Exception:
                        continue
                    if cwd.startswith(folder_l) or folder_l in cmd:
                        pids.add(c.pid)
        except Exception:
            pass
    me = os.getpid()
    victims: list[Any] = []
    for pid in pids:
        if pid == me:
            continue
        try:
            proc = psutil.Process(pid)
            victims.append(proc)
            victims.extend(proc.children(recursive=True))
        except Exception:
            continue
    seen: set[int] = set()
    stopped = 0
    for proc in victims:
        if proc.pid in seen or proc.pid == me:
            continue
        seen.add(proc.pid)
        try:
            proc.terminate()
            stopped += 1
        except Exception:
            continue
    if not stopped:
        return {"ok": False, "error": f"{app['name']} doesn't appear to be running"}
    psutil.wait_procs([p for p in victims if p.pid in seen], timeout=3)
    return {"ok": True, "stopped": stopped, "name": app["name"]}


def open_claude_terminal() -> dict[str, Any]:
    return launcher.launch_terminal("claude") if which("claude") else {
        "ok": False, "error": "claude CLI not found"}
