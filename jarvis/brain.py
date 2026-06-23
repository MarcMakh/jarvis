"""The 'ask JARVIS' brain.

Routing (config [brain].mode):
  auto -> use ANTHROPIC_API_KEY via the anthropic SDK if present, else the
          claude CLI (which uses your existing Claude Code subscription auth).
  sdk  -> force the SDK path (needs ANTHROPIC_API_KEY).
  cli  -> force the claude CLI path.

Whichever path is used, JARVIS gets a persona and a compact summary of the live
system state injected as context so it can actually answer questions about your
machine.
"""

from __future__ import annotations

import os
from typing import Any

from .util import run, which

PERSONA = (
    "You are JARVIS, a calm, dry-witted, hyper-competent AI assistant modeled on "
    "Tony Stark's JARVIS. You manage {honorific}'s development machine. Address the "
    "user as '{honorific}'. Be concise and precise; a touch of dry wit is welcome but "
    "never verbose. When asked about the system, ground every answer in the SYSTEM "
    "STATE provided below. If something isn't in the state, say so plainly rather "
    "than guessing. Keep answers short enough to be spoken aloud."
)


def status(cfg: dict[str, Any]) -> dict[str, Any]:
    """Report which brain path is active so the HUD can show readiness."""
    bcfg = cfg.get("brain", {})
    enabled = bcfg.get("enabled", True)
    mode = bcfg.get("mode", "auto")
    have_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
    have_cli = bool(which("claude"))
    if not enabled:
        return {"ready": False, "path": "disabled", "label": "brain disabled"}
    if mode == "sdk" or (mode == "auto" and have_key):
        return {
            "ready": have_key,
            "path": "sdk",
            "label": f"SDK · {bcfg.get('sdk_model', 'claude-sonnet-4-6')}"
            if have_key
            else "SDK (set ANTHROPIC_API_KEY)",
        }
    return {
        "ready": have_cli,
        "path": "cli",
        "label": f"Claude CLI · {bcfg.get('cli_model', 'sonnet')}"
        if have_cli
        else "CLI (claude not found)",
    }


def ping(cfg: dict[str, Any]) -> dict[str, Any]:
    """Do a real, tiny round-trip to confirm the brain can actually answer."""
    st = status(cfg)
    if not st["ready"] and st["path"] != "cli":
        return {"ok": False, "answer": st["label"], "source": st["path"]}
    bcfg = cfg.get("brain", {})
    system = "You are JARVIS. Reply with exactly one word: ONLINE."
    if st["path"] == "sdk":
        res = _ask_sdk("Are you online?", system, bcfg)
    else:
        res = _ask_cli("Are you online?", system, bcfg)
    res["path"] = st["path"]
    return res


def build_context(state: dict[str, Any]) -> str:
    """Compact, human-readable snapshot of the live state for the model."""
    lines: list[str] = []
    inv = state.get("inventory", {})

    rts = [r for r in inv.get("runtimes", []) if r.get("present")]
    lines.append("RUNTIMES: " + ", ".join(f"{r['label']} {r['version']}" for r in rts))

    tools = [t for t in inv.get("dev_tools", []) if t.get("present")]
    lines.append("DEV TOOLS: " + ", ".join(f"{t['label']} {t['version']}" for t in tools))

    missing = [t["label"] for t in inv.get("dev_tools", []) if not t.get("present")]
    if missing:
        lines.append("MISSING TOOLS: " + ", ".join(missing))

    conn = state.get("connectors", {})
    c = conn.get("claude", {})
    lines.append(f"CLAUDE CODE: {c.get('status')} v{c.get('version')}")
    mcp = conn.get("mcp_servers", [])
    lines.append(
        "MCP SERVERS: " + ", ".join(f"{m['name']}({m['status']})" for m in mcp) if mcp else "MCP SERVERS: none"
    )
    herm = conn.get("hermes", {})
    if herm.get("skills"):
        names = ", ".join(s["name"] for s in herm["skills"])
        lines.append(f"HERMES: {herm.get('status')} ({len(herm['skills'])} skills: {names})")
    else:
        lines.append(f"HERMES: {herm.get('status', 'not detected')}")
    lines.append(f"OPENCLAW: {conn.get('openclaw', {}).get('status', 'not detected')}")

    ob = state.get("obsidian", {})
    if ob.get("available"):
        lines.append(f"OBSIDIAN: linked, {ob.get('notes')} notes at {ob.get('vault')}")
    else:
        lines.append("OBSIDIAN: not linked")

    ups = state.get("updates", {})
    if ups:
        total = sum(ups.get(k, {}).get("count", 0) for k in ups)
        lines.append(f"UPDATES AVAILABLE: {total} package update(s) pending")

    sysm = inv.get("system", {})
    if sysm.get("ok"):
        lines.append(
            f"HOST: CPU {sysm['cpu_pct']}% / RAM {sysm['mem_pct']}% "
            f"({sysm['mem_used_gb']}/{sysm['mem_total_gb']}GB) / "
            f"Disk {sysm['disk_pct']}% used, {sysm['disk_free_gb']}GB free"
        )

    projs = state.get("projects", [])
    if projs:
        lines.append("RECENT PROJECTS: " + ", ".join(p["name"] for p in projs[:8]))

    return "\n".join(lines)


def ask(question: str, state: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    bcfg = cfg.get("brain", {})
    if not bcfg.get("enabled", True):
        return {"ok": False, "answer": "Brain is disabled in config.", "source": "disabled"}

    honorific = cfg.get("identity", {}).get("honorific", "sir")
    persona = PERSONA.format(honorific=honorific)
    context = build_context(state)
    system = f"{persona}\n\n=== SYSTEM STATE ===\n{context}"

    mode = bcfg.get("mode", "auto")
    have_key = bool(os.environ.get("ANTHROPIC_API_KEY"))

    if mode == "sdk" or (mode == "auto" and have_key):
        return _ask_sdk(question, system, bcfg)
    return _ask_cli(question, system, bcfg)


def _ask_sdk(question: str, system: str, bcfg: dict[str, Any]) -> dict[str, Any]:
    try:
        import anthropic

        client = anthropic.Anthropic()
        model = bcfg.get("sdk_model", "claude-sonnet-4-6")
        msg = client.messages.create(
            model=model,
            max_tokens=600,
            system=system,
            messages=[{"role": "user", "content": question}],
        )
        text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
        return {"ok": True, "answer": text.strip(), "source": f"sdk:{model}"}
    except Exception as exc:
        return {"ok": False, "answer": f"SDK path failed: {exc}", "source": "sdk-error"}


def _ask_cli(question: str, system: str, bcfg: dict[str, Any]) -> dict[str, Any]:
    exe = which("claude")
    if not exe:
        return {
            "ok": False,
            "answer": "No brain available: claude CLI not found and ANTHROPIC_API_KEY not set.",
            "source": "unavailable",
        }
    model = bcfg.get("cli_model", "sonnet")
    cmd = [
        exe,
        "-p",
        question,
        "--append-system-prompt",
        system,
        "--model",
        model,
        "--output-format",
        "text",
    ]
    res = run(cmd, timeout=120)
    if res.ok and res.out.strip():
        return {"ok": True, "answer": res.out.strip(), "source": f"cli:{model}"}
    detail = res.text or "no output"
    if "not logged in" in detail.lower() or "/login" in detail.lower():
        detail = (
            "Claude CLI isn't logged in for headless use. Run `claude` once and sign "
            "in, or set ANTHROPIC_API_KEY to use the SDK path."
        )
    return {"ok": False, "answer": f"Brain unavailable — {detail}", "source": "cli-error"}
