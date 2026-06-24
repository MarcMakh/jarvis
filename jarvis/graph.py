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

    def add(nid: str, label: str, group: str, r: float = 6.0, drill: dict | None = None) -> str:
        nodes.append({"id": nid, "label": label, "group": group, "r": r, "drill": drill})
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

    # Hermes + skills
    h = conn.get("hermes", {})
    if h.get("present"):
        hermes = add("conn:hermes", "Hermes", "ai", 13.0, {"kind": "conn", "name": "hermes"})
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

    # Projects
    proj_hub = add("hub:projects", "Projects", "hub", 10.0)
    link(core, proj_hub)
    for p in state.get("projects", [])[:16]:
        nid = add(f"proj:{p['path']}", p["name"], "project", 8.0,
                  {"kind": "project", "path": p["path"]})
        link(proj_hub, nid)

    return {"nodes": nodes, "links": links}
