"""HUD state store: independent sections, each cached on its own schedule.

The old store rebuilt *everything* — ~25 subprocesses including `claude mcp
list`, which spawns every MCP server — whenever a single 60-second timer ran
out, and made the request wait for it. Concurrent requests each started their
own rebuild.

Now every section (runtimes, MCP health, git status, running apps…) has its
own time-to-live and is refreshed in the background:

  * a read never blocks: it returns what is known now, plus `meta` saying how
    old each section is and whether it is refreshing;
  * a stale section is rescanned once (single-flight), off the request path;
  * nothing is scanned unless a HUD is actually asking (`ensure()` is driven by
    requests and the live stream), so an idle auto-started JARVIS sits at zero;
  * the last known state is kept on disk, so the HUD paints instantly after a
    restart instead of showing dashes until the first scan completes.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from . import __version__, apps, connectors, inventory, shield, tasks, updater
from .obsidian import from_config as vault
from .util import config_error, data_dir, load_config, read_json, write_json_atomic

_PERSIST = ("runtimes", "dev_tools", "claude", "mcp", "hermes", "openclaw", "projects",
            "obsidian", "automations", "updates")


class _Section:
    __slots__ = ("name", "fn", "ttl", "data", "ts", "refreshing", "error", "rev", "took",
                 "retry_at")

    def __init__(self, name: str, fn: Callable[[], Any], ttl: float, default: Any):
        self.name, self.fn, self.ttl = name, fn, ttl
        self.data: Any = default
        self.ts: float | None = None     # None = never scanned
        self.refreshing = False
        self.error: str | None = None
        self.rev = 0
        self.took = 0.0
        self.retry_at = 0.0              # back-off after a failed scan


def _obsidian_status() -> dict[str, Any]:
    return vault().status()


class Store:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._pool = ThreadPoolExecutor(max_workers=6, thread_name_prefix="scan")
        self._rev = 0
        self._started = time.time()
        self._save_timer: threading.Timer | None = None
        self._shield_cache: tuple[float, int, dict[str, Any]] | None = None
        off = {"present": False, "status": "not detected", "version": None, "path": None}
        self._s: dict[str, _Section] = {s.name: s for s in (
            _Section("runtimes", inventory.scan_runtimes, 1800, []),
            _Section("dev_tools", inventory.scan_dev_tools, 1800, []),
            _Section("claude", connectors.claude_code, 900,
                     {**off, "name": "Claude Code", "auth": {"installed": False, "logged_in": False}}),
            _Section("mcp", connectors.mcp_servers, 600, []),
            _Section("hermes_chat", connectors.hermes_chat, 20,
                     {"name": "Hermes Chat", "status": "offline", "providers": [], "url": "",
                      "present": False}),
            _Section("hermes", connectors.hermes_skills, 600,
                     {**off, "name": "Hermes Skills", "skills": [], "branch": None}),
            _Section("openclaw", connectors.openclaw, 3600, {**off, "name": "OpenClaw"}),
            _Section("projects", connectors.scan_projects, 120, []),
            _Section("obsidian", _obsidian_status, 120,
                     {"available": False, "vault": None, "notes": 0, "recent": []}),
            _Section("apps", apps.status, 10, []),
            _Section("automations", tasks.scan, 600, []),
            _Section("updates", updater.check_updates, 6 * 3600, {}),
        )}
        self._load_disk()

    # ── persistence ──────────────────────────────────────────────────────
    def _cache_path(self):
        return data_dir() / "state_cache.json"

    def _load_disk(self) -> None:
        saved = read_json(self._cache_path(), {})
        if not isinstance(saved, dict):
            return
        for name in _PERSIST:
            row = saved.get(name)
            sec = self._s.get(name)
            if sec is None or not isinstance(row, dict) or "data" not in row:
                continue
            if type(row["data"]) is not type(sec.data):
                continue
            sec.data, sec.ts = row["data"], float(row.get("ts") or 0) or None

    def _save_soon(self) -> None:
        with self._lock:
            if self._save_timer is not None and self._save_timer.is_alive():
                return
            self._save_timer = threading.Timer(5.0, self._save_now)
            self._save_timer.daemon = True
            self._save_timer.start()

    def _save_now(self) -> None:
        try:
            with self._lock:
                out = {n: {"ts": self._s[n].ts, "data": self._s[n].data}
                       for n in _PERSIST if self._s[n].ts}
            write_json_atomic(self._cache_path(), out)
        except Exception:
            pass

    # ── refreshing ───────────────────────────────────────────────────────
    def _auto_updates(self) -> bool:
        return bool((load_config().get("updates", {}) or {}).get("auto_check", True))

    def _stale(self, sec: _Section, now: float) -> bool:
        if sec.refreshing or now < sec.retry_at:
            return False
        if sec.name == "updates":
            # The winget scan is heavy: never on the first paint, and only on
            # its own (long) schedule unless asked for explicitly.
            if not self._auto_updates() or now - self._started < 25:
                return False
        return sec.ts is None or (now - sec.ts) > sec.ttl

    def ensure(self) -> None:
        """Schedule a background rescan of every stale section. Cheap."""
        now = time.time()
        for sec in self._s.values():
            if self._stale(sec, now):
                self._kick(sec)

    def refresh(self, names: Iterable[str] | None = None) -> list[str]:
        """Force a rescan now (the ⟳ button, or after an action changed things)."""
        wanted = list(self._s) if names is None else [n for n in names if n in self._s]
        kicked = []
        for n in wanted:
            if self._kick(self._s[n]):
                kicked.append(n)
        return kicked

    def _kick(self, sec: _Section) -> bool:
        with self._lock:
            if sec.refreshing:
                return False
            sec.refreshing = True
        self._pool.submit(self._run, sec)
        return True

    def _run(self, sec: _Section) -> None:
        t0 = time.time()
        try:
            data = sec.fn()
            err = None
        except Exception as exc:
            data, err = None, f"{type(exc).__name__}: {exc}"
        with self._lock:
            now = time.time()
            changed = True
            if err is None:
                # The revision only moves when the data really changed, so the
                # HUD refetches on news — not on every identical rescan.
                changed = sec.ts is None or sec.error is not None or data != sec.data
                sec.data, sec.ts, sec.error, sec.retry_at = data, now, None, 0.0
            else:
                # keep the old data (and its age); retry in 30 s, not every tick
                changed = sec.error != err
                sec.error, sec.retry_at = err, now + 30
            sec.took = round(now - t0, 2)
            sec.refreshing = False
            if changed:
                self._rev += 1
                sec.rev = self._rev
        if sec.name in _PERSIST and err is None:
            self._save_soon()

    # ── reading ──────────────────────────────────────────────────────────
    @property
    def rev(self) -> int:
        return self._rev

    def changed_since(self, rev: int) -> list[str]:
        with self._lock:
            return [s.name for s in self._s.values() if s.rev > rev]

    def get(self, name: str) -> Any:
        return self._s[name].data

    def _shield(self, projects: list[dict[str, Any]], prev_rev: int) -> dict[str, Any]:
        now = time.time()
        hit = self._shield_cache
        if hit and hit[1] == prev_rev and now - hit[0] < 8 and not hit[2].get("busy"):
            return hit[2]
        try:
            report = shield.assess(projects)
        except Exception as exc:
            report = {"dir": "", "projects": [], "counts": {}, "at_risk": 0, "jobs": {},
                      "busy": False, "error": str(exc)}
        self._shield_cache = (now, prev_rev, report)
        return report

    def snapshot(self, ensure: bool = True) -> dict[str, Any]:
        """The whole state as the HUD (and the brain) read it. Never blocks."""
        if ensure:
            self.ensure()
        cfg = load_config()
        now = time.time()
        with self._lock:
            d = {n: s.data for n, s in self._s.items()}
            meta = {n: {"ts": s.ts, "age": round(now - s.ts, 1) if s.ts else None,
                        "refreshing": s.refreshing, "ttl": s.ttl, "error": s.error,
                        "took": s.took}
                    for n, s in self._s.items()}
            proj_rev = self._s["projects"].rev
            rev = self._rev
        mcp = d["mcp"]
        claude = dict(d["claude"])
        claude["detail"] = {"mcp_count": len(mcp),
                            "mcp_online": sum(1 for m in mcp if m.get("status") == "online")}
        return {
            "version": __version__,
            "identity": cfg.get("identity", {"name": "JARVIS", "honorific": "sir"}),
            "generated": now,
            "rev": rev,
            "inventory": {"runtimes": d["runtimes"], "dev_tools": d["dev_tools"],
                          "system": inventory.system_stats()},
            "connectors": {"claude": claude, "mcp_servers": mcp, "hermes_chat": d["hermes_chat"],
                           "hermes": d["hermes"], "openclaw": d["openclaw"]},
            "projects": d["projects"],
            "obsidian": d["obsidian"],
            "apps": d["apps"],
            "automations": d["automations"],
            "updates": d["updates"],
            "shield": self._shield(d["projects"], proj_rev),
            "meta": meta,
            "config_error": config_error(),
        }

    def wait_fresh(self, names: Iterable[str], timeout: float = 20.0) -> None:
        """Block (up to `timeout`) until the named sections are loaded and not
        mid-rescan. For callers that speak or write about the state — the
        greeting, the snapshot note — never for painting the HUD."""
        wanted = [self._s[n] for n in names if n in self._s]
        now = time.time()
        for sec in wanted:
            if sec.ts is None or (now - sec.ts) > sec.ttl:
                self._kick(sec)
        end = time.time() + timeout
        while time.time() < end:
            if all(sec.ts is not None and not sec.refreshing for sec in wanted):
                return
            time.sleep(0.1)


store = Store()
