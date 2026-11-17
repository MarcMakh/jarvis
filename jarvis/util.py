"""Shared helpers: config loading, subprocess execution, executable resolution,
path guards and atomic file writes.

Three environment overrides exist so a second instance (tests, dev) can run
beside the real one without touching its files:

  JARVIS_CONFIG    path to an alternate config.toml
  JARVIS_DATA_DIR  alternate data directory (caches, index, alerts, settings)
  JARVIS_PORT      alternate port

`python -m jarvis --config X --data-dir Y --port N` sets the same three.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import tomllib
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent

# Locations to search for CLIs that may not be on the spawned process's PATH
# (Claude Code's native install lands in ~/.local/bin, which often isn't on PATH).
_EXTRA_BIN_DIRS = [
    Path.home() / ".local" / "bin",
    Path.home() / "AppData" / "Roaming" / "npm",
    Path.home() / "AppData" / "Roaming" / "Python" / "Python314" / "Scripts",
    Path.home() / "AppData" / "Local" / "Microsoft" / "WindowsApps",
]

_EXE_SUFFIXES = ["", ".exe", ".cmd", ".ps1", ".bat"]


def config_path() -> Path:
    return Path(os.environ.get("JARVIS_CONFIG") or (ROOT / "config.toml"))


def data_dir() -> Path:
    """Where JARVIS keeps local state. Read lazily (never at import time) so
    the --data-dir flag can redirect it before anything is written."""
    d = Path(os.environ.get("JARVIS_DATA_DIR") or (ROOT / "data"))
    d.mkdir(parents=True, exist_ok=True)
    return d


_cfg_lock = threading.Lock()
_cfg_cache: dict[str, Any] = {"key": None, "data": {}, "error": None}


def load_config() -> dict[str, Any]:
    """Load config.toml. Parsed once and re-read only when the file changes
    (this is called on nearly every request). A file that fails to parse —
    e.g. caught mid-edit — keeps the last good config instead of taking the
    whole HUD down; `config_error()` reports the problem.

    The returned dict is shared: treat it as read-only."""
    path = config_path()
    try:
        st = path.stat()
    except OSError:
        return {}
    key = (str(path), st.st_mtime_ns, st.st_size)
    with _cfg_lock:
        if _cfg_cache["key"] != key:
            try:
                with open(path, "rb") as fh:
                    _cfg_cache["data"] = tomllib.load(fh)
                _cfg_cache["error"] = None
            except Exception as exc:  # keep the last good config
                _cfg_cache["error"] = f"{path.name}: {exc}"
            _cfg_cache["key"] = key
        return _cfg_cache["data"]


def config_error() -> str | None:
    load_config()
    return _cfg_cache["error"]


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
        if key and val and key not in os.environ:
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


# Variables a Claude Code / agent-SDK host injects into its children. A `claude`
# CLI spawned with them inherits the *host session's* auth plumbing instead of
# the user's own login, so JARVIS strips them before calling the CLI — the
# brain then behaves the same however JARVIS itself was launched.
_HOST_ENV_PREFIXES = ("CLAUDE_CODE_", "CLAUDE_AGENT_SDK_", "CLAUDE_PREVIEW_", "MCP_")
_HOST_ENV_NAMES = {"CLAUDECODE", "CLAUDE_PID", "ANTHROPIC_BASE_URL"}


def clean_env() -> dict[str, str]:
    return {
        k: v
        for k, v in os.environ.items()
        if k not in _HOST_ENV_NAMES and not k.startswith(_HOST_ENV_PREFIXES)
    }


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
    env: dict[str, str] | None = None,
    stdin_text: str | None = None,
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
            env=env,
            encoding="utf-8",
            errors="replace",
            # Detach stdin so CLIs that probe it (e.g. `claude -p`) don't stall
            # for several seconds waiting on input that will never arrive.
            input=stdin_text,
            stdin=None if stdin_text is not None else subprocess.DEVNULL,
            creationflags=no_window(),
        )
        return CmdResult(proc.returncode == 0, proc.returncode, proc.stdout or "", proc.stderr or "")
    except FileNotFoundError:
        return CmdResult(False, 127, "", "executable not found")
    except subprocess.TimeoutExpired:
        return CmdResult(False, 124, "", f"timed out after {timeout}s")
    except Exception as exc:  # pragma: no cover - defensive
        return CmdResult(False, 1, "", str(exc))


def no_window() -> int:
    """Avoid flashing console windows when spawning on Windows."""
    if sys.platform == "win32":
        return getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return 0


_no_window = no_window  # old name, still imported elsewhere


def first_line(text: str) -> str:
    for line in text.splitlines():
        line = line.strip()
        if line:
            return line
    return ""


def is_under(path: str | os.PathLike[str], roots: Iterable[str | os.PathLike[str]]) -> bool:
    """True when `path` resolves to somewhere inside one of `roots`.

    Compares resolved path *components*, so a sibling such as
    `…\\PythonProjects-old` is not mistaken for `…\\PythonProjects` (a plain
    string-prefix test is) and `..` segments can't climb out."""
    try:
        rp = Path(path).resolve()
    except (OSError, ValueError):
        return False
    for root in roots:
        try:
            if rp.is_relative_to(Path(root).resolve()):
                return True
        except (OSError, ValueError):
            continue
    return False


def project_roots() -> list[Path]:
    """Folders JARVIS treats as "yours": projects are discovered here and only
    paths inside them may be opened, run or snapshotted."""
    cfg = (load_config().get("projects", {}) or {}).get("roots")
    if cfg:
        roots = [Path(r) for r in cfg]
    else:
        roots = [Path.home() / "Documents" / "PythonProjects", Path.home() / "source" / "repos"]
    return [r for r in roots if r.exists()]


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json_atomic(path: Path, obj: Any, **dump_kwargs: Any) -> None:
    """Write JSON via a temp file + rename, so a crash or power cut mid-write
    can never leave a half-written (corrupt) file behind."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        tmp.write_text(json.dumps(obj, **dump_kwargs), encoding="utf-8")
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
