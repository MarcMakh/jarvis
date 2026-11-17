"""Runtime settings edited from the HUD (no restart, no config.toml editing).

config.toml stays the source of defaults; anything changed in the ⚙ panel is
saved to data/settings.json and wins over it.
"""

from __future__ import annotations

import threading
from typing import Any

from .util import data_dir, load_config, read_json, write_json_atomic

_lock = threading.Lock()

# key -> (default, allowed values or None for free text)
_SCHEMA: dict[str, tuple[Any, tuple[str, ...] | None]] = {
    # which engine answers "ask JARVIS": "auto" walks [brain].chain
    "brain_engine": ("auto", ("auto", "claude-cli", "sdk", "hermes", "local")),
    "brain_hermes_provider": ("", None),
    "brain_hermes_model": ("", None),
    "brain_local_model": ("", None),
    # which alerts JARVIS says out loud
    "speak_alerts": ("critical", ("none", "critical", "all")),
    "greet_on_start": (True, None),
}


def _path():
    return data_dir() / "settings.json"


def _config_defaults() -> dict[str, Any]:
    cfg = load_config()
    b = cfg.get("brain", {}) or {}
    v = cfg.get("voice", {}) or {}
    a = cfg.get("alerts", {}) or {}
    out = {k: d for k, (d, _) in _SCHEMA.items()}
    out["brain_hermes_provider"] = str(b.get("hermes_provider", "") or "")
    out["brain_hermes_model"] = str(b.get("hermes_model", "") or "")
    out["brain_local_model"] = str(b.get("local_model", "") or "")
    out["speak_alerts"] = str(a.get("speak", "critical"))
    out["greet_on_start"] = bool(v.get("greet_on_start", True))
    return out


def get_settings() -> dict[str, Any]:
    out = _config_defaults()
    saved = read_json(_path(), {})
    if isinstance(saved, dict):
        for k, val in saved.items():
            if k in _SCHEMA and val is not None:
                out[k] = val
        out["muted_alerts"] = [str(x) for x in saved.get("muted_alerts", [])][:200]
    else:
        out["muted_alerts"] = []
    out.setdefault("muted_alerts", [])
    return out


def set_settings(patch: dict[str, Any]) -> dict[str, Any]:
    """Persist known keys only; anything unknown or invalid is ignored."""
    with _lock:
        saved = read_json(_path(), {})
        if not isinstance(saved, dict):
            saved = {}
        for k, val in (patch or {}).items():
            if k == "muted_alerts" and isinstance(val, list):
                saved[k] = sorted({str(x) for x in val})[:200]
                continue
            spec = _SCHEMA.get(k)
            if spec is None:
                continue
            default, allowed = spec
            if isinstance(default, bool):
                saved[k] = bool(val)
            else:
                sval = str(val or "").strip()
                if allowed is not None and sval not in allowed:
                    continue
                saved[k] = sval
        write_json_atomic(_path(), saved, indent=1)
    return get_settings()


def mute_alert(key: str, muted: bool = True) -> dict[str, Any]:
    cur = set(get_settings().get("muted_alerts", []))
    if muted:
        cur.add(key)
    else:
        cur.discard(key)
    return set_settings({"muted_alerts": sorted(cur)})
