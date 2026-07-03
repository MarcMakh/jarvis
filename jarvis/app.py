"""FastAPI backend for the JARVIS HUD."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import __version__, brain, connectors, graph, inventory, launcher, rag, updater
from .obsidian import Obsidian
from .util import load_config, load_env
from .voice import Voice

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"

# Pull any .env (e.g. ANTHROPIC_API_KEY) into the environment at import time,
# before the brain reads os.environ.
load_env()


class StateStore:
    """Caches the expensive scans so the HUD can poll cheaply."""

    def __init__(self, ttl: float = 60.0):
        self.ttl = ttl
        self._cache: dict[str, Any] = {}
        self._ts: float = 0.0
        self._updates: dict[str, Any] = {}
        self._updates_ts: float = 0.0

    def get(self, refresh: bool = False) -> dict[str, Any]:
        now = time.time()
        if refresh or not self._cache or (now - self._ts) > self.ttl:
            self._rebuild()
        # System stats are always live (cheap) even on a cache hit.
        self._cache["inventory"]["system"] = inventory.system_stats()
        if self._updates:
            self._cache["updates"] = self._updates
        return self._cache

    def _rebuild(self) -> None:
        cfg = load_config()
        ob = Obsidian(
            cfg.get("obsidian", {}).get("vault", ""),
            cfg.get("obsidian", {}).get("log_folder", "JARVIS Logs"),
        )
        # The four scans are independent and subprocess/IO-bound — run them
        # concurrently so a cold load costs the slowest scan, not the sum.
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=4) as ex:
            f_inv = ex.submit(inventory.collect)
            f_conn = ex.submit(connectors.scan_connectors)
            f_proj = ex.submit(connectors.scan_projects)
            f_ob = ex.submit(ob.status)
            self._cache = {
                "version": __version__,
                "identity": cfg.get("identity", {"name": "JARVIS", "honorific": "sir"}),
                "generated": time.time(),
                "inventory": f_inv.result(),
                "connectors": f_conn.result(),
                "projects": f_proj.result(),
                "obsidian": f_ob.result(),
            }
        self._ts = time.time()

    def get_updates(self, refresh: bool = False) -> dict[str, Any]:
        now = time.time()
        if refresh or not self._updates or (now - self._updates_ts) > 300:
            self._updates = updater.check_updates()
            self._updates_ts = now
        return self._updates


store = StateStore()


def create_app() -> FastAPI:
    app = FastAPI(title="JARVIS", version=__version__)

    @app.middleware("http")
    async def _no_store(request, call_next):
        # JARVIS is a single-user local dashboard; never let the browser serve a
        # stale index.html/jarvis.js (that hid the graph after upgrades).
        resp = await call_next(request)
        if request.url.path.startswith("/api/icon"):
            resp.headers["Cache-Control"] = "public, max-age=86400"  # icons rarely change
        else:
            resp.headers["Cache-Control"] = "no-store, max-age=0"
        return resp

    app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(str(STATIC / "index.html"))

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "version": __version__}

    @app.get("/api/state")
    def api_state(refresh: bool = False) -> JSONResponse:
        return JSONResponse(store.get(refresh=refresh))

    @app.get("/api/updates")
    def api_updates(refresh: bool = False) -> JSONResponse:
        return JSONResponse(store.get_updates(refresh=refresh))

    @app.post("/api/update")
    async def api_update(request: Request) -> JSONResponse:
        body = await request.json()
        target = (body or {}).get("target", "")
        pkg = (body or {}).get("package")
        if pkg:
            result = updater.upgrade_package(pkg)
        else:
            result = updater.apply_update(target)
        store._updates_ts = 0.0  # force updates re-check next poll
        return JSONResponse(result)

    @app.post("/api/tools/upgrade")
    async def api_tools_upgrade(request: Request) -> JSONResponse:
        body = await request.json()
        pkg_id = (body or {}).get("id", "")
        result = updater.winget_upgrade(pkg_id)
        store._updates_ts = 0.0
        return JSONResponse(result)

    @app.get("/api/winget/all")
    def api_winget_all() -> JSONResponse:
        return JSONResponse(updater.winget_all())

    @app.get("/api/project")
    def api_project(path: str = "") -> JSONResponse:
        return JSONResponse(connectors.project_detail(path))

    @app.get("/api/graph")
    def api_graph() -> JSONResponse:
        return JSONResponse(graph.build(store.get()))

    @app.get("/api/icon")
    def api_icon(path: str = "") -> Response:
        """Icon PNG for a library shortcut, guarded to configured library roots."""
        from pathlib import Path

        from . import icons, launcher

        p = Path(path)
        roots = launcher._library_roots()
        rp = str(p.resolve()).lower()
        if not p.exists() or not any(rp.startswith(str(r.resolve()).lower()) for r in roots):
            return Response(status_code=404)
        data = icons.icon_png(path)
        if not data:
            return Response(status_code=404)
        return Response(content=data, media_type="image/png")

    @app.post("/api/launch")
    async def api_launch(request: Request) -> JSONResponse:
        body = await request.json() or {}
        kind = body.get("kind")
        if kind == "terminal":
            return JSONResponse(launcher.launch_terminal(body.get("target", "")))
        if kind == "app":
            return JSONResponse(launcher.launch_app(body.get("path", "")))
        if kind == "file":
            return JSONResponse(launcher.launch_file(body.get("path", "")))
        if kind == "obsidian":
            return JSONResponse(launcher.launch_obsidian())
        return JSONResponse({"ok": False, "error": "unknown launch kind"})

    @app.post("/api/hermes/open")
    def api_hermes_open() -> JSONResponse:
        return JSONResponse(launcher.launch_hermes())

    @app.get("/api/run-info")
    def api_run_info(path: str = "") -> JSONResponse:
        """Whether a project has a detectable runnable app (drives the Run button)."""
        from pathlib import Path

        plan = launcher.detect_run(Path(path)) if path else None
        return JSONResponse({"runnable": bool(plan), "label": plan["label"] if plan else None})

    def _open_with_os(path: str, must_be_file: bool) -> dict[str, Any]:
        import os
        from pathlib import Path

        from .launcher import _under_roots

        p = Path(path)
        ok_kind = p.is_file() if must_be_file else p.is_dir()
        if not ok_kind or not _under_roots(p):
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
    async def api_open_doc(request: Request) -> JSONResponse:
        body = await request.json() or {}
        return JSONResponse(_open_with_os(body.get("path", ""), must_be_file=True))

    @app.post("/api/open/folder")
    async def api_open_folder(request: Request) -> JSONResponse:
        body = await request.json() or {}
        return JSONResponse(_open_with_os(body.get("path", ""), must_be_file=False))

    @app.post("/api/open/code")
    async def api_open_code(request: Request) -> JSONResponse:
        from .connectors import project_detail
        from .util import run, which

        body = await request.json()
        path = (body or {}).get("path", "")
        # Reuse the project-root guard: only open validated project paths.
        if not project_detail(path).get("ok"):
            return JSONResponse({"ok": False, "error": "path not allowed"})
        code = which("code")
        if not code:
            return JSONResponse({"ok": False, "error": "VS Code (code) not found"})
        run([code, path], timeout=15)
        return JSONResponse({"ok": True})

    @app.post("/api/ask")
    async def api_ask(request: Request) -> JSONResponse:
        body = await request.json()
        question = (body or {}).get("question", "").strip()
        if not question:
            return JSONResponse({"ok": False, "answer": "No question, sir."})
        cfg = load_config()
        state = store.get()
        try:
            state["updates"] = store._updates
        except Exception:
            pass
        result = brain.ask(question, state, cfg)
        return JSONResponse(result)

    @app.post("/api/speak")
    async def api_speak(request: Request) -> Response:
        body = await request.json()
        text = (body or {}).get("text", "")
        cfg = load_config()
        v = Voice(cfg)
        if not v.enabled:
            return Response(status_code=204)
        audio = await v.synthesize(text)
        if not audio:
            return Response(status_code=204)
        return Response(content=audio, media_type="audio/mpeg")

    @app.get("/api/greeting")
    def api_greeting() -> dict[str, Any]:
        cfg = load_config()
        ident = cfg.get("identity", {})
        honorific = ident.get("honorific", "sir")
        state = store.get()
        conn = state["connectors"]
        mcp_online = conn["claude"]["detail"].get("mcp_online", 0)
        mcp_total = conn["claude"]["detail"].get("mcp_count", 0)
        ob = "linked" if state["obsidian"]["available"] else "not linked"
        hour = time.localtime().tm_hour
        part = "morning" if hour < 12 else "afternoon" if hour < 18 else "evening"
        text = (
            f"Good {part}, {honorific}. JARVIS online. "
            f"All core systems nominal. {mcp_online} of {mcp_total} MCP servers connected. "
            f"Obsidian {ob}. Standing by."
        )
        return {"text": text, "enabled": cfg.get("voice", {}).get("greet_on_start", True)}

    @app.get("/api/rag/status")
    def api_rag_status() -> JSONResponse:
        return JSONResponse(rag.status())

    @app.post("/api/rag/reindex")
    async def api_rag_reindex(request: Request) -> JSONResponse:
        body = {}
        try:
            body = await request.json() or {}
        except Exception:
            pass
        return JSONResponse(rag.start_reindex(force=bool(body.get("force"))))

    @app.get("/api/rag/search")
    def api_rag_search(q: str = "", k: int = 0) -> JSONResponse:
        return JSONResponse({"query": q, "results": rag.search(q, k or None)})

    @app.get("/api/brain/status")
    def api_brain_status() -> JSONResponse:
        return JSONResponse(brain.status(load_config()))

    @app.post("/api/brain/test")
    def api_brain_test() -> JSONResponse:
        return JSONResponse(brain.ping(load_config()))

    @app.get("/api/voices")
    async def api_voices() -> JSONResponse:
        return JSONResponse(await Voice(load_config()).list_voices())

    @app.post("/api/obsidian/snapshot")
    def api_snapshot() -> JSONResponse:
        cfg = load_config()
        ob = Obsidian(
            cfg.get("obsidian", {}).get("vault", ""),
            cfg.get("obsidian", {}).get("log_folder", "JARVIS Logs"),
        )
        state = store.get(refresh=True)
        md = _snapshot_markdown(state, store.get_updates())
        return JSONResponse(ob.write_snapshot(md))

    @app.get("/api/obsidian/search")
    def api_ob_search(q: str = "") -> JSONResponse:
        cfg = load_config()
        ob = Obsidian(cfg.get("obsidian", {}).get("vault", ""))
        return JSONResponse(ob.search(q))

    return app


def _snapshot_markdown(state: dict[str, Any], updates: dict[str, Any]) -> str:
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
    lines.append(f"- Claude Code — {conn['claude'].get('status')} v{conn['claude'].get('version')}")
    for m in conn["mcp_servers"]:
        lines.append(f"  - MCP `{m['name']}` — {m['status']}")
    hc = conn.get("hermes_chat", {})
    lines.append(f"- Hermes Chat — {hc.get('status', 'offline')} ({hc.get('url', '')})")
    lines.append(f"- Hermes Skills — {conn.get('hermes', {}).get('status')}")
    lines.append(f"- OpenClaw — {conn.get('openclaw', {}).get('status')}")
    total = sum(updates.get(k, {}).get("count", 0) for k in updates) if updates else 0
    lines.append(f"\n## Updates\n\n- {total} package update(s) pending")
    return "\n".join(lines)


app = create_app()
