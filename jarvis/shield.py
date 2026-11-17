"""Shield: how safe is each project's work — and one-click snapshots.

A project whose only copy is the working folder on this disk is one bad
overwrite away from being gone. Shield grades every project:

  protected  git + remote, clean, nothing unpushed — a full copy lives elsewhere
  unpushed   git + remote, but uncommitted and/or unpushed work exists locally
  local      git, no remote — history exists only on this disk
  exposed    no version control at all

…and can snapshot any project to a timestamped zip (source + config + small
data files; virtualenvs, node_modules, caches and oversized binaries are left
out and listed in the archive's _SNAPSHOT.txt). Snapshots are never restored
automatically — they are plain zips you can open yourself.

    [shield]
    dir = 'F:\\Backups\\JARVIS'   # default: Documents\\JARVIS Backups
    keep = 5                      # snapshots kept per project
    max_file_mb = 50              # larger files are skipped (and listed)
    max_total_mb = 2000           # a snapshot bigger than this is abandoned
    fresh_days = 7                # a snapshot newer than this counts as cover
    include_git = false           # also archive the .git folder
"""

from __future__ import annotations

import os
import queue
import threading
import time
import zipfile
from pathlib import Path
from typing import Any

from .util import is_under, load_config, project_roots

_EXCLUDE_DIRS = {
    ".venv", "venv", "env", "node_modules", "__pycache__", ".ruff_cache", ".pytest_cache",
    ".mypy_cache", ".tox", ".next", ".idea", ".vs", ".playwright-mcp", "site-packages",
    # regenerable local state inside JARVIS/Hermes-style apps
    "app_profile", "whisper", "voice_cache", "icon_cache", "docs_md",
}

_lock = threading.Lock()
_jobs: dict[str, dict[str, Any]] = {}       # project path -> progress
_queue: "queue.Queue[str]" = queue.Queue()
_worker: threading.Thread | None = None


def _cfg() -> dict[str, Any]:
    c = load_config().get("shield", {}) or {}
    return {
        "dir": Path(c.get("dir") or (Path.home() / "Documents" / "JARVIS Backups")),
        "keep": max(1, int(c.get("keep", 5))),
        "max_file_mb": float(c.get("max_file_mb", 50)),
        "max_total_mb": float(c.get("max_total_mb", 2000)),
        "fresh_days": float(c.get("fresh_days", 7)),
        "include_git": bool(c.get("include_git", False)),
    }


def backup_dir() -> Path:
    return _cfg()["dir"]


def _snapshots(name: str, cfg: dict[str, Any] | None = None) -> list[Path]:
    """This project's snapshot zips, newest first."""
    folder = (cfg or _cfg())["dir"] / name
    try:
        zips = [p for p in folder.glob(f"{name}-*.zip") if p.is_file()]
    except OSError:
        return []
    return sorted(zips, key=lambda p: p.stat().st_mtime, reverse=True)


def assess(projects: list[dict[str, Any]]) -> dict[str, Any]:
    """Grade every project (the "shield" state section)."""
    cfg = _cfg()
    now = time.time()
    rows: list[dict[str, Any]] = []
    for p in projects:
        dirty = int(p.get("dirty_files") or 0)
        ahead = int(p.get("ahead") or 0)
        if not p.get("git"):
            level, why = "exposed", "no version control"
        elif not p.get("remote"):
            level = "local"
            why = "git, but no remote" + (f" · {dirty} uncommitted" if dirty else "")
        elif dirty or ahead:
            level = "unpushed"
            bits = []
            if dirty:
                bits.append(f"{dirty} uncommitted")
            if ahead:
                bits.append(f"{ahead} unpushed")
            why = " · ".join(bits)
        else:
            level, why = "protected", "committed and pushed"
        snaps = _snapshots(p["name"], cfg)
        last = snaps[0].stat().st_mtime if snaps else None
        fresh = bool(last and (now - last) < cfg["fresh_days"] * 86400)
        # Work that changed after the newest snapshot isn't covered by it.
        if fresh and last and float(p.get("mtime") or 0) > last + 60 and level != "protected":
            fresh = False
        rows.append({
            "name": p["name"], "path": p["path"], "level": level, "why": why,
            "last_snapshot": last, "snapshots": len(snaps), "covered": level == "protected" or fresh,
            "size_mb": round(snaps[0].stat().st_size / 1e6, 1) if snaps else None,
        })
    order = {"exposed": 0, "local": 1, "unpushed": 2, "protected": 3}
    rows.sort(key=lambda r: (r["covered"], order[r["level"]], r["name"].lower()))
    with _lock:
        jobs = {k: dict(v) for k, v in _jobs.items()}
    return {
        "dir": str(cfg["dir"]),
        "projects": rows,
        "counts": {lvl: sum(1 for r in rows if r["level"] == lvl) for lvl in order},
        "at_risk": sum(1 for r in rows if not r["covered"]),
        "jobs": jobs,
        "busy": any(j["state"] in ("queued", "running") for j in jobs.values()),
    }


# ── snapshots ────────────────────────────────────────────────────────────

def _zip_project(src: Path, cfg: dict[str, Any], job: dict[str, Any]) -> Path:
    dest_dir = cfg["dir"] / src.name
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    final = dest_dir / f"{src.name}-{stamp}.zip"
    part = final.with_suffix(".zip.part")
    max_file = cfg["max_file_mb"] * 1e6
    max_total = cfg["max_total_mb"] * 1e6
    exclude = set(_EXCLUDE_DIRS)
    if not cfg["include_git"]:
        exclude.add(".git")
    skipped: list[str] = []
    total = 0
    try:
        with zipfile.ZipFile(part, "w", zipfile.ZIP_DEFLATED, compresslevel=5) as zf:
            for dirpath, dirnames, filenames in os.walk(src):
                dirnames[:] = [d for d in dirnames if d not in exclude]
                for fn in filenames:
                    f = Path(dirpath) / fn
                    rel = f.relative_to(src)
                    try:
                        size = f.stat().st_size
                    except OSError:
                        continue
                    if size > max_file:
                        skipped.append(f"{rel}  ({size / 1e6:.0f} MB — over the per-file limit)")
                        continue
                    if total + size > max_total:
                        raise RuntimeError(
                            f"snapshot would exceed {cfg['max_total_mb']:.0f} MB — raise "
                            "[shield].max_total_mb or move bulky data out of the project")
                    try:
                        zf.write(f, str(rel))
                    except (OSError, ValueError):
                        skipped.append(f"{rel}  (unreadable / locked)")
                        continue
                    total += size
                    job["files"] += 1
                    job["bytes"] = total
            note = [
                f"JARVIS snapshot of {src}",
                f"taken {time.strftime('%Y-%m-%d %H:%M:%S')}",
                f"{job['files']} files, {total / 1e6:.1f} MB before compression",
                "",
                "Left out on purpose: " + ", ".join(sorted(exclude)),
            ]
            if skipped:
                note += ["", f"Skipped files ({len(skipped)}):", *skipped]
            zf.writestr("_SNAPSHOT.txt", "\n".join(note) + "\n")
        os.replace(part, final)
    finally:
        if part.exists():
            try:
                part.unlink()
            except OSError:
                pass
    for old in _snapshots(src.name, cfg)[cfg["keep"]:]:
        try:
            old.unlink()
        except OSError:
            pass
    job["skipped"] = len(skipped)
    return final


def _work() -> None:
    while True:
        try:
            path = _queue.get(timeout=30)
        except queue.Empty:
            return  # idle: let the thread end; the next snapshot starts a new one
        job = _jobs[path]
        job.update(state="running", started=time.time())
        try:
            out = _zip_project(Path(path), _cfg(), job)
            job.update(state="done", zip=str(out), size_mb=round(out.stat().st_size / 1e6, 1),
                       finished=time.time())
        except Exception as exc:
            job.update(state="failed", error=str(exc), finished=time.time())


def snapshot(path: str) -> dict[str, Any]:
    """Queue a snapshot of one project (must live inside a project root)."""
    global _worker
    p = Path(path)
    if not path or not p.is_dir() or not is_under(p, project_roots()):
        return {"ok": False, "error": "not a project folder"}
    key = str(p)
    with _lock:
        cur = _jobs.get(key)
        if cur and cur["state"] in ("queued", "running"):
            return {"ok": True, "already": True, "name": p.name}
        _jobs[key] = {"name": p.name, "state": "queued", "files": 0, "bytes": 0,
                      "queued": time.time()}
        _queue.put(key)
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_work, name="shield", daemon=True)
            _worker.start()
    return {"ok": True, "queued": True, "name": p.name}


def snapshot_at_risk(projects: list[dict[str, Any]]) -> dict[str, Any]:
    """Queue a snapshot for every project that isn't currently covered."""
    report = assess(projects)
    names = []
    for row in report["projects"]:
        if not row["covered"] and snapshot(row["path"]).get("ok"):
            names.append(row["name"])
    return {"ok": True, "queued": names}


def jobs_done_since(ts: float) -> list[dict[str, Any]]:
    with _lock:
        return [dict(j) for j in _jobs.values()
                if j.get("finished") and j["finished"] > ts]
