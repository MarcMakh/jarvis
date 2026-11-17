"""Things JARVIS can *do* — one whitelist shared by three callers:

  * the command palette (Ctrl+K) lists them via `catalog()`,
  * typed/spoken commands are matched without any model by `match_intent()`
    ("open QuantFin", "remind me in 20 minutes to stretch", "note: …"),
  * the brain may request one with a `[[do:name arg]]` directive.

Every action resolves its argument against things JARVIS already knows (the
app registry, the scanned projects) — nothing here executes free text.
A result is {ok, say, …}; `say` is the line JARVIS shows and speaks, and an
optional `ui` hint asks the HUD to switch view / open a panel.
"""

from __future__ import annotations

import difflib
import re
import time
from pathlib import Path
from typing import Any

from . import alerts, apps, launcher, shield
from .obsidian import from_config as vault
from .util import which


# ── fuzzy name resolution ────────────────────────────────────────────────

def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def _best(query: str, choices: dict[str, list[str]], cutoff: float = 0.74) -> str | None:
    """Pick the key whose aliases best match `query`. Exact > prefix > contains
    > close spelling; returns None when nothing is convincingly close."""
    q = _norm(query)
    if not q:
        return None
    best_key, best_score = None, 0.0
    for key, aliases in choices.items():
        for alias in aliases:
            a = _norm(alias)
            if not a:
                continue
            if a == q:
                score = 1.0
            elif a.startswith(q) or q.startswith(a):
                score = 0.9 - abs(len(a) - len(q)) * 0.01
            elif q in a or a in q:
                score = 0.8 - abs(len(a) - len(q)) * 0.01
            else:
                score = difflib.SequenceMatcher(None, q, a).ratio() * 0.85
            if score > best_score:
                best_key, best_score = key, score
    return best_key if best_score >= cutoff else None


_APP_ALIASES: dict[str, list[str]] = {
    "quantflow": ["quant flow", "intraday", "volume scanner"],
    "quantfin": ["quant fin", "trading dashboard", "trading app", "end of day"],
    "chess": ["chess", "chess training", "chess coach"],
    "gamecenter": ["games", "game library", "pc games"],
    "emucenter": ["emulators", "emu center", "emulation", "roms"],
    "moviecenter": ["movies", "movie library", "shows"],
    "pantry": ["pantry app", "recipes", "meal planner"],
    "hermes": ["hermes chat", "chat", "llm chat"],
    "lmstudio": ["lm studio", "local models"],
    "obsidian": ["vault", "notes", "obsidian vault"],
    "ai-delivery": ["ai delivery system", "ai delivery"],
    "powerbi-delivery": ["power bi delivery system", "powerbi", "power bi"],
    "engineering-delivery": ["engineering delivery system", "engineering"],
    "translation-delivery": ["translation delivery system", "translation"],
}


def _find_app(query: str, state: dict[str, Any]) -> dict[str, Any] | None:
    listing = state.get("apps") or apps.status()
    choices = {a["id"]: [a["id"], a["name"], *_APP_ALIASES.get(a["id"], [])] for a in listing}
    key = _best(query, choices)
    return next((a for a in listing if a["id"] == key), None) if key else None


def _find_project(query: str, state: dict[str, Any]) -> dict[str, Any] | None:
    projs = state.get("projects") or []
    key = _best(query, {p["path"]: [p["name"]] for p in projs})
    return next((p for p in projs if p["path"] == key), None) if key else None


# ── durations ────────────────────────────────────────────────────────────

_UNIT = {"s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1,
         "m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60,
         "h": 3600, "hr": 3600, "hrs": 3600, "hour": 3600, "hours": 3600}
_DUR = re.compile(r"(\d+(?:\.\d+)?)\s*(seconds?|secs?|s|minutes?|mins?|m|hours?|hrs?|h)(?![a-z])", re.I)
_WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "five": 5, "ten": 10, "fifteen": 15,
          "twenty": 20, "thirty": 30, "half an": 0.5, "half a": 0.5}


def parse_duration(text: str) -> tuple[float, str] | None:
    """Leading duration -> (seconds, the rest). '1h30m stretch' -> (5400, 'stretch')."""
    t = text.strip()
    for word, n in sorted(_WORDS.items(), key=lambda kv: -len(kv[0])):
        m = re.match(rf"{word}\s+(seconds?|minutes?|hours?)\b", t, re.I)
        if m:
            t = f"{n} {m.group(1)}" + t[m.end():]
            break
    total, pos = 0.0, 0
    while True:
        m = _DUR.match(t, pos)
        if not m:
            break
        total += float(m.group(1)) * _UNIT[m.group(2).lower()]
        pos = m.end()
        nxt = re.match(r"\s*(?:and\s+)?", t[pos:])
        pos += nxt.end() if nxt else 0
    if total <= 0:
        return None
    return total, t[pos:].strip()


def _human(seconds: float) -> str:
    seconds = int(round(seconds))
    if seconds < 60:
        return f"{seconds} second{'s' if seconds != 1 else ''}"
    mins, sec = divmod(seconds, 60)
    hours, mins = divmod(mins, 60)
    bits = []
    if hours:
        bits.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if mins:
        bits.append(f"{mins} minute{'s' if mins != 1 else ''}")
    if sec and not hours:
        bits.append(f"{sec} seconds")
    return " ".join(bits)


# ── execution ────────────────────────────────────────────────────────────

def execute(name: str, arg: str, state: dict[str, Any], honorific: str = "sir") -> dict[str, Any]:
    """Run one whitelisted action. Never raises."""
    try:
        return _execute(name.lower().strip(), (arg or "").strip(), state, honorific)
    except Exception as exc:  # an action must never take the brain down with it
        return {"ok": False, "say": f"That didn't work, {honorific}: {exc}"}


def _execute(name: str, arg: str, state: dict[str, Any], sir: str) -> dict[str, Any]:
    if name == "open":
        app = _find_app(arg, state)
        if app is None:
            proj = _find_project(arg, state)
            if proj is not None:
                return _execute("project", proj["name"], state, sir)
            return {"ok": False, "say": f"I don't have an app called “{arg}”, {sir}."}
        res = apps.open_app(app["id"])
        if not res.get("ok"):
            return {"ok": False, "say": f"I couldn't open {app['name']}: {res.get('error', 'unknown error')}"}
        say = (f"{app['name']} is starting, {sir} — its window will appear in a few seconds."
               if res.get("action") == "starting" else f"Opening {app['name']}, {sir}.")
        return {"ok": True, "say": say, "refresh": ["apps"]}

    if name == "stop":
        app = _find_app(arg, state)
        if app is None:
            return {"ok": False, "say": f"I don't have an app called “{arg}”, {sir}."}
        res = apps.stop_app(app["id"])
        if not res.get("ok"):
            return {"ok": False, "say": res.get("error", "couldn't stop it")}
        return {"ok": True, "say": f"{app['name']} stopped, {sir}.", "refresh": ["apps"]}

    if name in ("project", "code", "run", "folder", "snapshot"):
        if name == "snapshot" and arg.lower() in ("all", "everything", "at risk", "unprotected"):
            res = shield.snapshot_at_risk(state.get("projects") or [])
            n = len(res["queued"])
            return {"ok": True, "refresh": ["shield"],
                    "say": (f"Snapshotting {n} unprotected project{'s' if n != 1 else ''}, {sir}."
                            if n else f"Everything is already covered, {sir}.")}
        proj = _find_project(arg, state)
        if proj is None:
            return {"ok": False, "say": f"I can't find a project called “{arg}”, {sir}."}
        if name == "project":
            return {"ok": True, "say": f"Here is {proj['name']}, {sir}.",
                    "ui": {"do": "project", "path": proj["path"]}}
        if name == "code":
            code = which("code")
            if not code:
                return {"ok": False, "say": "VS Code isn't installed on this machine."}
            import subprocess

            from .util import no_window

            subprocess.Popen([code, proj["path"]], creationflags=no_window())
            return {"ok": True, "say": f"Opening {proj['name']} in VS Code, {sir}."}
        if name == "run":
            target = proj.get("run_path") or proj["path"]
            res = launcher.launch_app(target)
            if not res.get("ok"):
                return {"ok": False, "say": f"I couldn't run {proj['name']}: {res.get('error')}"}
            return {"ok": True, "say": f"Running {proj['name']} ({res.get('plan')}), {sir}.",
                    "refresh": ["apps"]}
        if name == "folder":
            import os

            os.startfile(proj["path"])  # type: ignore[attr-defined]
            return {"ok": True, "say": f"Opening the {proj['name']} folder, {sir}."}
        res = shield.snapshot(proj["path"])
        if not res.get("ok"):
            return {"ok": False, "say": f"I couldn't snapshot {proj['name']}: {res.get('error')}"}
        return {"ok": True, "say": f"Snapshotting {proj['name']}, {sir}. I'll note it when it's saved.",
                "refresh": ["shield"]}

    if name == "updates":
        return {"ok": True, "say": f"Checking for updates, {sir}. This takes a minute.",
                "refresh": ["updates!"], "ui": {"do": "view", "view": "systems"}}

    if name == "note":
        if not arg:
            return {"ok": False, "say": f"What should I note down, {sir}?"}
        res = vault().capture(arg)
        if not res.get("ok"):
            return {"ok": False, "say": f"I couldn't save that note: {res.get('error')}"}
        return {"ok": True, "say": f"Noted, {sir}.", "refresh": ["obsidian"]}

    if name == "remind":
        parsed = parse_duration(arg)
        if not parsed:
            return {"ok": False, "say": f"When should I remind you, {sir}? Try “remind me in 20 minutes to stretch”."}
        seconds, text = parsed
        text = re.sub(r"^(?:to|that|about)\s+", "", text, flags=re.I).strip(" .") or "time's up"
        alerts.add_reminder(time.time() + seconds, text)
        return {"ok": True, "say": f"I'll remind you in {_human(seconds)}, {sir}."}

    if name == "brief":
        from . import briefing

        b = briefing.build(state)
        return {"ok": True, "say": b["speech"], "ui": {"do": "view", "view": "overview"}}

    if name == "claude":
        res = apps.open_claude_terminal()
        return {"ok": bool(res.get("ok")),
                "say": f"Opening a Claude terminal, {sir}." if res.get("ok") else str(res.get("error"))}

    if name == "vault":
        return {"ok": True, "say": f"Searching your vault, {sir}.",
                "ui": {"do": "vault", "query": arg}}

    if name == "view":
        view = _best(arg, {v: [v] for v in ("overview", "projects", "systems", "map", "vault")}, 0.6)
        if not view:
            return {"ok": False, "say": f"I don't have a “{arg}” view, {sir}."}
        return {"ok": True, "say": f"Showing {view}, {sir}.", "ui": {"do": "view", "view": view}}

    if name in ("mute", "unmute"):
        return {"ok": True, "say": "" if name == "mute" else f"Voice restored, {sir}.",
                "ui": {"do": name}}

    if name == "refresh":
        return {"ok": True, "say": f"Rescanning everything, {sir}.", "refresh": ["all!"]}

    return {"ok": False, "say": f"I don't know how to “{name}”, {sir}."}


# ── intent matching (no model involved) ──────────────────────────────────

_POLITE = re.compile(r"^(?:hey |ok |okay )?(?:jarvis[, ]+)?(?:please |could you |can you |would you )?", re.I)
_TRAIL = re.compile(r"(?:,? please| for me| now|,? jarvis)?[.!?\s]*$", re.I)

_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^(?:open|show)\s+(.+?)\s+in\s+(?:vs ?code|code|the editor)$", re.I), "code"),
    (re.compile(r"^(?:stop|close|kill|quit|shut ?down|terminate)\s+(?:the\s+|my\s+)?(.+)$", re.I), "stop"),
    (re.compile(r"^(?:back ?up|snapshot|take a snapshot of)\s+(?:the\s+|my\s+)?(.+?)(?:\s+project)?$", re.I), "snapshot"),
    (re.compile(r"^(?:open|launch|start|run|fire up|bring up|boot up|load)\s+(?:up\s+)?(?:the\s+|my\s+)?(.+?)(?:\s+app)?$", re.I), "open"),
    (re.compile(r"^(?:note|remember|jot(?: down)?|capture|write down)(?: that| this)?\s*[:,-]?\s+(.+)$", re.I), "note"),
    (re.compile(r"^(?:remind me|set a (?:timer|reminder)|timer|reminder)\s+(?:in\s+|for\s+)?(.+)$", re.I), "remind"),
    (re.compile(r"^(?:check|scan|look)(?: for)?\s+(?:for\s+)?updates?$", re.I), "updates"),
    (re.compile(r"^(?:brief me|briefing|status report|sit ?rep|give me (?:a |the )?(?:status |morning |daily )?(?:report|briefing|rundown))$", re.I), "brief"),
    (re.compile(r"^(?:mute|be quiet|quiet|silence|shut up|stop talking)$", re.I), "mute"),
    (re.compile(r"^(?:unmute|speak|you can talk)$", re.I), "unmute"),
    (re.compile(r"^(?:refresh|rescan|scan everything|refresh everything)$", re.I), "refresh"),
    (re.compile(r"^(?:go to|show(?: me)?|switch to)\s+(?:the\s+)?(overview|projects|systems|map|vault)(?:\s+(?:view|tab|page))?$", re.I), "view"),
    (re.compile(r"^(?:search|look in|find in)\s+(?:my\s+|the\s+)?(?:vault|notes)\s+(?:for\s+)?(.+)$", re.I), "vault"),
]


def match_intent(text: str, state: dict[str, Any]) -> tuple[str, str] | None:
    """Recognise a plain command. Deliberately conservative: anything that
    doesn't clearly name a known app/project falls through to the model."""
    t = _TRAIL.sub("", _POLITE.sub("", (text or "").strip())).strip()
    if not t or len(t) > 160 or "?" in text:
        return None
    for pat, name in _PATTERNS:
        m = pat.match(t)
        if not m:
            continue
        arg = (m.group(1) if m.groups() else "").strip()
        if name == "open":
            # "run the tests", "open the pod bay doors" -> not ours; let the brain answer
            if _find_app(arg, state) is None and _find_project(arg, state) is None:
                return None
        elif name == "stop":
            if _find_app(arg, state) is None:
                return None
        elif name in ("code", "snapshot"):
            if arg.lower() not in ("all", "everything") and _find_project(arg, state) is None:
                return None
        elif name == "remind":
            if parse_duration(arg) is None:
                return None
        return name, arg
    return None


# ── command palette catalogue ────────────────────────────────────────────

def catalog(state: dict[str, Any]) -> list[dict[str, Any]]:
    """Everything the palette can search: apps, projects, tools, notes, actions."""
    items: list[dict[str, Any]] = []
    for a in state.get("apps") or []:
        items.append({"id": f"app:{a['id']}", "title": ("Open " if not a.get("running") else "Show ") + a["name"],
                      "sub": a["desc"], "icon": a["icon"], "group": "Apps",
                      "run": {"act": "app-open", "id": a["id"]},
                      "keywords": " ".join(_APP_ALIASES.get(a["id"], [])), "running": a.get("running")})
        if a.get("running") and a.get("can_stop"):
            items.append({"id": f"stop:{a['id']}", "title": "Stop " + a["name"], "sub": "end its processes",
                          "icon": "■", "group": "Apps", "run": {"act": "app-stop", "id": a["id"]}})
    for p in state.get("projects") or []:
        sub = (p.get("branch") or ("no git" if not p.get("git") else "git")) + " · " + p["root"]
        items.append({"id": f"proj:{p['path']}", "title": p["name"], "sub": sub, "icon": "▣",
                      "group": "Projects", "run": {"kind": "project", "path": p["path"]}})
        items.append({"id": f"code:{p['path']}", "title": f"Open {p['name']} in VS Code", "sub": p["path"],
                      "icon": "‹›", "group": "Projects", "run": {"act": "open-code", "path": p["path"]}})
        if p.get("runnable"):
            items.append({"id": f"run:{p['path']}", "title": f"Run {p['name']}", "sub": p.get("run_label") or "",
                          "icon": "▶", "group": "Projects",
                          "run": {"act": "run-app", "path": p.get("run_path") or p["path"], "label": p["name"]}})
    ob = state.get("obsidian") or {}
    for n in (ob.get("recent") or [])[:12]:
        items.append({"id": f"note:{n['rel']}", "title": n["title"], "sub": "note · " + str(Path(n["rel"]).parent),
                      "icon": "✎", "group": "Vault", "run": {"act": "vault-open", "rel": n["rel"]}})
    inv = state.get("inventory") or {}
    for t in inv.get("dev_tools", []):
        if t.get("present") and t["name"] in ("code",):
            items.append({"id": "tool:code", "title": "Open VS Code", "sub": t.get("version") or "",
                          "icon": "‹›", "group": "Tools", "run": {"act": "launch-term", "target": "code"}})
    if (state.get("connectors", {}).get("claude") or {}).get("present"):
        items.append({"id": "tool:claude", "title": "Open a Claude terminal", "sub": "claude CLI",
                      "icon": "✳", "group": "Tools", "run": {"act": "launch-term", "target": "claude"}})
    return items
