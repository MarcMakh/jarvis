"""FastAPI backend for the JARVIS HUD.

Conventions that keep the server responsive:
  * Anything that blocks (a subprocess, a model call, winget) lives in a plain
    `def` endpoint, which FastAPI runs on a worker thread. The old `async def`
    handlers called blocking code directly and froze the whole server — every
    poll, the voice, even /api/health — for as long as the brain was thinking
    or an update was installing.
  * The HUD reads state without waiting (state.py) and receives live telemetry,
    state-change notices and alerts over one server-sent-event stream.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import Body, FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from . import (
    __version__,
    actions,
    alerts,
    apps,
    brain,
    briefing,
    connectors,
    graph,
    launcher,
    rag,
    settings,
    shield,
    updater,
)
from .ears import Ears
from .obsidian import from_config as vault
from .state import store
from .telemetry import sampler
from .util import ROOT, is_under, load_config, load_env, project_roots, run, which
from .voice import Voice

STATIC = ROOT / "static"

# Pull any .env (e.g. ANTHROPIC_API_KEY) into the environment at import time,
# before the brain reads os.environ.
load_env()

_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}
_SSE_HEADERS = {"Cache-Control": "no-store", "X-Accel-Buffering": "no"}


def _local_request(request: Request) -> bool:
    """JARVIS launches programs and runs updates, so it only obeys *this
    machine's own pages*. Two checks close the gap that 'it only listens on
    localhost' leaves open:

      Host    must be a loopback name — defeats DNS rebinding, where a hostile
              site's domain is pointed at 127.0.0.1.
      Origin  when a browser sends one, it must be a loopback origin — so a web
              page you happen to visit can't fire requests at JARVIS behind
              your back. Server-to-server calls (Hermes, scripts) send none.
    """
    host = (request.headers.get("host") or "").rsplit(":", 1)[0].lower()
    if host not in _LOCAL_HOSTS:
        return False
    origin = request.headers.get("origin")
    if origin and origin != "null":
        try:
            if (urlsplit(origin).hostname or "").lower() not in _LOCAL_HOSTS:
                return False
        except ValueError:
            return False
    elif origin == "null" and request.method != "GET":
        return False
    if request.method != "GET" and request.headers.get("sec-fetch-site") == "cross-site":
        return False
    return True


def _apply_refresh(result: dict[str, Any]) -> None:
    """Honour an action's `refresh` hint (e.g. rescan apps after opening one)."""
    for name in result.get("refresh") or []:
        if name == "all!":
            store.refresh(None)
        elif name.endswith("!"):
            store.refresh([name[:-1]])
        elif name == "shield":
            store._shield_cache = None
        else:
            # give the app a moment to bind its port before looking again
            for delay in (1.5, 5.0, 12.0):
                _later(delay, name)


def _later(delay: float, section: str) -> None:
    import threading

    t = threading.Timer(delay, lambda: store.refresh([section]))
    t.daemon = True
    t.start()


def _sse(obj: dict[str, Any]) -> str:
    return f"data: {json.dumps(obj, separators=(',', ':'))}\n\n"


def _base_url() -> str:
    srv = load_config().get("server", {})
    port = int(os.environ.get("JARVIS_PORT") or srv.get("port", 7717))
    return f"http://{srv.get('host', '127.0.0.1')}:{port}"


def create_app() -> FastAPI:
    app = FastAPI(title="JARVIS", version=__version__)
    alerts.arm()   # reminders saved before a restart get their timers back

    @app.middleware("http")
    async def _guard(request: Request, call_next):
        if request.url.path.startswith("/api/") and not _local_request(request):
            return JSONResponse({"ok": False, "error": "forbidden: local requests only"},
                                status_code=403)
        resp = await call_next(request)
        # JARVIS is a single-user local dashboard; never let the browser serve a
        # stale index.html/jarvis.js (that hid the graph after upgrades).
        if request.url.path.startswith("/api/icon"):
            resp.headers["Cache-Control"] = "public, max-age=86400"  # icons rarely change
        elif "cache-control" not in resp.headers:
            resp.headers["Cache-Control"] = "no-store, max-age=0"
        return resp

    app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(str(STATIC / "index.html"))

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "app": "jarvis", "version": __version__}

    # ── state, telemetry, live stream ────────────────────────────────────

    @app.get("/api/state")
    def api_state(refresh: bool = False) -> JSONResponse:
        if refresh:
            store.refresh(None)
        return JSONResponse(store.snapshot())

    @app.post("/api/refresh")
    def api_refresh(payload: dict[str, Any] = Body(default={})) -> JSONResponse:
        names = payload.get("sections")
        brain.forget_probes()
        return JSONResponse({"ok": True, "refreshing": store.refresh(names if names else None)})

    @app.get("/api/telemetry")
    def api_telemetry() -> JSONResponse:
        return JSONResponse({"latest": sampler.snapshot(), "history": sampler.history(),
                             "procs": sampler.processes()})

    @app.get("/api/stream")
    async def api_stream(request: Request) -> StreamingResponse:
        """One long-lived stream per open HUD: a telemetry sample every second,
        a notice whenever a state section changes, and new alerts. It is also
        the heartbeat that keeps scans and the sampler running — when the last
        HUD closes, everything goes idle."""
        try:
            cursor = int(request.query_params.get("alerts") or alerts.last_id())
        except ValueError:
            cursor = alerts.last_id()

        async def gen():
            alerts.listeners += 1
            try:
                yield "retry: 2000\n\n"
                async for chunk in loop():
                    yield chunk
            finally:
                alerts.listeners -= 1

        async def loop():
            nonlocal cursor
            rev = -1
            tick = 0
            last_eval = 0.0
            last_procs: Any = None
            while True:
                if await request.is_disconnected():
                    break
                store.ensure()
                tel = sampler.snapshot()
                yield _sse({"t": "tel", "d": {k: tel.get(k) for k in (
                    "ts", "cpu", "cores", "cpu_count", "mem", "gpu", "disks", "net", "uptime_h")}})
                if tick % 5 == 0:
                    procs = sampler.processes()
                    if procs != last_procs:
                        last_procs = procs
                        yield _sse({"t": "procs", "d": procs})
                if store.rev != rev:
                    changed = store.changed_since(rev)
                    rev = store.rev
                    if changed:
                        yield _sse({"t": "state", "sections": changed, "rev": rev})
                now = time.time()
                if now - last_eval >= 4:
                    last_eval = now
                    try:
                        await run_in_threadpool(_evaluate_alerts)
                    except Exception:
                        pass
                    feed = alerts.snapshot(cursor)
                    if feed["events"] or tick == 0:
                        cursor = feed["last"]
                        yield _sse({"t": "alerts", "d": feed})
                tick += 1
                await asyncio.sleep(1.0)

        return StreamingResponse(gen(), media_type="text/event-stream", headers=_SSE_HEADERS)

    def _evaluate_alerts() -> None:
        cfg = load_config()
        alerts.evaluate(store.snapshot(ensure=False), sampler.snapshot(), brain.status(cfg))

    # ── updates ──────────────────────────────────────────────────────────

    @app.get("/api/updates")
    def api_updates(refresh: bool = False) -> JSONResponse:
        if refresh:
            store.refresh(["updates"])
        return JSONResponse(store.get("updates"))

    @app.post("/api/update")
    def api_update(payload: dict[str, Any] = Body(default={})) -> JSONResponse:
        result = updater.apply_update(str(payload.get("target", "")), payload.get("package"))
        store.refresh(["updates", "claude"])
        return JSONResponse(result)

    @app.post("/api/tools/upgrade")
    def api_tools_upgrade(payload: dict[str, Any] = Body(default={})) -> JSONResponse:
        result = updater.winget_upgrade(str(payload.get("id", "")))
        store.refresh(["updates", "runtimes", "dev_tools"])
        return JSONResponse(result)

    @app.get("/api/winget/all")
    def api_winget_all() -> JSONResponse:
        return JSONResponse(updater.winget_all())

    # ── projects, map, icons ─────────────────────────────────────────────

    @app.get("/api/project")
    def api_project(path: str = "") -> JSONResponse:
        return JSONResponse(connectors.project_detail(path))

    @app.get("/api/graph")
    def api_graph() -> JSONResponse:
        return JSONResponse(graph.build(store.snapshot()))

    @app.get("/api/icon")
    def api_icon(path: str = "") -> Response:
        """Icon PNG for a library shortcut, guarded to configured library roots."""
        from . import icons

        p = Path(path)
        if not path or not p.exists() or not is_under(p, launcher._library_roots()):
            return Response(status_code=404)
        data = icons.icon_png(path)
        if not data:
            return Response(status_code=404)
        return Response(content=data, media_type="image/png")

    # ── apps & launching ─────────────────────────────────────────────────

    @app.get("/api/apps")
    def api_apps() -> JSONResponse:
        return JSONResponse({"apps": store.get("apps") or apps.status()})

    def _open_app(app_id: str) -> JSONResponse:
        res = apps.open_app(app_id)
        _apply_refresh({"refresh": ["apps", "hermes_chat"] if app_id == "hermes" else ["apps"]})
        return JSONResponse(res)

    @app.post("/api/apps/{app_id}/open")
    def api_app_open(app_id: str) -> JSONResponse:
        return _open_app(app_id)

    @app.post("/api/apps/{app_id}/stop")
    def api_app_stop(app_id: str) -> JSONResponse:
        res = apps.stop_app(app_id)
        store.refresh(["apps"])
        _later(2.0, "apps")
        return JSONResponse(res)

    # The original per-app routes still work (the Hermes `jarvis` skill, old
    # bookmarks and the map's launch nodes use them).
    for _legacy in ("hermes", "gamecenter", "emucenter", "moviecenter", "quantflow", "quantfin",
                    "chess", "pantry"):
        app.add_api_route(f"/api/{_legacy}/open",
                          (lambda app_id=_legacy: _open_app(app_id)), methods=["POST"],
                          name=f"open_{_legacy}")

    @app.post("/api/launch")
    def api_launch(payload: dict[str, Any] = Body(default={})) -> JSONResponse:
        kind = payload.get("kind")
        if kind == "terminal":
            return JSONResponse(launcher.launch_terminal(str(payload.get("target", ""))))
        if kind == "app":
            res = launcher.launch_app(str(payload.get("path", "")))
            _apply_refresh({"refresh": ["apps"]})
            return JSONResponse(res)
        if kind == "file":
            return JSONResponse(launcher.launch_file(str(payload.get("path", ""))))
        if kind == "obsidian":
            return JSONResponse(launcher.launch_obsidian())
        return JSONResponse({"ok": False, "error": "unknown launch kind"})

    @app.post("/api/appwindow")
    def api_appwindow() -> JSONResponse:
        """Reopen JARVIS as the autoplay app window (self-heal from a regular tab)."""
        return JSONResponse({"ok": launcher.open_app_window(_base_url())})

    @app.get("/api/run-info")
    def api_run_info(path: str = "") -> JSONResponse:
        """Whether a project has a detectable runnable app (drives the Run button)."""
        plan = launcher.detect_run(Path(path)) if path else None
        return JSONResponse({"runnable": bool(plan), "label": plan["label"] if plan else None})

    def _open_with_os(path: str, must_be_file: bool) -> dict[str, Any]:
        p = Path(path)
        ok_kind = p.is_file() if must_be_file else p.is_dir()
        allowed = [*project_roots(), shield.backup_dir()]
        vault_dir = vault().vault
        if vault_dir:
            allowed.append(vault_dir)
        if not path or not ok_kind or not is_under(p, allowed):
            return {"ok": False, "error": "path not allowed"}
        try:
            if os.name == "nt":
                os.startfile(str(p))  # type: ignore[attr-defined]
            else:
                import subprocess

                subprocess.Popen(["xdg-open", str(p)])
            return {"ok": True}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @app.post("/api/open/doc")
    def api_open_doc(payload: dict[str, Any] = Body(default={})) -> JSONResponse:
        return JSONResponse(_open_with_os(str(payload.get("path", "")), must_be_file=True))

    @app.post("/api/open/folder")
    def api_open_folder(payload: dict[str, Any] = Body(default={})) -> JSONResponse:
        return JSONResponse(_open_with_os(str(payload.get("path", "")), must_be_file=False))

    @app.post("/api/open/code")
    def api_open_code(payload: dict[str, Any] = Body(default={})) -> JSONResponse:
        path = str(payload.get("path", ""))
        # Only open folders inside a project root.
        if not path or not Path(path).is_dir() or not is_under(path, project_roots()):
            return JSONResponse({"ok": False, "error": "path not allowed"})
        code = which("code")
        if not code:
            return JSONResponse({"ok": False, "error": "VS Code (code) not found"})
        run([code, path], timeout=15)
        return JSONResponse({"ok": True})

    # ── actions & command palette ────────────────────────────────────────

    @app.get("/api/commands")
    def api_commands() -> JSONResponse:
        return JSONResponse({"items": actions.catalog(store.snapshot(ensure=False))})

    @app.post("/api/act")
    def api_act(payload: dict[str, Any] = Body(default={})) -> JSONResponse:
        honorific = load_config().get("identity", {}).get("honorific", "sir")
        res = actions.execute(str(payload.get("name", "")), str(payload.get("arg", "")),
                              store.snapshot(ensure=False), honorific)
        _apply_refresh(res)
        return JSONResponse(res)

    # ── brain ────────────────────────────────────────────────────────────

    @app.post("/api/ask")
    def api_ask(payload: dict[str, Any] = Body(default={})) -> JSONResponse:
        question = str(payload.get("question", "")).strip()
        if not question:
            return JSONResponse({"ok": False, "answer": "No question, sir."})
        result = brain.ask(question, store.snapshot(ensure=False), load_config(),
                           payload.get("history"))
        for act in result.get("actions", []):
            _apply_refresh(act.get("result") or {})
        return JSONResponse(result)

    @app.post("/api/ask/stream")
    def api_ask_stream(payload: dict[str, Any] = Body(default={})) -> StreamingResponse:
        """Streamed answer (see brain.ask_stream for the event types)."""
        question = str(payload.get("question", "")).strip()
        history = payload.get("history") if isinstance(payload.get("history"), list) else None

        def gen():
            if not question:
                yield _sse({"t": "error", "error": "No question, sir."})
                return
            try:
                for ev in brain.ask_stream(question, history, store.snapshot(ensure=False),
                                           load_config()):
                    if ev["t"] == "action":
                        _apply_refresh(ev.get("result") or {})
                    yield _sse(ev)
            except Exception as exc:  # never leave the HUD hanging on a dead stream
                yield _sse({"t": "error", "error": f"{type(exc).__name__}: {exc}"})

        return StreamingResponse(gen(), media_type="text/event-stream", headers=_SSE_HEADERS)

    @app.get("/api/brain/status")
    def api_brain_status() -> JSONResponse:
        return JSONResponse(brain.status(load_config()))

    @app.post("/api/brain/test")
    def api_brain_test() -> JSONResponse:
        return JSONResponse(brain.ping(load_config()))

    @app.get("/api/brain/options")
    def api_brain_options() -> JSONResponse:
        """What the ⚙ panel offers as the brain: the engines, plus the models
        reachable through Hermes and on the local endpoint."""
        cfg = load_config()
        provs = brain._hermes_providers() or []
        return JSONResponse({
            "status": brain.status(cfg),
            "hermes": [{"id": p["id"], "label": p.get("label"), "kind": p.get("kind"),
                        "available": p.get("available"), "models": (p.get("models") or [])[:400],
                        "loaded": p.get("loaded") or [], "default_model": p.get("default_model")}
                       for p in provs],
            "local": brain._local_models(cfg) or [],
        })

    @app.get("/api/settings")
    def api_settings() -> JSONResponse:
        return JSONResponse(settings.get_settings())

    @app.post("/api/settings")
    def api_settings_save(payload: dict[str, Any] = Body(default={})) -> JSONResponse:
        out = settings.set_settings(payload)
        brain.forget_probes()
        return JSONResponse(out)

    # ── voice & ears ─────────────────────────────────────────────────────

    @app.post("/api/speak")
    async def api_speak(request: Request) -> Response:
        body = await request.json()
        text = str((body or {}).get("text", ""))[:3000]
        v = Voice(load_config())
        if not v.enabled:
            return Response(status_code=204)
        audio = await v.synthesize(text)
        if not audio:
            return Response(status_code=204)
        return Response(content=audio, media_type="audio/mpeg")

    @app.get("/api/voice")
    def api_voice() -> JSONResponse:
        return JSONResponse(Voice(load_config()).status())

    @app.post("/api/transcribe")
    async def api_transcribe(request: Request) -> JSONResponse:
        """Mic audio from the browser -> text via local Whisper (see ears.py)."""
        audio = await request.body()
        ears = Ears(load_config())
        return JSONResponse(await run_in_threadpool(ears.transcribe, audio))

    @app.get("/api/ears/status")
    def api_ears_status() -> JSONResponse:
        return JSONResponse(Ears(load_config()).status())

    @app.get("/api/voices")
    async def api_voices() -> JSONResponse:
        return JSONResponse(await Voice(load_config()).list_voices())

    # ── greeting & briefing ──────────────────────────────────────────────

    def _briefing(wait: float) -> dict[str, Any]:
        store.wait_fresh(["mcp", "claude", "hermes_chat", "projects", "automations", "apps"],
                         timeout=wait)
        cfg = load_config()
        return briefing.build(store.snapshot(ensure=False), brain.status(cfg))

    @app.get("/api/greeting")
    def api_greeting() -> dict[str, Any]:
        b = _briefing(wait=6.0)
        return {"text": b["speech"], "headline": b["headline"],
                "enabled": bool(settings.get_settings().get("greet_on_start", True))}

    @app.get("/api/briefing")
    def api_briefing(wait: float = 0.0) -> JSONResponse:
        return JSONResponse(_briefing(wait=min(max(wait, 0.0), 15.0)))

    # ── alerts & reminders ───────────────────────────────────────────────

    @app.get("/api/alerts")
    def api_alerts(since: int = 0) -> JSONResponse:
        return JSONResponse(alerts.snapshot(since))

    @app.post("/api/alerts/mute")
    def api_alerts_mute(payload: dict[str, Any] = Body(default={})) -> JSONResponse:
        key = str(payload.get("key", ""))
        if not key:
            return JSONResponse({"ok": False, "error": "no key"})
        settings.mute_alert(key, bool(payload.get("muted", True)))
        return JSONResponse({"ok": True, **alerts.snapshot(0)})

    @app.post("/api/alerts/clear")
    def api_alerts_clear() -> JSONResponse:
        alerts.clear_feed()
        return JSONResponse({"ok": True})

    @app.post("/api/reminders")
    def api_reminder_add(payload: dict[str, Any] = Body(default={})) -> JSONResponse:
        parsed = actions.parse_duration(str(payload.get("in", "")))
        if not parsed:
            return JSONResponse({"ok": False, "error": "give a duration such as 20m or 1h30m"})
        rem = alerts.add_reminder(time.time() + parsed[0],
                                  str(payload.get("text") or parsed[1] or "Time's up"))
        return JSONResponse({"ok": True, "reminder": rem})

    @app.delete("/api/reminders/{rem_id}")
    def api_reminder_cancel(rem_id: int) -> JSONResponse:
        return JSONResponse({"ok": alerts.cancel_reminder(rem_id)})

    # ── shield (project protection & snapshots) ──────────────────────────

    @app.get("/api/shield")
    def api_shield() -> JSONResponse:
        return JSONResponse(shield.assess(store.get("projects")))

    @app.post("/api/shield/snapshot")
    def api_shield_snapshot(payload: dict[str, Any] = Body(default={})) -> JSONResponse:
        store._shield_cache = None
        if payload.get("all"):
            return JSONResponse(shield.snapshot_at_risk(store.get("projects")))
        return JSONResponse(shield.snapshot(str(payload.get("path", ""))))

    @app.post("/api/shield/open")
    def api_shield_open() -> JSONResponse:
        d = shield.backup_dir()
        d.mkdir(parents=True, exist_ok=True)
        return JSONResponse(_open_with_os(str(d), must_be_file=False))

    # ── vault: Obsidian + local RAG ──────────────────────────────────────

    @app.get("/api/rag/status")
    def api_rag_status() -> JSONResponse:
        return JSONResponse(rag.status())

    @app.post("/api/rag/reindex")
    def api_rag_reindex(payload: dict[str, Any] = Body(default={})) -> JSONResponse:
        return JSONResponse(rag.start_reindex(force=bool(payload.get("force"))))

    @app.get("/api/rag/search")
    def api_rag_search(q: str = "", k: int = 0) -> JSONResponse:
        return JSONResponse({"query": q, "results": rag.search(q, k or None)})

    @app.post("/api/obsidian/snapshot")
    def api_snapshot() -> JSONResponse:
        store.wait_fresh(["runtimes", "dev_tools", "claude", "mcp", "hermes", "openclaw"], 30)
        res = vault().write_snapshot(_snapshot_markdown(store.snapshot(ensure=False)))
        store.refresh(["obsidian"])
        return JSONResponse(res)

    @app.get("/api/obsidian/search")
    def api_ob_search(q: str = "") -> JSONResponse:
        return JSONResponse(vault().search(q))

    @app.post("/api/vault/open")
    def api_vault_open(payload: dict[str, Any] = Body(default={})) -> JSONResponse:
        return JSONResponse(launcher.launch_obsidian(str(payload.get("rel", ""))))

    @app.post("/api/vault/capture")
    def api_vault_capture(payload: dict[str, Any] = Body(default={})) -> JSONResponse:
        res = vault().capture(str(payload.get("text", "")))
        if res.get("ok"):
            store.refresh(["obsidian"])
        return JSONResponse(res)

    @app.post("/api/obsidian/note")
    def api_ob_note(payload: dict[str, Any] = Body(default={})) -> JSONResponse:
        """Create a note in the vault (Hermes uses this to export a chat)."""
        md = str(payload.get("markdown", ""))
        if not md.strip():
            return JSONResponse({"ok": False, "error": "empty note"})
        tags = [str(t) for t in payload.get("tags", []) if str(t).replace("-", "").isalnum()][:8]
        res = vault().write_note(str(payload.get("folder", "")), str(payload.get("title", "")),
                                 md, tags)
        if res.get("ok"):
            store.refresh(["obsidian"])
        return JSONResponse(res)

    return app


def _snapshot_markdown(state: dict[str, Any]) -> str:
    inv = state["inventory"]
    lines = ["## Runtimes\n"]
    for r in inv["runtimes"]:
        if r.get("present"):
            lines.append(f"- **{r['label']}** — `{r['version']}`")
    lines.append("\n## Dev tools\n")
    for t in inv["dev_tools"]:
        mark = f"`{t['version']}`" if t.get("present") else "_not installed_"
        lines.append(f"- {t['label']} — {mark}")
    lines.append("\n## AI connectors\n")
    conn = state["connectors"]
    c = conn["claude"]
    signed = "signed in" if (c.get("auth") or {}).get("logged_in") else "CLI signed out"
    lines.append(f"- Claude Code — {c.get('status')} v{c.get('version')} ({signed})")
    for m in conn["mcp_servers"]:
        lines.append(f"  - MCP `{m['name']}` — {m['status']}")
    hc = conn.get("hermes_chat", {})
    lines.append(f"- Hermes Chat — {hc.get('status', 'offline')} ({hc.get('url', '')})")
    lines.append(f"- Hermes Skills — {conn.get('hermes', {}).get('status')}")
    lines.append(f"- OpenClaw — {conn.get('openclaw', {}).get('status')}")

    tel = inv.get("system") or {}
    if tel.get("ok"):
        lines.append("\n## Host\n")
        mem = tel.get("mem", {})
        lines.append(f"- Memory — {mem.get('used_gb')} / {mem.get('total_gb')} GB ({mem.get('pct')}%)")
        g = tel.get("gpu")
        if g:
            lines.append(f"- GPU — {g.get('name')}, {g.get('vram_total_gb')} GB VRAM")
        for d in tel.get("disks", []):
            lines.append(f"- Drive {d['mount']} — {d['pct']:.0f}% used, {d['free_gb']:.0f} GB free")

    sh = state.get("shield") or {}
    if sh.get("projects"):
        lines.append("\n## Project protection\n")
        for p in sh["projects"]:
            lines.append(f"- {p['name']} — {p['level']} ({p['why']})"
                         + ("" if p["covered"] else " ⚠ not covered"))

    autos = state.get("automations") or []
    if autos:
        lines.append("\n## Scheduled tasks\n")
        for t in autos:
            lines.append(f"- {t['name']} — {t['state']}"
                         + (" — last run FAILED" if t.get("failed") else ""))

    ups = state.get("updates") or {}
    if ups.get("checked"):
        total = sum(int((ups.get(k) or {}).get("count", 0)) for k in ("npm", "winget", "env"))
        lines.append(f"\n## Updates\n\n- {total} update(s) pending")
    return "\n".join(lines)


app = create_app()
