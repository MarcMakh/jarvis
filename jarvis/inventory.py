"""Scans the machine for runtimes, dev tools, Python packages, and live system stats."""

from __future__ import annotations

import re
import sys
from typing import Any

from .util import first_line, run, which

# Tools JARVIS knows how to version-probe. Each entry: (label, exe, version-args).
_DEV_TOOLS: list[tuple[str, str, list[str]]] = [
    ("Git", "git", ["--version"]),
    ("GitHub CLI", "gh", ["--version"]),
    ("ripgrep", "rg", ["--version"]),
    ("Pandoc", "pandoc", ["--version"]),
    ("ruff", "ruff", ["--version"]),
    ("Pyright", "pyright", ["--version"]),
    ("VS Code", "code", ["--version"]),
    ("Docker", "docker", ["--version"]),
]

# Python packages worth surfacing (installed status + version).
_KEY_PACKAGES = [
    "anthropic",
    "edge-tts",
    "fastapi",
    "uvicorn",
    "psutil",
    "httpx",
    "pydantic",
    "requests",
    "rich",
    "openai",
    "mcp",
]

_VERSION_RE = re.compile(r"(\d+\.\d+(?:\.\d+)?(?:[.\-]\w+)*)")


def _ver_from(text: str) -> str:
    m = _VERSION_RE.search(text)
    return m.group(1) if m else first_line(text)


def _probe(label: str, exe: str, args: list[str]) -> dict[str, Any]:
    path = which(exe)
    if not path:
        return {"label": label, "name": exe, "present": False, "version": None, "path": None}
    res = run([path, *args], timeout=12)
    return {
        "label": label,
        "name": exe,
        "present": True,
        "version": _ver_from(res.text) if res.ok else None,
        "path": path,
    }


def scan_runtimes() -> list[dict[str, Any]]:
    """Python interpreters, uv, Node, npm."""
    items: list[dict[str, Any]] = []

    # Active interpreter (the one running JARVIS)
    items.append(
        {
            "label": "Python (active)",
            "name": "python",
            "present": True,
            "version": "%d.%d.%d" % sys.version_info[:3],
            "path": sys.executable,
        }
    )

    # All Python versions known to the py launcher
    py = which("py")
    if py:
        res = run([py, "--list"], timeout=10)
        for line in res.out.splitlines():
            m = re.search(r"-V:(\S+)\s+\*?\s*(.*)", line.strip())
            if m:
                items.append(
                    {
                        "label": f"Python {m.group(1)}",
                        "name": "py",
                        "present": True,
                        "version": _ver_from(m.group(2)) or m.group(1),
                        "path": "py launcher",
                    }
                )

    # Version probes are independent subprocess spawns — run them concurrently.
    from concurrent.futures import ThreadPoolExecutor

    tools = [
        ("uv", "uv", ["--version"]),
        ("Node.js", "node", ["--version"]),
        ("npm", "npm", ["--version"]),
        ("pip", "pip", ["--version"]),
    ]
    with ThreadPoolExecutor(max_workers=len(tools)) as ex:
        items.extend(ex.map(lambda t: _probe(*t), tools))

    return items


def scan_dev_tools() -> list[dict[str, Any]]:
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=len(_DEV_TOOLS)) as ex:
        return list(ex.map(lambda t: _probe(*t), _DEV_TOOLS))


def scan_packages() -> list[dict[str, Any]]:
    """Installed versions of key Python packages, via importlib.metadata."""
    from importlib import metadata

    out: list[dict[str, Any]] = []
    for name in _KEY_PACKAGES:
        try:
            ver = metadata.version(name)
            out.append({"name": name, "present": True, "version": ver})
        except metadata.PackageNotFoundError:
            out.append({"name": name, "present": False, "version": None})
    return out


def system_stats() -> dict[str, Any]:
    """Live CPU / memory / GPU / disk reading (see telemetry.py). The sampler
    measures in the background, so this never blocks a request the way the old
    `cpu_percent(interval=0.15)` call did — and it no longer reads JARVIS's own
    scan burst as "CPU load"."""
    try:
        from .telemetry import sampler

        return sampler.snapshot()
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def collect() -> dict[str, Any]:
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=4) as ex:
        f_rt = ex.submit(scan_runtimes)
        f_dt = ex.submit(scan_dev_tools)
        f_pk = ex.submit(scan_packages)
        f_sys = ex.submit(system_stats)
        return {
            "runtimes": f_rt.result(),
            "dev_tools": f_dt.result(),
            "packages": f_pk.result(),
            "system": f_sys.result(),
        }
