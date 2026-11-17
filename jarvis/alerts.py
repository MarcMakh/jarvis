"""Alerts: JARVIS's watchdog, notification feed and reminders.

The old HUD only showed what you went looking for. This module turns state
into *events*: a condition that starts being true raises an alert once, and
clears itself with a "resolved" entry when it stops — a failing MCP server, a
disk running out, a scheduled task whose last run failed, the brain losing
its engine, work that exists nowhere but this disk.

Everything is evaluated only while a HUD is connected (the stream loop calls
`evaluate`), so an idle, auto-started JARVIS does no extra work.

Levels: crit · warn · info · ok (a resolution) · reminder.
Each condition has a stable `key`; muting a key (⚙ or the bell panel) hides it
for good — a games drive that is *meant* to be full shouldn't nag forever.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from collections import deque
from typing import Any
from urllib.request import urlopen

from . import settings
from .util import data_dir, no_window, read_json, which, write_json_atomic

_lock = threading.RLock()
_feed: deque[dict[str, Any]] = deque(maxlen=200)
_active: dict[str, dict[str, Any]] = {}
_reminders: list[dict[str, Any]] = []
_streak: dict[str, int] = {}
_last_id = 0          # highest id ever issued (survives feed clears and restarts)
_loaded = False
_dirty = False
_last_save = 0.0
_prev_running: dict[str, bool] | None = None
_hermes_since = int(time.time() * 1000)
_hermes_polled = 0.0
_shield_seen = time.time()
_timers: dict[int, threading.Timer] = {}
listeners = 0          # open HUD streams; app.py keeps this current


def _path():
    return data_dir() / "alerts.json"


def _load() -> None:
    global _loaded, _last_id
    if _loaded:
        return
    with _lock:
        if _loaded:
            return
        d = read_json(_path(), {})
        if isinstance(d, dict):
            for ev in d.get("feed", [])[-200:]:
                if isinstance(ev, dict) and "id" in ev:
                    _feed.append(ev)
            for k, a in (d.get("active") or {}).items():
                if isinstance(a, dict):
                    _active[k] = a
            _reminders.extend(r for r in d.get("reminders", []) if isinstance(r, dict))
            _last_id = max([int(d.get("seq", 0))] + [int(e.get("id", 0)) for e in _feed]
                           + [int(r.get("id", 0)) for r in _reminders])
        _loaded = True


def _save(force: bool = False) -> None:
    global _dirty, _last_save
    if not _dirty and not force:
        return
    if not force and time.time() - _last_save < 4:
        return
    with _lock:
        write_json_atomic(_path(), {"seq": _last_id, "feed": list(_feed), "active": _active,
                                    "reminders": _reminders})
        _dirty = False
        _last_save = time.time()


def _next_id() -> int:
    global _last_id
    _last_id += 1
    return _last_id


def _emit(level: str, title: str, detail: str = "", key: str | None = None,
          act: dict[str, Any] | None = None, speak: str | None = None) -> dict[str, Any]:
    global _dirty
    ev = {"id": _next_id(), "ts": time.time(), "level": level, "title": title,
          "detail": detail, "key": key, "act": act, "speak": speak}
    _feed.append(ev)
    _dirty = True
    return ev


def push(level: str, title: str, detail: str = "", key: str | None = None,
         act: dict[str, Any] | None = None, speak: str | None = None) -> dict[str, Any]:
    """Record a one-off event (job finished, snapshot saved, app stopped…)."""
    _load()
    with _lock:
        ev = _emit(level, title, detail, key, act, speak)
    _save()
    return ev


# ── reminders ────────────────────────────────────────────────────────────

_TOAST_PS = r"""
$ErrorActionPreference = 'Stop'
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$xml = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$nodes = $xml.GetElementsByTagName('text')
$nodes.Item(0).AppendChild($xml.CreateTextNode($env:JARVIS_TOAST_TITLE)) | Out-Null
$nodes.Item(1).AppendChild($xml.CreateTextNode($env:JARVIS_TOAST_TEXT)) | Out-Null
$app = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($app).Show([Windows.UI.Notifications.ToastNotification]::new($xml))
"""


def toast(title: str, text: str) -> bool:
    """A Windows notification — used when a reminder comes due and no HUD is
    open to show or speak it. The text travels in environment variables, never
    inside the script, so it can't be interpreted as PowerShell."""
    if sys.platform != "win32":
        return False
    ps = which("powershell")
    if not ps:
        return False
    env = dict(os.environ, JARVIS_TOAST_TITLE=title[:120], JARVIS_TOAST_TEXT=text[:300])
    try:
        subprocess.Popen([ps, "-NoProfile", "-NonInteractive", "-Command", _TOAST_PS], env=env,
                         creationflags=no_window(), stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except OSError:
        return False


def _fire_due() -> None:
    """Turn every reminder that has come due into a feed event (and a desktop
    notification if nobody is looking at the HUD)."""
    global _dirty
    now = time.time()
    with _lock:
        due = [r for r in _reminders if r["due"] <= now + 0.25]
        if not due:
            return
        _reminders[:] = [r for r in _reminders if r["due"] > now + 0.25]
        for r in due:
            _timers.pop(r["id"], None)
            _emit("reminder", "Reminder", r["text"], speak=f"Reminder: {r['text']}")
        _dirty = True
        nobody = listeners <= 0
    if nobody:
        for r in due:
            toast("JARVIS reminder", r["text"])
    _save(force=True)


def _schedule(rem: dict[str, Any]) -> None:
    old = _timers.pop(rem["id"], None)
    if old is not None:
        old.cancel()
    t = threading.Timer(max(0.2, rem["due"] - time.time()), _fire_due)
    t.daemon = True
    _timers[rem["id"]] = t
    t.start()


def arm() -> None:
    """Load saved reminders and (re)start their timers — called at startup, so
    a reminder set before a restart still fires on time."""
    _load()
    with _lock:
        for rem in _reminders:
            _schedule(rem)


def add_reminder(due: float, text: str) -> dict[str, Any]:
    global _dirty
    _load()
    with _lock:
        rem = {"id": _next_id(), "due": float(due), "text": text.strip() or "Time's up",
               "created": time.time()}
        _reminders.append(rem)
        _reminders.sort(key=lambda r: r["due"])
        _dirty = True
        _schedule(rem)
    _save(force=True)
    return rem


def cancel_reminder(rem_id: int) -> bool:
    global _dirty
    _load()
    with _lock:
        before = len(_reminders)
        _reminders[:] = [r for r in _reminders if r["id"] != rem_id]
        changed = len(_reminders) != before
        timer = _timers.pop(rem_id, None)
        if timer is not None:
            timer.cancel()
        _dirty = _dirty or changed
    if changed:
        _save(force=True)
    return changed


def reminders() -> list[dict[str, Any]]:
    _load()
    with _lock:
        return [dict(r) for r in _reminders]


# ── conditions ───────────────────────────────────────────────────────────

def _sustained(key: str, on: bool, need: int) -> bool:
    """True once `on` has held for `need` consecutive evaluations."""
    _streak[key] = _streak.get(key, 0) + 1 if on else 0
    return _streak[key] >= need


def _conditions(state: dict[str, Any], tel: dict[str, Any],
                brain: dict[str, Any] | None) -> tuple[dict[str, dict[str, Any]], set[str]]:
    """Currently-true conditions, plus the key prefixes we could actually
    judge this round (a section that hasn't loaded yet must not "resolve"
    alerts it simply couldn't see)."""
    conds: dict[str, dict[str, Any]] = {}
    judged: set[str] = set()
    meta = state.get("meta", {})

    def loaded(section: str) -> bool:
        return bool((meta.get(section) or {}).get("ts"))

    def add(key: str, level: str, title: str, detail: str = "",
            act: dict[str, Any] | None = None, speak: str | None = None) -> None:
        conds[key] = {"level": level, "title": title, "detail": detail, "act": act,
                      "speak": speak}

    # host --------------------------------------------------------------
    if tel.get("ok"):
        judged.update(("disk:", "mem", "gpu"))
        for d in tel.get("disks", []):
            pct, free, mount = d["pct"], d["free_gb"], d["mount"]
            if d.get("system") and (pct >= 95 or free < 10):
                add(f"disk:{mount}", "crit", f"System drive {mount} is nearly full",
                    f"{pct:.0f}% used · {free:.0f} GB free",
                    speak=f"The system drive is nearly full: {free:.0f} gigabytes left.")
            elif pct >= 98 or free < 3:
                add(f"disk:{mount}", "warn", f"Drive {mount} is full",
                    f"{pct:.1f}% used · {free:.1f} GB free")
        mem = tel.get("mem", {})
        if _sustained("mem", mem.get("pct", 0) >= 93, 10):
            add("mem", "warn", "Memory is nearly exhausted",
                f"{mem.get('pct', 0):.0f}% of {mem.get('total_gb', 0):.0f} GB in use")
        gpu = tel.get("gpu") or {}
        if _sustained("gpu", gpu.get("temp", 0) >= 87, 10):
            add("gpu", "warn", "GPU is running hot", f"{gpu.get('temp')}°C at {gpu.get('util')}% load")

    # AI stack ----------------------------------------------------------
    conn = state.get("connectors", {})
    if loaded("mcp"):
        judged.add("mcp:")
        for m in conn.get("mcp_servers", []):
            if m.get("status") == "error":
                add(f"mcp:{m['name']}", "warn", f"MCP server “{m['name']}” is failing",
                    "Claude Code can't reach it — check its command or token.",
                    act={"label": "details", "kind": "mcp", "name": m["name"]})
    if brain is not None:
        judged.add("brain")
        engines = brain.get("engines", [])
        if not brain.get("ready"):
            add("brain", "crit", "No brain engine is available",
                "; ".join(f"{e['label']}: {e['reason']}" for e in engines if e.get("reason"))[:300],
                speak="I have no reasoning engine available at the moment.")
        else:
            first = engines[0] if engines else None
            if first and not first.get("ready") and first["id"] != brain.get("engine"):
                add("brain", "info", f"Brain is running on {brain.get('label', 'a fallback')}",
                    f"{first['label']} unavailable — {first.get('reason', '')}".strip(" —"),
                    act={"label": "brain settings", "ui": "settings"})

    # automations -------------------------------------------------------
    if loaded("automations"):
        judged.add("task:")
        for t in state.get("automations", []):
            if t.get("failed"):
                add(f"task:{t['name']}", "warn", f"Scheduled task “{t['name']}” failed",
                    f"last run {str(t.get('last_run') or '?').replace('T', ' ')} · "
                    f"result code {t.get('last_result')}",
                    act={"label": "systems", "ui": "view", "view": "systems"})

    # updates -----------------------------------------------------------
    ups = state.get("updates") or {}
    if ups.get("checked"):
        judged.add("updates")
        total = sum(int((ups.get(k) or {}).get("count", 0)) for k in ("npm", "winget", "env"))
        if total:
            add("updates", "info", f"{total} update{'s' if total != 1 else ''} available",
                "Dev toolchain and global packages.",
                act={"label": "review", "ui": "view", "view": "systems"})

    # shield ------------------------------------------------------------
    sh = state.get("shield") or {}
    if loaded("projects") and sh.get("projects"):
        judged.add("shield")
        risky = [p for p in sh["projects"] if not p["covered"] and p["level"] in ("exposed", "local")]
        if risky:
            names = ", ".join(p["name"] for p in risky[:5]) + ("…" if len(risky) > 5 else "")
            add("shield", "info",
                f"{len(risky)} project{'s' if len(risky) != 1 else ''} exist only on this disk",
                f"No remote and no recent snapshot: {names}",
                act={"label": "protect", "ui": "view", "view": "projects", "filter": "risk"})

    cfg_err = state.get("config_error")
    judged.add("config")
    if cfg_err:
        add("config", "warn", "config.toml has an error", str(cfg_err)[:240])
    return conds, judged


def _poll_hermes(state: dict[str, Any]) -> None:
    """Surface finished Hermes jobs in the feed (JARVIS is where you look)."""
    global _hermes_since, _hermes_polled
    hc = (state.get("connectors", {}) or {}).get("hermes_chat", {}) or {}
    if hc.get("status") != "online" or time.time() - _hermes_polled < 30:
        return
    _hermes_polled = time.time()
    try:
        with urlopen(f"{hc['url']}/api/jobs/events?since={_hermes_since}", timeout=2) as r:
            events = json.loads(r.read().decode("utf-8")).get("events", [])
    except Exception:
        return
    for ev in events:
        _hermes_since = max(_hermes_since, int(ev.get("ts", 0)))
        ok = ev.get("status") == "done"
        cost = f" · ${ev['cost']:.4f}" if ev.get("cost") else ""
        _emit("info" if ok else "warn",
              f"Hermes job “{ev.get('name', '?')}” {'finished' if ok else 'failed'}",
              f"{ev.get('ms', 0) / 1000:.1f}s{cost}",
              act={"label": "open Hermes", "act": "app-open", "id": "hermes"})


def evaluate(state: dict[str, Any], tel: dict[str, Any],
             brain: dict[str, Any] | None = None) -> None:
    """Diff current conditions against the active set; raise and resolve."""
    global _prev_running, _shield_seen, _dirty
    _load()
    muted = set(settings.get_settings().get("muted_alerts", []))
    conds, judged = _conditions(state, tel, brain)
    with _lock:
        for key, c in conds.items():
            cur = _active.get(key)
            if cur is None:
                ev = _emit(c["level"], c["title"], c["detail"], key, c["act"],
                           None if key in muted else c["speak"])
                _active[key] = {**c, "id": ev["id"], "ts": ev["ts"], "key": key}
            elif (cur.get("detail"), cur.get("title"), cur.get("level")) != (
                    c["detail"], c["title"], c["level"]):
                cur.update(level=c["level"], title=c["title"], detail=c["detail"], act=c["act"])
                _dirty = True
        for key in list(_active):
            if key in conds:
                continue
            if any(key == j or (j.endswith(":") and key.startswith(j)) for j in judged):
                gone = _active.pop(key)
                if gone.get("level") in ("warn", "crit"):
                    _emit("ok", "Resolved: " + gone["title"], "", key)
                _dirty = True

        # app up/down transitions (activity, never spoken)
        running = {a["id"]: bool(a["running"]) for a in state.get("apps", [])
                   if a.get("running") is not None}
        names = {a["id"]: a["name"] for a in state.get("apps", [])}
        if _prev_running is not None:
            for app_id, up in running.items():
                was = _prev_running.get(app_id)
                if was is not None and was != up and app_id != "emucenter":
                    _emit("info", f"{names.get(app_id, app_id)} {'is up' if up else 'stopped'}",
                          "", act={"label": "open", "act": "app-open", "id": app_id} if up else None)
        if running:
            _prev_running = running

        _poll_hermes(state)
    _fire_due()        # backstop: the per-reminder timers normally get there first

    # finished snapshots
    try:
        from . import shield

        for job in shield.jobs_done_since(_shield_seen):
            _shield_seen = max(_shield_seen, job["finished"])
            with _lock:
                if job["state"] == "done":
                    _emit("ok", f"Snapshot saved: {job['name']}",
                          f"{job.get('files', 0)} files · {job.get('size_mb', 0)} MB")
                else:
                    _emit("warn", f"Snapshot failed: {job['name']}", str(job.get("error", ""))[:200])
    except Exception:
        pass
    _save()


# ── reads ────────────────────────────────────────────────────────────────

def snapshot(since: int = 0) -> dict[str, Any]:
    """Feed entries newer than `since`, the active set, and reminders."""
    _load()
    muted = set(settings.get_settings().get("muted_alerts", []))
    with _lock:
        events = [e for e in _feed if e["id"] > since and (e.get("key") not in muted)]
        active = sorted((a for k, a in _active.items() if k not in muted),
                        key=lambda a: ({"crit": 0, "warn": 1, "info": 2}.get(a["level"], 3),
                                       -a.get("ts", 0)))
        return {"events": events, "active": active, "last": max(since, _last_id),
                "reminders": [dict(r) for r in _reminders], "muted": sorted(muted)}


def last_id() -> int:
    _load()
    with _lock:
        return _last_id


def clear_feed() -> None:
    global _dirty
    _load()
    with _lock:
        _feed.clear()
        _dirty = True
    _save(force=True)
