"""Automations: the Windows scheduled tasks that belong to your projects.

Nightly refreshes and trading runs fire from Task Scheduler with nobody
watching — when one fails (or sits disabled) there is normally no sign of it
until the data is noticeably stale. JARVIS lists the tasks that are *yours*
with their state, last result and next run, and the alert watchdog raises a
flag when a last run failed.

"Yours" = a task outside \\Microsoft\\ whose action points into a project root,
or whose name mentions a project. Vendor updaters (Adobe, NVIDIA, OneDrive…)
are ignored. Tune with:

    [automations]
    include = ["FanControl"]     # extra task names (substring match)
    exclude = ["Some Task"]
"""

from __future__ import annotations

import json
import sys
from typing import Any

from .util import load_config, project_roots, run, which

# One PowerShell process returns everything as JSON. Dates are emitted as ISO
# strings (ConvertTo-Json's default /Date(…)/ form is awkward to consume).
_PS = r"""
$ErrorActionPreference = 'SilentlyContinue'
$out = foreach ($t in Get-ScheduledTask | Where-Object { $_.TaskPath -notlike '\Microsoft\*' }) {
  $i = $t | Get-ScheduledTaskInfo
  $a = $t.Actions | Select-Object -First 1
  [pscustomobject]@{
    name = $t.TaskName; path = $t.TaskPath; state = [string]$t.State
    last = if ($i.LastRunTime -and $i.LastRunTime.Year -gt 2000) { $i.LastRunTime.ToString('s') } else { $null }
    result = $i.LastTaskResult
    next = if ($i.NextRunTime) { $i.NextRunTime.ToString('s') } else { $null }
    missed = $i.NumberOfMissedRuns
    exe = $a.Execute; args = $a.Arguments; cwd = $a.WorkingDirectory
  }
}
ConvertTo-Json -InputObject @($out) -Compress -Depth 3
"""

# Task Scheduler result codes that are not failures.
_OK_RESULTS = {
    0,           # success
    267009,      # 0x41301 task is currently running
    267011,      # 0x41303 task has not yet run
    267014,      # 0x41306 task was terminated by the user
}


def scan() -> list[dict[str, Any]]:
    if sys.platform != "win32":
        return []
    ps = which("powershell") or "powershell"
    res = run([ps, "-NoProfile", "-NonInteractive", "-Command", _PS], timeout=45)
    try:
        rows = json.loads(res.out.strip() or "[]")
    except Exception:
        return []
    if isinstance(rows, dict):
        rows = [rows]

    cfg = load_config().get("automations", {}) or {}
    include = [str(s).lower() for s in cfg.get("include", [])]
    exclude = [str(s).lower() for s in cfg.get("exclude", [])]
    roots = [str(r).lower() for r in project_roots()]
    names: list[str] = []
    for r in project_roots():
        try:
            names += [p.name.lower() for p in r.iterdir() if p.is_dir() and len(p.name) >= 4]
        except OSError:
            continue

    out: list[dict[str, Any]] = []
    for t in rows:
        name = str(t.get("name") or "")
        low = name.lower()
        blob = " ".join(str(t.get(k) or "") for k in ("exe", "args", "cwd")).lower()
        mine = (any(r in blob for r in roots)
                or any(n in low.replace(" ", "") or n in low for n in names)
                or any(s in low for s in include))
        if not mine or any(s in low for s in exclude):
            continue
        result = t.get("result")
        state = str(t.get("state") or "")
        failed = (result is not None and int(result) not in _OK_RESULTS
                  and state.lower() != "disabled" and bool(t.get("last")))
        out.append({
            "name": name,
            "state": state,                       # Ready | Running | Disabled | Queued
            "enabled": state.lower() != "disabled",
            "last_run": t.get("last"),
            "last_result": result,
            "failed": failed,
            "next_run": t.get("next") if state.lower() != "disabled" else None,
            "missed": int(t.get("missed") or 0),
            "command": " ".join(x for x in (str(t.get("exe") or ""), str(t.get("args") or "")) if x)[:300],
        })
    out.sort(key=lambda r: (not r["enabled"], r["name"].lower()))
    return out
