"""The briefing: what deserves your attention right now, computed straight
from live state — no model call, so it is instant, free and always available.

`build()` returns

  items     [{level: ok|info|warn|crit, icon, text, act?}]  the full rundown
  headline  one line for the HUD's hero panel
  speech    what JARVIS says aloud (greeting + the few things that matter)

It replaces the old fixed greeting ("All core systems nominal…"), which said
the same thing whether or not anything was actually wrong.
"""

from __future__ import annotations

import time
from typing import Any

from . import settings
from .util import load_config


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _join(names: list[str], limit: int = 3) -> str:
    shown = names[:limit]
    more = len(names) - len(shown)
    text = ", ".join(shown)
    return text + (f" and {more} more" if more > 0 else "")


def build(state: dict[str, Any], brain: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = load_config()
    sir = cfg.get("identity", {}).get("honorific", "sir")
    hour = time.localtime().tm_hour
    part = "morning" if hour < 12 else "afternoon" if hour < 18 else "evening"
    items: list[dict[str, Any]] = []
    meta = state.get("meta", {})

    def loaded(section: str) -> bool:
        return bool((meta.get(section) or {}).get("ts"))

    # A muted alert stays muted here too: the same `key` silences it in the
    # bell, in this rundown, and in what JARVIS says when he greets you.
    muted = set(settings.get_settings().get("muted_alerts", []))

    def add(level: str, icon: str, text: str, say: str | None = None,
            act: dict[str, Any] | None = None, key: str | None = None) -> None:
        if key and key in muted:
            return
        items.append({"level": level, "icon": icon, "text": text, "say": say, "act": act,
                      "key": key})

    # ── brain ──
    if brain is not None:
        if not brain.get("ready"):
            add("crit", "🧠", "No brain engine is available — questions can't be answered.",
                "I have no reasoning engine available.", {"ui": "settings", "label": "fix"})
        elif brain.get("fallback"):
            # Working, just not on the first-choice engine: worth showing, not
            # worth announcing at every greeting.
            first = (brain.get("engines") or [{}])[0]
            add("info", "🧠", f"Thinking with {brain['label']} — {first.get('label', 'the first engine')} "
                              f"is unavailable ({first.get('reason', 'unknown')}).",
                None, {"ui": "settings", "label": "brain"}, key="brain")
        else:
            add("ok", "🧠", f"Brain online: {brain['label']}.")

    # ── AI stack ──
    conn = state.get("connectors", {})
    if loaded("mcp"):
        mcp = conn.get("mcp_servers", [])
        bad = [m["name"] for m in mcp if m.get("status") == "error"]
        up = sum(1 for m in mcp if m.get("status") == "online")
        if bad:
            add("warn", "🔌", f"MCP: {_join(bad)} failing ({up} of {len(mcp)} connected).",
                f"{_plural(len(bad), 'MCP server')} failing: {_join(bad)}.",
                {"ui": "view", "view": "systems", "label": "systems"},
                key=f"mcp:{bad[0]}" if len(bad) == 1 else None)
        elif mcp:
            add("ok", "🔌", f"All {len(mcp)} MCP servers connected.")
    hc = conn.get("hermes_chat") or {}
    if loaded("hermes_chat"):
        if hc.get("status") == "online":
            ready = [p["label"] for p in hc.get("providers", []) if p.get("available")]
            usage = hc.get("usage") or {}
            spend = usage.get("spent_today") or 0
            budget = usage.get("daily_budget_usd") or 0
            text = f"Hermes online — {_plural(len(ready), 'provider')} ready"
            if spend:
                text += f", ${spend:.2f} spent today"
            level = "ok"
            say = None
            if budget and spend >= budget:
                level, say = "warn", f"Hermes has passed today's ${budget:.2f} budget."
                text += f" (over the ${budget:.2f} budget)"
            add(level, "🜁", text + ".", say, {"act": "app-open", "id": "hermes", "label": "open"})
        else:
            add("info", "🜁", "Hermes is offline.", None,
                {"act": "app-open", "id": "hermes", "label": "start"})

    # ── host ──
    tel = (state.get("inventory") or {}).get("system") or {}
    if tel.get("ok"):
        for d in tel.get("disks", []):
            if d.get("system") and (d["pct"] >= 95 or d["free_gb"] < 10):
                add("crit", "💽", f"System drive {d['mount']} is {d['pct']:.0f}% full ({d['free_gb']:.0f} GB left).",
                    f"The system drive has only {d['free_gb']:.0f} gigabytes left.",
                    key=f"disk:{d['mount']}")
            elif d["pct"] >= 98 or d["free_gb"] < 3:
                add("warn", "💽", f"Drive {d['mount']} is full — {d['free_gb']:.1f} GB left.",
                    f"Drive {d['mount'][0]} is full.", key=f"disk:{d['mount']}")
        g = tel.get("gpu") or {}
        if g and g.get("util", 0) >= 85:
            add("info", "🎛", f"GPU busy: {g['util']}% load, {g['vram_used_gb']}/{g['vram_total_gb']} GB VRAM"
                             + (f", {g['temp']}°C" if g.get("temp") else "")
                             + " — local models will be slow right now.")

    # ── automations ──
    if loaded("automations"):
        autos = state.get("automations", [])
        failed = [t["name"] for t in autos if t.get("failed")]
        off = [t["name"] for t in autos if not t.get("enabled")]
        if failed:
            add("warn", "⏱", f"Scheduled task failed: {_join(failed)}.",
                f"{_plural(len(failed), 'scheduled task')} failed: {_join(failed)}.",
                {"ui": "view", "view": "systems", "label": "systems"},
                key=f"task:{failed[0]}" if len(failed) == 1 else None)
        if off:
            add("info", "⏱", f"Disabled scheduled {'task' if len(off) == 1 else 'tasks'}: {_join(off)}.")
        nxt = sorted((t for t in autos if t.get("enabled") and t.get("next_run")),
                     key=lambda t: t["next_run"])
        if nxt and not failed:
            when = str(nxt[0]["next_run"]).replace("T", " ")[:16]
            add("ok", "⏱", f"Next automation: {nxt[0]['name']} at {when[11:]} on {when[:10]}.")

    # ── projects & protection ──
    if loaded("projects"):
        projs = state.get("projects", [])
        dirty = [p["name"] for p in projs if p.get("dirty")]
        ahead = [p["name"] for p in projs if p.get("ahead")]
        if dirty:
            add("info", "✎", f"Uncommitted work in {_join(dirty, 4)}.", None,
                {"ui": "view", "view": "projects", "filter": "dirty", "label": "projects"})
        if ahead:
            add("info", "⇡", f"Unpushed commits in {_join(ahead, 4)}.")
        sh = state.get("shield") or {}
        risky = [p["name"] for p in sh.get("projects", [])
                 if not p["covered"] and p["level"] in ("exposed", "local")]
        if risky:
            add("warn", "🛡", f"{_plural(len(risky), 'project')} exist only on this disk "
                             f"(no remote, no recent snapshot): {_join(risky, 4)}.",
                f"{_plural(len(risky), 'project')} {'has' if len(risky) == 1 else 'have'} no backup.",
                {"ui": "view", "view": "projects", "filter": "risk", "label": "protect"},
                key="shield")
        elif projs:
            add("ok", "🛡", f"All {len(projs)} projects are pushed or snapshotted.")

    # ── updates ──
    ups = state.get("updates") or {}
    if ups.get("checked"):
        total = sum(int((ups.get(k) or {}).get("count", 0)) for k in ("npm", "winget", "env"))
        if total:
            add("info", "⬆", f"{_plural(total, 'update')} available.", None,
                {"ui": "view", "view": "systems", "label": "review"}, key="updates")
        else:
            add("ok", "⬆", "Toolchain is up to date.")

    # ── apps ──
    running = [a["name"] for a in state.get("apps", []) if a.get("running")]
    if running:
        add("ok", "▶", f"Running: {_join(running, 6)}.")

    order = {"crit": 0, "warn": 1, "info": 2, "ok": 3}
    items.sort(key=lambda i: order[i["level"]])
    attention = [i for i in items if i["level"] in ("crit", "warn")]

    if not attention:
        headline = "All systems nominal."
        speech = f"Good {part}, {sir}. All systems nominal."
    else:
        headline = (f"{_plural(len(attention), 'item')} need{'s' if len(attention) == 1 else ''} "
                    "your attention.")
        said = [i["say"] or i["text"] for i in attention[:3]]
        speech = f"Good {part}, {sir}. {headline} " + " ".join(said)
        if len(attention) > 3:
            speech += f" And {len(attention) - 3} more."
    return {"part": part, "honorific": sir, "headline": headline, "speech": speech, "items": items,
            "attention": len(attention), "ts": time.time()}
