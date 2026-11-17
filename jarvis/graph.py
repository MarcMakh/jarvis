"""Build the ecosystem graph (nodes + links) from the live JARVIS state.

The HUD renders this as an animated force-directed map: JARVIS at the centre,
with branches to its AI connectors (Claude + MCP servers, Hermes + skills,
OpenClaw, Obsidian + notes), runtimes, dev tools, and the project ecosystem.
Each node carries a `drill` payload so clicking it opens the same detail modal
the cards use.
"""

from __future__ import annotations

from typing import Any


def build(state: dict[str, Any]) -> dict[str, Any]:
    nodes: list[dict[str, Any]] = []
    links: list[dict[str, str]] = []

    # Which registered apps are up right now -> a live ring on their nodes.
    running = {a["id"] for a in state.get("apps", []) if a.get("running")}
    app_by_path = {str(a.get("path") or "").lower(): a["id"] for a in state.get("apps", [])
                   if a.get("path")}

    def add(nid: str, label: str, group: str, r: float = 6.0, drill: dict | None = None,
            live: bool = False) -> str:
        nodes.append({"id": nid, "label": label, "group": group, "r": r, "drill": drill,
                      "live": live})
        return nid

    def link(a: str, b: str) -> None:
        links.append({"source": a, "target": b})

    core = add("jarvis", "JARVIS", "core", 22.0)

    conn = state.get("connectors", {})

    # Claude + MCP servers
    c = conn.get("claude", {})
    claude = add("conn:claude", f"Claude {c.get('version', '')}".strip(), "ai", 14.0,
                 {"kind": "conn", "name": "claude"})
    link(core, claude)
    for m in conn.get("mcp_servers", []):
        nid = add(f"mcp:{m['name']}", m["name"], "mcp" if m["status"] == "online" else "mcp_err",
                  8.0, {"kind": "mcp", "name": m["name"]})
        link(claude, nid)

    # Hermes chat app (open-LLM relay) — its own server on :7718, with a node
    # per model provider so its availability shows on the map.
    hc = conn.get("hermes_chat", {})
    online = hc.get("status") == "online"
    hchat = add(
        "conn:hermes_chat",
        "Hermes" + (f" v{hc.get('version')}" if hc.get("version") else ""),
        "ai" if online else "absent",
        14.0,
        {"kind": "conn", "name": "hermes_chat"},
        live=online,
    )
    link(core, hchat)
    for p in hc.get("providers", []):
        nid = add(f"llm:{p.get('id')}", p.get("label", p.get("id", "?")),
                  "skill" if p.get("available") else "absent", 7.0,
                  {"kind": "conn", "name": "hermes_chat"})
        link(hchat, nid)

    # Hermes Skills repo + its skills
    h = conn.get("hermes", {})
    if h.get("present"):
        hermes = add("conn:hermes", "Hermes Skills", "ai", 12.0, {"kind": "conn", "name": "hermes"})
        link(core, hermes)
        for s in h.get("skills", []):
            nid = add(f"skill:{s['name']}", s["name"], "skill", 6.0)
            link(hermes, nid)

    # OpenClaw (even if absent, show it dim so the slot is visible)
    oc = conn.get("openclaw", {})
    openclaw = add("conn:openclaw", "OpenClaw", "ai" if oc.get("present") else "absent", 10.0,
                   {"kind": "conn", "name": "openclaw"})
    link(core, openclaw)

    # Obsidian as a "brain" + recent notes
    ob = state.get("obsidian", {})
    if ob.get("available"):
        obs = add("conn:obsidian", f"Obsidian ({ob.get('notes', 0)})", "brain", 14.0,
                  {"kind": "conn", "name": "obsidian"})
        link(core, obs)
        for n in ob.get("recent", [])[:8]:
            nid = add(f"note:{n['rel']}", n["title"], "note", 5.0, {"kind": "note", "rel": n["rel"]})
            link(obs, nid)

    inv = state.get("inventory", {})

    # Runtimes hub
    rt_hub = add("hub:runtimes", "Runtimes", "hub", 9.0)
    link(core, rt_hub)
    for rt in inv.get("runtimes", []):
        if rt.get("present"):
            nid = add(f"rt:{rt['name']}:{rt['label']}", rt["label"], "runtime", 7.0,
                      {"kind": "tool", "name": rt["name"]})
            link(rt_hub, nid)

    # Tools hub
    tool_hub = add("hub:tools", "Dev Tools", "hub", 9.0)
    link(core, tool_hub)
    for t in inv.get("dev_tools", []):
        present = t.get("present")
        nid = add(f"tool:{t['name']}", t["label"], "tool" if present else "absent", 6.5,
                  {"kind": "tool", "name": t["name"]})
        link(tool_hub, nid)

    # Projects. An override `launch = "quantfin"` in [projects.overrides.<Name>]
    # makes clicking the node open that app directly (like the Game Center node
    # does) instead of the project detail modal.
    from .util import load_config

    overrides = (load_config().get("projects", {}) or {}).get("overrides", {}) or {}
    proj_hub = add("hub:projects", "Projects", "hub", 10.0)
    link(core, proj_hub)
    for p in state.get("projects", [])[:24]:
        app = (overrides.get(p["name"]) or {}).get("launch")
        drill = ({"kind": "app", "id": app, "name": p["name"], "path": p["path"]}
                 if app else {"kind": "project", "path": p["path"]})
        app_id = app or app_by_path.get(str(p["path"]).lower())
        nid = add(f"proj:{p['path']}", p["name"], "project", 8.0, drill,
                  live=bool(app_id and app_id in running))
        link(proj_hub, nid)

    # Library (games, emulators). A root flagged `opens = "gamecenter"` or
    # `opens = "emucenter"` in config becomes a single launcher node for that
    # Center app instead of expanding to raw shortcuts; every other root lists
    # its launchable shortcuts as leaves.
    from . import library
    from .launcher import emucenter_dir, gamecenter_dir, moviecenter_dir

    centers = {"gamecenter": gamecenter_dir, "emucenter": emucenter_dir,
               "moviecenter": moviecenter_dir}
    for lib in library.scan():
        opens = lib.get("opens")
        if opens in centers and centers[opens]() is not None:
            gc = add(f"lib:{lib['name']}", lib["name"], opens, 12.0,
                     {"kind": "app", "id": opens, "name": lib["name"]}, live=opens in running)
            link(core, gc)
            continue
        emu = "emu" in lib["name"].lower()
        hub = add(f"lib:{lib['name']}", f"{lib['name']} ({lib['count']})", "hub", 11.0,
                  {"kind": "library", "name": lib["name"]})
        link(core, hub)
        for it in lib["items"]:
            nid = add(f"launch:{it['path']}", it["name"],
                      "emulator" if emu else "game", 10.0,
                      {"kind": "launch", "path": it["path"], "name": it["name"]})
            link(hub, nid)

    return {"nodes": nodes, "links": links}
