"""Shared helpers: config loading, subprocess execution, executable resolution."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.toml"

# Locations to search for CLIs that may not be on the spawned process's PATH
# (Claude Code's native install lands in ~/.local/bin, which often isn't on PATH).
_EXTRA_BIN_DIRS = [
    Path.home() / ".local" / "bin",
    Path.home() / "AppData" / "Roaming" / "npm",
    Path.home() / "AppData" / "Roaming" / "Python" / "Python314" / "Scripts",
    Path.home() / "AppData" / "Local" / "Microsoft" / "WindowsApps",
]

_EXE_SUFFIXES = ["", ".exe", ".cmd", ".ps1", ".bat"]


def load_config() -> dict[str, Any]:
    """Load config.toml, tolerating a missing file with sane defaults."""
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "rb") as fh:
            return tomllib.load(fh)
    return {}


def load_env() -> None:
    """Load KEY=VALUE pairs from a project-root .env into os.environ (without
    overriding values already set). Lets you drop ANTHROPIC_API_KEY in a file
    instead of setting a system environment variable."""
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


def which(name: str) -> str | None:
    """Resolve an executable by name, including JARVIS's extra bin dirs."""
    found = shutil.which(name)
    if found:
        return found
    for d in _EXTRA_BIN_DIRS:
        for suffix in _EXE_SUFFIXES:
            candidate = d / f"{name}{suffix}"
            if candidate.exists():
                return str(candidate)
    return None


@dataclass
class CmdResult:
    ok: bool
    code: int
    out: str
    err: str

    @property
    def text(self) -> str:
        return (self.out or self.err).strip()


def run(
    args: list[str] | str,
    timeout: float = 25.0,
    cwd: str | os.PathLike[str] | None = None,
) -> CmdResult:
    """Run a command, capturing output. Never raises; returns a CmdResult.

    A list is run without a shell (safe). A string is run via the shell, which
    is only used internally for trusted, hard-coded commands.
    """
    shell = isinstance(args, str)
    try:
        proc = subprocess.run(
            args,
            shell=shell,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
            encoding="utf-8",
            errors="replace",
            # Detach stdin so CLIs that probe it (e.g. `claude -p`) don't stall
            # for several seconds waiting on input that will never arrive.
            stdin=subprocess.DEVNULL,
            creationflags=_no_window(),
        )
        return CmdResult(proc.returncode == 0, proc.returncode, proc.stdout or "", proc.stderr or "")
    except FileNotFoundError:
        return CmdResult(False, 127, "", "executable not found")
    except subprocess.TimeoutExpired:
        return CmdResult(False, 124, "", f"timed out after {timeout}s")
    except Exception as exc:  # pragma: no cover - defensive
        return CmdResult(False, 1, "", str(exc))


def _no_window() -> int:
    """Avoid flashing console windows when spawning on Windows."""
    if sys.platform == "win32":
        return getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return 0


def first_line(text: str) -> str:
    for line in text.splitlines():
        line = line.strip()
        if line:
            return line
    return ""
