"""The 'ask JARVIS' brain.

An *engine chain* instead of a single route: engines are tried in order and
the first one that is actually ready answers; if it fails before saying
anything, the next one takes over. So JARVIS keeps thinking when Claude is
signed out, rate-limited or offline — it falls back to whatever Hermes can
reach, or to a local model.

    [brain]
    chain = ["claude-cli", "sdk", "hermes", "local"]

  claude-cli  your Claude subscription through the `claude` CLI (needs
              `claude auth login` once — JARVIS checks, it doesn't assume)
  sdk         the Anthropic API (ANTHROPIC_API_KEY in .env)
  hermes      any model Hermes can reach — its providers, keys and cost ledger
  local       an OpenAI-compatible endpoint directly (LM Studio by default),
              for when Hermes itself is down

Answers stream token by token (`ask_stream`), the conversation is multi-turn,
and the brain can *act*: it may emit `[[do:open quantfin]]`-style directives,
which actions.py validates and runs (see there for the whitelist).
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from collections.abc import Callable, Iterator
from typing import Any

import httpx

from . import settings
from .textstream import DirectiveFilter, ThinkSplitter, iter_sse_json
from .util import clean_env, data_dir, no_window, which

ENGINES = ("claude-cli", "sdk", "hermes", "local")

PERSONA = (
    "You are JARVIS, a calm, dry-witted, hyper-competent AI assistant modeled on "
    "Tony Stark's JARVIS. You run {honorific}'s development machine and address the "
    "user as '{honorific}'. Be concise and precise; a touch of dry wit is welcome, "
    "verbosity is not. Your answers are read aloud, so default to one to three short "
    "sentences and use a short markdown list only when you are listing things. Ground "
    "every claim about the machine in the SYSTEM STATE below; if something isn't in "
    "it, say so plainly rather than guessing."
)

ACTIONS_DOC = """=== ACTIONS ===
You can act on this machine. When — and only when — the user asks for one of these,
put the directive on its own line in your reply and confirm in a few words:
  [[do:open <app id>]]            open or start an app ({apps})
  [[do:stop <app id>]]            stop a running app
  [[do:code <project name>]]      open a project in VS Code
  [[do:run <project name>]]       run a project's app
  [[do:project <project name>]]   show a project's details
  [[do:updates]]                  check for updates
  [[do:note <text>]]              save a note to the Obsidian inbox
  [[do:remind <duration> <text>]] set a reminder, e.g. [[do:remind 20m stretch]]
  [[do:snapshot <project name>]]  back up a project to a zip snapshot
  [[do:brief]]                    give the status briefing
Never show directive syntax in any other situation, and never invent an action
that is not in this list."""


class EngineError(Exception):
    """An engine couldn't answer. kind: auth | unavailable | failed."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


# ── configuration ────────────────────────────────────────────────────────

def _bcfg(cfg: dict[str, Any]) -> dict[str, Any]:
    return cfg.get("brain", {}) or {}


def chain(cfg: dict[str, Any]) -> list[str]:
    """Engine order: the user's pick first (⚙ panel), then [brain].chain."""
    b = _bcfg(cfg)
    order = [e for e in (b.get("chain") or []) if e in ENGINES]
    if not order:  # legacy `mode` key
        mode = b.get("mode", "auto")
        have_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
        if mode == "sdk":
            order = ["sdk", "hermes", "local"]
        elif mode == "cli":
            order = ["claude-cli", "hermes", "local"]
        else:
            order = (["sdk", "claude-cli"] if have_key else ["claude-cli", "sdk"]) + ["hermes", "local"]
    pick = settings.get_settings().get("brain_engine", "auto")
    if pick in ENGINES:
        order = [pick] + [e for e in order if e != pick]
    return order


# ── cheap, cached readiness probes ───────────────────────────────────────

_cache_lock = threading.Lock()
_cache: dict[str, tuple[float, Any]] = {}
_penalty: dict[str, tuple[float, str]] = {}   # engine -> (until, reason)


def _cached(key: str, ttl: float, fn: Callable[[], Any], bad_ttl: float = 4.0) -> Any:
    """Memoise a probe. A failed probe (None) is remembered only briefly, so a
    server that was merely slow to answer isn't written off for half a minute."""
    now = time.time()
    hit = _cache.get(key)
    if hit and now - hit[0] < (ttl if hit[1] is not None else bad_ttl):
        return hit[1]
    val = fn()
    with _cache_lock:
        _cache[key] = (time.time(), val)
    return val


def forget_probes() -> None:
    """Drop cached readiness (after settings change or a manual brain test)."""
    with _cache_lock:
        _cache.clear()
    _penalty.clear()


def _cli_auth() -> dict[str, Any]:
    from .connectors import claude_auth

    return _cached("cli-auth", 180, claude_auth)


def _hermes_url() -> str:
    from .connectors import hermes_chat_url

    return hermes_chat_url()


def _hermes_providers() -> list[dict[str, Any]] | None:
    def probe() -> list[dict[str, Any]] | None:
        try:
            r = httpx.get(f"{_hermes_url()}/api/providers",
                          timeout=httpx.Timeout(15, connect=2))
            r.raise_for_status()
            return r.json().get("providers", [])
        except Exception:
            return None

    return _cached("hermes-providers", 25, probe)


def _hermes_target(cfg: dict[str, Any]) -> tuple[str, str, str] | None:
    """(provider id, model, provider label) Hermes should answer with.
    Order: your explicit pick -> a local model already loaded in memory (free
    and instant) -> the first available provider's default model."""
    provs = _hermes_providers()
    if not provs:
        return None
    s = settings.get_settings()
    want_p, want_m = s.get("brain_hermes_provider", ""), s.get("brain_hermes_model", "")
    avail = [p for p in provs if p.get("available") and p.get("models")]
    if want_p:
        p = next((x for x in avail if x["id"] == want_p), None)
        if p:
            model = want_m if want_m in p["models"] else (p.get("default_model") or p["models"][0])
            return p["id"], model, p.get("label", p["id"])
    for p in avail:
        loaded = p.get("loaded") or []
        if p.get("kind") == "local" and loaded:
            return p["id"], loaded[0], p.get("label", p["id"])
    for p in avail:
        model = p.get("default_model") or p["models"][0]
        return p["id"], model, p.get("label", p["id"])
    return None


def _local_base(cfg: dict[str, Any]) -> str:
    return str(_bcfg(cfg).get("local_base_url") or "http://127.0.0.1:1234/v1").rstrip("/")


def _local_models(cfg: dict[str, Any]) -> list[dict[str, Any]] | None:
    """Chat models on the local endpoint, loaded ones first. LM Studio's native
    listing is preferred because it says which model is in memory."""
    base = _local_base(cfg)
    root = base[:-3] if base.endswith("/v1") else base

    def probe() -> list[dict[str, Any]] | None:
        for url in (f"{root}/api/v0/models", f"{base}/models"):
            try:
                r = httpx.get(url, timeout=2.5)
                r.raise_for_status()
                rows = [m for m in r.json().get("data", [])
                        if m.get("id") and m.get("type") != "embeddings"]
                if rows:
                    rows.sort(key=lambda m: m.get("state") != "loaded")
                    return [{"id": m["id"], "loaded": m.get("state") == "loaded"} for m in rows]
            except Exception:
                continue
        return None

    return _cached("local-models:" + base, 25, probe)


def _local_model(cfg: dict[str, Any]) -> str | None:
    models = _local_models(cfg)
    if not models:
        return None
    want = settings.get_settings().get("brain_local_model", "")
    if want and any(m["id"] == want for m in models):
        return want
    return models[0]["id"]


def engine_status(engine: str, cfg: dict[str, Any]) -> dict[str, Any]:
    """{id, label, ready, reason} — what the HUD shows and what the chain obeys."""
    b = _bcfg(cfg)
    out: dict[str, Any] = {"id": engine, "label": engine, "ready": False, "reason": ""}
    pen = _penalty.get(engine)
    if engine == "claude-cli":
        model = b.get("cli_model", "sonnet")
        out["label"] = f"Claude CLI · {model}"
        auth = _cli_auth()
        if not auth.get("installed"):
            out["reason"] = "claude CLI not installed"
        elif not auth.get("logged_in"):
            out["reason"] = "signed out — run `claude auth login` once"
        else:
            out["ready"] = True
    elif engine == "sdk":
        model = b.get("sdk_model", "claude-sonnet-5-5")
        out["label"] = f"Claude API · {model}"
        if os.environ.get("ANTHROPIC_API_KEY"):
            out["ready"] = True
        else:
            out["reason"] = "no ANTHROPIC_API_KEY in .env"
    elif engine == "hermes":
        out["label"] = "Hermes"
        tgt = _hermes_target(cfg)
        if tgt:
            prov = next((p for p in (_hermes_providers() or []) if p.get("id") == tgt[0]), {})
            cold = prov.get("kind") == "local" and tgt[1] not in (prov.get("loaded") or [])
            out.update(ready=True, label=f"Hermes · {tgt[1]}" + (" (cold start)" if cold else ""),
                       model=tgt[1], provider=tgt[0], cold=cold)
        elif _hermes_providers() is None:
            out["reason"] = "Hermes isn't running"
        else:
            out["reason"] = "Hermes has no available model"
    elif engine == "local":
        out["label"] = "Local model"
        model = _local_model(cfg)
        if model:
            cold = not any(m["id"] == model and m["loaded"] for m in (_local_models(cfg) or []))
            out.update(ready=True, label=f"Local · {model}" + (" (cold start)" if cold else ""),
                       model=model, cold=cold)
        else:
            out["reason"] = "no local model server (start LM Studio's server)"
    if out["ready"] and pen and pen[0] > time.time():
        out.update(ready=False, reason=pen[1])
    return out


def status(cfg: dict[str, Any]) -> dict[str, Any]:
    """Which engine will answer, and why the others can't. Honest: an engine
    counts as ready only when its real precondition holds (CLI signed in, key
    present, server reachable) — not merely because an exe exists."""
    if not _bcfg(cfg).get("enabled", True):
        return {"ready": False, "engine": None, "path": "disabled", "label": "brain disabled",
                "engines": []}
    engines = [engine_status(e, cfg) for e in chain(cfg)]
    first = next((e for e in engines if e["ready"]), None)
    return {
        "ready": first is not None,
        "engine": first["id"] if first else None,
        "path": first["id"] if first else "none",      # legacy field
        "label": first["label"] if first else "no engine available",
        "fallback": bool(first and engines and engines[0]["id"] != first["id"]),
        "engines": engines,
    }


# ── context ──────────────────────────────────────────────────────────────

def build_context(state: dict[str, Any]) -> str:
    """Compact, human-readable snapshot of the live state for the model."""
    lines: list[str] = [time.strftime("NOW: %A %Y-%m-%d %H:%M")]
    inv = state.get("inventory", {})

    rts = [r for r in inv.get("runtimes", []) if r.get("present")]
    if rts:
        lines.append("RUNTIMES: " + ", ".join(f"{r['label']} {r['version']}" for r in rts))
    tools = [t for t in inv.get("dev_tools", []) if t.get("present")]
    if tools:
        lines.append("DEV TOOLS: " + ", ".join(f"{t['label']} {t['version']}" for t in tools))
    missing = [t["label"] for t in inv.get("dev_tools", []) if not t.get("present")]
    if missing:
        lines.append("MISSING TOOLS: " + ", ".join(missing))

    conn = state.get("connectors", {})
    c = conn.get("claude", {})
    if c.get("present"):
        auth = "signed in" if (c.get("auth") or {}).get("logged_in") else "CLI signed OUT"
        lines.append(f"CLAUDE CODE: v{c.get('version')} ({auth})")
    mcp = conn.get("mcp_servers", [])
    if mcp:
        lines.append("MCP SERVERS: " + ", ".join(f"{m['name']}({m['status']})" for m in mcp))
    hc = conn.get("hermes_chat", {})
    if hc:
        provs = ", ".join(f"{p.get('label')}{'' if p.get('available') else ' (unavailable)'}"
                          for p in hc.get("providers", []))
        usage = hc.get("usage") or {}
        spend = f"; spent today ${usage.get('spent_today', 0):.4f}" if usage else ""
        lines.append(f"HERMES CHAT: {hc.get('status')}" + (f" — providers: {provs}" if provs else "") + spend)
    herm = conn.get("hermes", {})
    if herm.get("skills"):
        lines.append("HERMES SKILLS: " + ", ".join(s["name"] for s in herm["skills"]))

    ob = state.get("obsidian", {})
    if ob.get("available"):
        lines.append(f"OBSIDIAN: linked, {ob.get('notes')} notes at {ob.get('vault')}")

    sysm = inv.get("system", {})
    if sysm.get("ok"):
        mem = sysm.get("mem", {})
        host = (f"HOST: CPU {sysm.get('cpu')}% of {sysm.get('cpu_count')} threads; RAM {mem.get('pct')}% "
                f"({mem.get('used_gb')}/{mem.get('total_gb')} GB); uptime {sysm.get('uptime_h')} h")
        g = sysm.get("gpu")
        if g:
            host += (f"; GPU {g.get('name')} {g.get('util')}% load, VRAM {g.get('vram_used_gb')}/"
                     f"{g.get('vram_total_gb')} GB, {g.get('temp', '?')}°C")
        lines.append(host)
        disks = ", ".join(f"{d['mount']} {d['pct']:.0f}% used ({d['free_gb']:.0f} GB free)"
                          for d in sysm.get("disks", []))
        if disks:
            lines.append("DISKS: " + disks)

    apps = state.get("apps", [])
    if apps:
        running = [a["name"] for a in apps if a.get("running")]
        lines.append("APPS RUNNING: " + (", ".join(running) if running else "none"))

    projs = state.get("projects", [])
    if projs:
        bits = []
        for p in projs[:14]:
            tag = ""
            if p.get("git"):
                tag = f" [{p.get('branch') or 'git'}" + (f", {p['dirty_files']} uncommitted" if p.get("dirty_files") else "") \
                      + (f", {p['ahead']} unpushed" if p.get("ahead") else "") + "]"
            else:
                tag = " [no git]"
            bits.append(p["name"] + tag)
        lines.append(f"PROJECTS ({len(projs)}, most recent first): " + "; ".join(bits))

    sh = state.get("shield") or {}
    if sh.get("counts"):
        cts = sh["counts"]
        lines.append(f"BACKUP STATUS: {cts.get('protected', 0)} protected, {cts.get('unpushed', 0)} with "
                     f"unpushed work, {cts.get('local', 0)} git-but-no-remote, {cts.get('exposed', 0)} with "
                     f"no version control; {sh.get('at_risk', 0)} have no cover at all")

    autos = state.get("automations", [])
    if autos:
        lines.append("SCHEDULED TASKS: " + "; ".join(
            f"{t['name']} ({t['state']}"
            + (", LAST RUN FAILED" if t.get("failed") else "")
            + (f", next {str(t['next_run']).replace('T', ' ')[:16]}" if t.get("next_run") else "") + ")"
            for t in autos))

    ups = state.get("updates") or {}
    if ups.get("checked"):
        parts = []
        for k in ("winget", "npm", "env"):
            g = ups.get(k) or {}
            if g.get("count"):
                names = ", ".join(i.get("label") or i.get("name", "?") for i in g.get("items", [])[:6])
                parts.append(f"{g.get('label', k)}: {names}")
        lines.append("UPDATES AVAILABLE: " + ("; ".join(parts) if parts else "none"))
    else:
        lines.append("UPDATES: not checked yet")

    return "\n".join(lines)


def build_system(state: dict[str, Any], cfg: dict[str, Any], question: str,
                 with_actions: bool = True) -> str:
    honorific = cfg.get("identity", {}).get("honorific", "sir")
    system = PERSONA.format(honorific=honorific) + "\n\n=== SYSTEM STATE ===\n" + build_context(state)
    if with_actions:
        app_ids = ", ".join(a["id"] for a in state.get("apps", [])) or "none registered"
        system += "\n\n" + ACTIONS_DOC.replace("{apps}", app_ids)
    # Vault memory: local RAG over the Obsidian + Delivery vaults. Empty string
    # when disabled/unindexed/offline, so the brain works exactly as before.
    try:
        from . import rag

        vault = rag.brain_context(question)
        if vault:
            system += (
                "\n\n=== VAULT MEMORY (retrieved from the user's own notes; "
                "name the note when you draw on it) ===\n" + vault
            )
    except Exception:
        pass
    return system


def _flatten(messages: list[dict[str, str]]) -> str:
    """Conversation -> one prompt, for the CLI (which takes a single prompt)."""
    if len(messages) == 1:
        return messages[0]["content"]
    lines = ["Conversation so far:"]
    for m in messages[:-1]:
        lines.append(("User: " if m["role"] == "user" else "JARVIS: ") + m["content"])
    lines += ["", "The user's new message (reply to this):", messages[-1]["content"]]
    return "\n".join(lines)


# ── engines: each yields ("delta" | "reasoning", text) ───────────────────

def _engine_cli(system: str, messages: list[dict[str, str]], cfg: dict[str, Any]) -> Iterator[tuple[str, str]]:
    exe = which("claude")
    if not exe:
        raise EngineError("unavailable", "claude CLI not installed")
    model = _bcfg(cfg).get("cli_model", "sonnet")
    # A lean, single-purpose call: our own system prompt instead of Claude
    # Code's (which carries ~20k tokens of tool instructions), no tools, no MCP
    # servers spawned, no skills, and no session file left behind per question.
    cmd = [exe, "-p", "--model", model, "--system-prompt", system, "--tools", "",
           "--strict-mcp-config", "--disable-slash-commands", "--no-session-persistence",
           "--output-format", "stream-json", "--verbose", "--include-partial-messages"]
    try:
        proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", cwd=str(data_dir()),
            env=clean_env(), creationflags=no_window(),
        )
    except OSError as exc:
        raise EngineError("unavailable", f"could not start claude: {exc}") from exc
    killer = threading.Timer(float(_bcfg(cfg).get("timeout", 180)), proc.kill)
    killer.daemon = True
    killer.start()
    got = False
    err: str | None = None
    try:
        assert proc.stdin and proc.stdout
        try:
            # The prompt goes in on stdin: a long conversation would overflow
            # Windows' 32k command-line limit as an argument.
            proc.stdin.write(_flatten(messages))
            proc.stdin.close()
        except OSError:
            pass
        for line in proc.stdout:
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            kind = ev.get("type")
            if kind == "stream_event":
                e = ev.get("event") or {}
                if e.get("type") == "content_block_delta":
                    d = e.get("delta") or {}
                    if d.get("type") == "text_delta" and d.get("text"):
                        got = True
                        yield ("delta", d["text"])
                    elif d.get("type") == "thinking_delta":
                        yield ("reasoning", d.get("thinking") or "")
            elif kind == "assistant":
                msg = ev.get("message") or {}
                text = "".join(b.get("text", "") for b in msg.get("content", [])
                               if isinstance(b, dict) and b.get("type") == "text")
                if ev.get("error"):
                    err = text or str(ev["error"])
                elif text and not got:      # no partial events arrived
                    got = True
                    yield ("delta", text)
            elif kind == "result":
                if ev.get("is_error"):
                    err = str(ev.get("result") or err or "claude returned an error")
                elif not got and ev.get("result"):
                    got = True
                    yield ("delta", str(ev["result"]))
        proc.wait(timeout=10)
    finally:
        killer.cancel()
        if proc.poll() is None:
            proc.kill()
    if err:
        low = err.lower()
        if "not logged in" in low or "/login" in low or "authentication" in low:
            with _cache_lock:
                _cache.pop("cli-auth", None)
            raise EngineError("auth", "Claude CLI is signed out — run `claude auth login` once")
        raise EngineError("failed", err[:300])
    if not got:
        tail = (proc.stderr.read() if proc.stderr else "").strip()[-300:]
        raise EngineError("failed", tail or "claude produced no output")


def _engine_sdk(system: str, messages: list[dict[str, str]], cfg: dict[str, Any]) -> Iterator[tuple[str, str]]:
    try:
        import anthropic
    except ImportError as exc:
        raise EngineError("unavailable", "anthropic package not installed") from exc
    model = _bcfg(cfg).get("sdk_model", "claude-sonnet-5-5")
    try:
        client = anthropic.Anthropic()
        # Streaming, with room for the model to think before it answers —
        # current models reason by default, and a small cap truncates replies.
        with client.messages.stream(model=model, max_tokens=8000, system=system,
                                    messages=messages) as stream:  # type: ignore[arg-type]
            for text in stream.text_stream:
                if text:
                    yield ("delta", text)
            final = stream.get_final_message()
        if final.stop_reason == "refusal":
            yield ("delta", "I'm unable to help with that request.")
    except anthropic.AuthenticationError as exc:
        raise EngineError("auth", "Anthropic API key was rejected") from exc
    except anthropic.RateLimitError as exc:
        raise EngineError("failed", "Anthropic API rate limit reached") from exc
    except anthropic.APIConnectionError as exc:
        raise EngineError("unavailable", "couldn't reach the Anthropic API") from exc
    except anthropic.APIStatusError as exc:
        raise EngineError("failed", f"Anthropic API error {exc.status_code}: {exc.message}"[:300]) from exc


def _sse_lines(resp: httpx.Response) -> Iterator[str]:
    yield from resp.iter_lines()


def _engine_hermes(system: str, messages: list[dict[str, str]], cfg: dict[str, Any]) -> Iterator[tuple[str, str]]:
    tgt = _hermes_target(cfg)
    if not tgt:
        raise EngineError("unavailable", "Hermes has no available model")
    body = {"provider": tgt[0], "model": tgt[1], "messages": messages, "system": system,
            "temperature": 0.4, "effort": "low", "source": "jarvis"}
    try:
        with httpx.stream("POST", f"{_hermes_url()}/api/chat", json=body,
                          timeout=httpx.Timeout(600, connect=4)) as resp:
            if resp.status_code != 200:
                raise EngineError("failed", f"Hermes returned HTTP {resp.status_code}")
            for ev in iter_sse_json(_sse_lines(resp)):
                if ev.get("error"):
                    raise EngineError("failed", str(ev["error"])[:300])
                if ev.get("reasoning"):
                    yield ("reasoning", ev["reasoning"])
                if ev.get("delta"):
                    yield ("delta", ev["delta"])
    except httpx.HTTPError as exc:
        with _cache_lock:
            _cache.pop("hermes-providers", None)
        raise EngineError("unavailable", f"Hermes unreachable ({type(exc).__name__})") from exc


def _engine_local(system: str, messages: list[dict[str, str]], cfg: dict[str, Any]) -> Iterator[tuple[str, str]]:
    model = _local_model(cfg)
    if not model:
        raise EngineError("unavailable", "no local model server")
    body = {"model": model, "stream": True, "temperature": 0.4,
            "messages": [{"role": "system", "content": system}, *messages]}
    splitter = ThinkSplitter()
    try:
        with httpx.stream("POST", f"{_local_base(cfg)}/chat/completions", json=body,
                          timeout=httpx.Timeout(600, connect=4)) as resp:
            if resp.status_code != 200:
                detail = resp.read().decode("utf-8", "replace")[:200]
                raise EngineError("failed", f"local model returned HTTP {resp.status_code} {detail}")
            for ev in iter_sse_json(_sse_lines(resp)):
                if ev.get("error"):
                    msg = ev["error"].get("message") if isinstance(ev["error"], dict) else ev["error"]
                    raise EngineError("failed", str(msg)[:300])
                for choice in ev.get("choices") or []:
                    d = choice.get("delta") or {}
                    r = d.get("reasoning_content") or d.get("reasoning")
                    if isinstance(r, str) and r:
                        yield ("reasoning", r)
                    c = d.get("content")
                    if isinstance(c, str) and c:
                        for kind, text in splitter.feed(c):
                            yield ("reasoning" if kind == "reasoning" else "delta", text)
            for kind, text in splitter.flush():
                yield ("reasoning" if kind == "reasoning" else "delta", text)
    except httpx.HTTPError as exc:
        raise EngineError("unavailable", f"local model unreachable ({type(exc).__name__})") from exc


_RUN: dict[str, Callable[..., Iterator[tuple[str, str]]]] = {
    "claude-cli": _engine_cli, "sdk": _engine_sdk, "hermes": _engine_hermes, "local": _engine_local,
}


def _generate(system: str, messages: list[dict[str, str]], cfg: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Walk the chain. Yields {t: start|reasoning|delta|fallback} events and,
    last, exactly one {t: end, engine, source} or {t: error}."""
    problems: list[str] = []
    for engine in chain(cfg):
        st = engine_status(engine, cfg)
        if not st["ready"]:
            problems.append(f"{st['label']}: {st['reason']}")
            continue
        yield {"t": "start", "engine": engine, "label": st["label"]}
        spoke = False
        thinking = False
        try:
            for kind, text in _RUN[engine](system, messages, cfg):
                if kind == "reasoning":
                    if not thinking:
                        thinking = True
                        yield {"t": "reasoning"}
                    continue
                if text:
                    spoke = True
                    yield {"t": "delta", "d": text}
        except EngineError as exc:
            if spoke:
                yield {"t": "error", "error": f"{st['label']} stopped mid-answer — {exc}"}
                return
            # Bench the engine briefly so the next questions don't each pay
            # for the same failure; auth problems last until they're fixed.
            _penalty[engine] = (time.time() + (300 if exc.kind == "auth" else 45), str(exc))
            problems.append(f"{st['label']}: {exc}")
            yield {"t": "fallback", "engine": engine, "label": st["label"], "reason": str(exc)}
            continue
        if not spoke:
            problems.append(f"{st['label']}: empty reply")
            yield {"t": "fallback", "engine": engine, "label": st["label"], "reason": "empty reply"}
            continue
        _penalty.pop(engine, None)
        yield {"t": "end", "engine": engine, "source": st["label"]}
        return
    yield {"t": "error",
           "error": "No brain engine could answer — " + ("; ".join(problems) or "none configured")}


# ── public API ───────────────────────────────────────────────────────────

def ask_stream(question: str, history: list[dict[str, str]] | None, state: dict[str, Any],
               cfg: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Answer `question`, streaming events:

      {t:"start", engine, label}      an engine picked up the question
      {t:"reasoning"}                 the model is thinking (shown as a pulse)
      {t:"delta", d}                  visible answer text
      {t:"fallback", engine, reason}  an engine failed; trying the next
      {t:"action", name, arg, result} an action ran
      {t:"done", answer, source, ms}  finished
      {t:"error", error}
    """
    from . import actions

    t0 = time.time()
    if not _bcfg(cfg).get("enabled", True):
        yield {"t": "error", "error": "The brain is disabled in config.toml."}
        return
    question = (question or "").strip()
    honorific = cfg.get("identity", {}).get("honorific", "sir")

    # 1. Plain commands ("open QuantFin", "remind me in 20 minutes…") run
    #    instantly — no model round-trip, and they work with no engine at all.
    hit = actions.match_intent(question, state)
    if hit:
        res = actions.execute(hit[0], hit[1], state, honorific)
        yield {"t": "start", "engine": "instant", "label": "instant"}
        yield {"t": "delta", "d": res.get("say", "Done.")}
        yield {"t": "action", "name": hit[0], "arg": hit[1], "result": res}
        yield {"t": "done", "answer": res.get("say", "Done."), "source": "instant",
               "ok": bool(res.get("ok")), "ms": int((time.time() - t0) * 1000)}
        return

    # 2. The engine chain.
    msgs = [{"role": m["role"], "content": str(m.get("content", ""))[:6000]}
            for m in (history or [])[-12:]
            if m.get("role") in ("user", "assistant") and m.get("content")]
    while msgs and msgs[0]["role"] != "user":      # must open with a user turn
        msgs.pop(0)
    msgs.append({"role": "user", "content": question})
    system = build_system(state, cfg, question)

    filt = DirectiveFilter()
    parts: list[str] = []
    source = ""
    for ev in _generate(system, msgs, cfg):
        if ev["t"] == "delta":
            vis = filt.feed(ev["d"])
            if vis:
                parts.append(vis)
                yield {"t": "delta", "d": vis}
        elif ev["t"] == "end":
            source = ev["source"]
        elif ev["t"] == "error":
            yield ev
            return
        else:
            if ev["t"] == "fallback":      # a failed engine's partial text is void
                filt, parts = DirectiveFilter(), []
            yield ev
    tail = filt.flush()
    if tail:
        parts.append(tail)
        yield {"t": "delta", "d": tail}

    answer = "".join(parts).strip()
    for name, arg in filt.found[:4]:
        res = actions.execute(name, arg, state, honorific)
        yield {"t": "action", "name": name, "arg": arg, "result": res}
        if not answer and res.get("say"):
            answer = res["say"]
            yield {"t": "delta", "d": answer}
        elif not res.get("ok") and res.get("say"):
            extra = "\n\n" + res["say"]
            answer += extra
            yield {"t": "delta", "d": extra}
    yield {"t": "done", "answer": answer, "source": source, "ok": True,
           "ms": int((time.time() - t0) * 1000)}


def ask(question: str, state: dict[str, Any], cfg: dict[str, Any],
        history: list[dict[str, str]] | None = None) -> dict[str, Any]:
    """Non-streaming ask (the old /api/ask contract): {ok, answer, source}."""
    answer, source, acts = "", "", []
    for ev in ask_stream(question, history, state, cfg):
        if ev["t"] == "done":
            return {"ok": ev.get("ok", True), "answer": ev["answer"], "source": ev["source"],
                    "actions": acts, "ms": ev["ms"]}
        if ev["t"] == "error":
            return {"ok": False, "answer": ev["error"], "source": "error", "actions": acts}
        if ev["t"] == "action":
            acts.append({"name": ev["name"], "arg": ev["arg"], "result": ev["result"]})
        if ev["t"] == "delta":
            answer += ev["d"]
    return {"ok": bool(answer), "answer": answer or "No answer.", "source": source, "actions": acts}


def ping(cfg: dict[str, Any]) -> dict[str, Any]:
    """A real, tiny round-trip through the chain: proves an engine can answer
    and says which one did (and what was skipped on the way)."""
    forget_probes()
    t0 = time.time()
    text, source, engine, skipped = "", "", None, []
    system = "You are JARVIS. Reply with exactly one word: ONLINE."
    for ev in _generate(system, [{"role": "user", "content": "Are you online?"}], cfg):
        if ev["t"] == "delta":
            text += ev["d"]
        elif ev["t"] == "fallback":
            skipped.append(f"{ev['label']}: {ev['reason']}")
        elif ev["t"] == "end":
            source, engine = ev["source"], ev["engine"]
        elif ev["t"] == "error":
            return {"ok": False, "answer": ev["error"], "source": "error", "path": "none",
                    "skipped": skipped, "ms": int((time.time() - t0) * 1000)}
    return {"ok": bool(text.strip()), "answer": text.strip(), "source": source, "path": engine,
            "engine": engine, "skipped": skipped, "ms": int((time.time() - t0) * 1000)}
