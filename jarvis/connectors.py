"""Detects and reports on everything wired into the AI stack:
Claude Code + its MCP servers, Hermes, OpenClaw, and the Python project ecosystem.

Anything not installed is reported as 'not detected' rather than failing, and the
watch list in config.toml lets new connectors light up automatically once present.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .util import first_line, load_config, run, which

_CLAUDE_JSON = Path.home() / ".claude.json"
_PROJECTS_DIR = Path.home() / "Documents" / "PythonProjects"


def _claude_code() -> dict[str, Any]:
    exe = which("claude")
    info: dict[str, Any] = {
        "name": "Claude Code",
        "kind": "ai-cli",
        "present": bool(exe),
        "path": exe,
        "version": None,
        "status": "not detected",
        "detail": {},
    }
    if not exe:
        return info
    res = run([exe, "--version"], timeout=12)
    info["version"] = _semver(res.text)
    info["status"] = "online"
    return info


def _mcp_servers() -> list[dict[str, Any]]:
    """Configured MCP servers from ~/.claude.json, enriched with live health
    from `claude mcp list` when the CLI is available."""
    configured: dict[str, dict[str, Any]] = {}
    if _CLAUDE_JSON.exists():
        try:
            data = json.loads(_CLAUDE_JSON.read_text(encoding="utf-8"))
            for name, spec in (data.get("mcpServers") or {}).items():
                configured[name] = {
                    "name": name,
                    "scope": "global",
                    "transport": spec.get("type") or ("stdio" if spec.get("command") else "?"),
                    "command": spec.get("command"),
                    "args": spec.get("args") or [],
                    "url": spec.get("url"),
                    "status": "configured",
                }
        except Exception:
            pass

    # Live health (claude mcp list prints "name: ... - ✓ Connected" style lines)
    exe = which("claude")
    if exe:
        res = run([exe, "mcp", "list"], timeout=30)
        if res.ok:
            for line in res.out.splitlines():
                line_l = line.lower()
                name = line.split(":", 1)[0].strip()
                if not name or name not in configured:
                    # tolerate formatting differences; match by prefix
                    match = next((n for n in configured if line.strip().startswith(n)), None)
                    name = match or name
                if name in configured:
                    if "✓" in line or "connected" in line_l:
                        configured[name]["status"] = "online"
                    elif "✗" in line or "failed" in line_l or "error" in line_l:
                        configured[name]["status"] = "error"
    return list(configured.values())


def _generic_cli(name: str, label: str, kind: str) -> dict[str, Any]:
    exe = which(name)
    info: dict[str, Any] = {
        "name": label,
        "kind": kind,
        "present": bool(exe),
        "path": exe,
        "version": None,
        "status": "not detected" if not exe else "online",
    }
    if exe:
        res = run([exe, "--version"], timeout=10)
        info["version"] = _semver(res.text) if res.ok else None
    return info


def _scan_skills(repo: Path) -> list[dict[str, str]]:
    """Enumerate Hermes skills: any folder containing a SKILL.md, tagged by the
    category folder it lives under (e.g. data-science/quantfin-nightly)."""
    skills: list[dict[str, str]] = []
    skills_root = repo / "skills"
    base = skills_root if skills_root.exists() else repo
    for sk in sorted(base.rglob("SKILL.md")):
        folder = sk.parent
        try:
            category = folder.parent.relative_to(base).as_posix() or "—"
        except ValueError:
            category = "—"
        skills.append({"name": folder.name, "category": category})
    return skills


def _hermes() -> dict[str, Any]:
    """Hermes may be a CLI or a skills repo. Prefer the configured repo path,
    then a `hermes` CLI, then a hermes-named folder in PythonProjects."""
    info: dict[str, Any] = {
        "name": "Hermes",
        "kind": "ai-framework",
        "present": False,
        "path": None,
        "version": None,
        "status": "not detected",
        "skills": [],
        "branch": None,
    }

    # 1. Configured skills repo
    cfg_path = (load_config().get("connectors", {}) or {}).get("hermes_path", "")
    repo: Path | None = None
    if cfg_path and Path(cfg_path).exists():
        repo = Path(cfg_path)
    else:
        for p in _safe_iter(_PROJECTS_DIR):
            if p.is_dir() and re.search(r"hermes", p.name, re.I):
                repo = p
                break

    if repo is not None:
        skills = _scan_skills(repo)
        git = which("git")
        branch = None
        if git and (repo / ".git").exists():
            b = run([git, "-C", str(repo), "rev-parse", "--abbrev-ref", "HEAD"], timeout=8)
            branch = b.text if b.ok else None
        info.update(
            present=True,
            status="linked" if skills else "linked (no skills)",
            path=str(repo),
            skills=skills,
            branch=branch,
            version=f"{len(skills)} skills",
        )
        return info

    # 2. CLI fallback
    cli = _generic_cli("hermes", "Hermes", "ai-framework")
    cli["skills"] = []
    cli["branch"] = None
    return cli


def _openclaw() -> dict[str, Any]:
    return _generic_cli("openclaw", "OpenClaw", "ai-framework")


def _safe_iter(path: Path):
    try:
        return list(path.iterdir())
    except Exception:
        return []


def _semver(text: str) -> str | None:
    m = re.search(r"(\d+\.\d+(?:\.\d+)?)", text)
    return m.group(1) if m else (first_line(text) or None)


def scan_connectors() -> dict[str, Any]:
    claude = _claude_code()
    mcp = _mcp_servers()
    claude["detail"] = {"mcp_count": len(mcp), "mcp_online": sum(1 for m in mcp if m["status"] == "online")}
    return {
        "claude": claude,
        "mcp_servers": mcp,
        "hermes": _hermes(),
        "openclaw": _openclaw(),
    }


def _project_roots() -> list[Path]:
    """Roots to scan for projects. Configurable via [projects].roots so any new
    project you create under these folders is auto-integrated on the next refresh."""
    cfg = (load_config().get("projects", {}) or {}).get("roots")
    if cfg:
        roots = [Path(r) for r in cfg]
    else:
        roots = [_PROJECTS_DIR, Path.home() / "source" / "repos"]
    return [r for r in roots if r.exists()]


def scan_projects(limit: int = 24) -> list[dict[str, Any]]:
    """The full project ecosystem across all configured roots, newest first."""
    git = which("git")
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for root in _project_roots():
        for p in _safe_iter(root):
            if not p.is_dir() or p.name.startswith("."):
                continue
            key = str(p.resolve()).lower()
            if key in seen:
                continue
            seen.add(key)
            is_git = (p / ".git").exists()
            branch = dirty = None
            if is_git and git:
                b = run([git, "-C", str(p), "rev-parse", "--abbrev-ref", "HEAD"], timeout=8)
                branch = b.text if b.ok else None
                st = run([git, "-C", str(p), "status", "--porcelain"], timeout=8)
                dirty = bool(st.out.strip()) if st.ok else None
            out.append(
                {
                    "name": p.name,
                    "path": str(p),
                    "root": root.name,
                    "mtime": p.stat().st_mtime,
                    "git": is_git,
                    "branch": branch,
                    "dirty": dirty,
                    "has_venv": (p / ".venv").exists() or (p / "venv").exists(),
                    "has_pyproject": (p / "pyproject.toml").exists(),
                }
            )
    out.sort(key=lambda d: d["mtime"], reverse=True)
    return out[:limit]


def project_detail(path: str) -> dict[str, Any]:
    """Rich drill-down for one project: git log/remote, deps, README excerpt."""
    p = Path(path)
    if not p.exists() or not p.is_dir():
        return {"ok": False, "error": "project not found"}
    # Only allow drilling into projects under a known root (no arbitrary paths).
    if not any(str(p.resolve()).lower().startswith(str(r.resolve()).lower()) for r in _project_roots()):
        return {"ok": False, "error": "path outside project roots"}

    git = which("git")
    info: dict[str, Any] = {"ok": True, "name": p.name, "path": str(p)}
    if git and (p / ".git").exists():
        info["branch"] = run([git, "-C", str(p), "rev-parse", "--abbrev-ref", "HEAD"], timeout=8).text
        info["remote"] = run([git, "-C", str(p), "remote", "get-url", "origin"], timeout=8).text or None
        st = run([git, "-C", str(p), "status", "--porcelain"], timeout=8)
        info["dirty_files"] = len([x for x in st.out.splitlines() if x.strip()])
        log = run([git, "-C", str(p), "log", "-5", "--pretty=%h %s (%cr)"], timeout=8)
        info["commits"] = [ln for ln in log.out.splitlines() if ln.strip()]

    py = p / "pyproject.toml"
    if py.exists():
        try:
            import tomllib

            data = tomllib.loads(py.read_text(encoding="utf-8"))
            proj = data.get("project", {})
            info["pyproject"] = {
                "name": proj.get("name"),
                "version": proj.get("version"),
                "deps": proj.get("dependencies", []),
                "requires_python": proj.get("requires-python"),
            }
        except Exception:
            pass

    for readme in ("README.md", "readme.md", "README.txt"):
        rp = p / readme
        if rp.exists():
            text = rp.read_text(encoding="utf-8", errors="ignore")
            info["readme"] = text[:1200]
            break

    try:
        files = list(p.rglob("*"))
        info["file_count"] = sum(1 for f in files if f.is_file() and ".git" not in f.parts)
    except Exception:
        info["file_count"] = None
    info["has_venv"] = (p / ".venv").exists() or (p / "venv").exists()

    overrides = ((load_config().get("projects", {}) or {}).get("overrides", {}) or {}).get(p.name, {})

    # Supporting documents — from an override folder if configured, else the project.
    docs_root = p
    if overrides.get("docs_path"):
        cand = p / overrides["docs_path"]
        if cand.exists():
            docs_root = cand
    info["docs"] = _scan_docs(docs_root, rel_base=p)
    info["docs_source"] = None if docs_root == p else str(docs_root.relative_to(p)).replace("\\", "/")

    # Runnable app — honour a run_path override (e.g. QuantFin -> trading_architecture).
    from .launcher import detect_run

    run_base = p
    if overrides.get("run_path"):
        cand = p / overrides["run_path"]
        if cand.exists():
            run_base = cand
    plan = detect_run(run_base)
    info["runnable"] = bool(plan)
    info["run_label"] = plan["label"] if plan else None
    info["run_path"] = str(run_base) if plan else None

    info["subprojects"] = _scan_subprojects(p)
    return info


_SKIP_SUBDIRS = {"__pycache__", ".venv", "venv", "node_modules", ".git", ".ruff_cache", "data"}


def _scan_subprojects(p: Path) -> list[dict[str, Any]]:
    """Immediate sub-folders that look like their own project (have code/launchers),
    each with run/open info — lets QuantFin's project_01…project_NN and the
    trading_architecture app be launched straight from the drill-down."""
    from .launcher import detect_run

    subs: list[dict[str, Any]] = []
    for child in sorted(p.iterdir(), key=lambda c: c.name.lower()):
        if not child.is_dir() or child.name.startswith(".") or child.name in _SKIP_SUBDIRS:
            continue
        has_code = (
            any(child.glob("*.py")) or any(child.glob("*.bat")) or any(child.glob("*.ipynb"))
            or (child / "pyproject.toml").exists() or (child / "package.json").exists()
        )
        if not has_code:
            continue
        plan = detect_run(child)
        subs.append(
            {
                "name": child.name,
                "path": str(child),
                "runnable": bool(plan),
                "run_label": plan["label"] if plan else None,
            }
        )
    return subs[:30]


_DOC_EXTS = {".md", ".docx", ".pdf", ".txt", ".rst", ".doc", ".pptx", ".xlsx"}
_DOC_DIRS = ("docs", "Documentation", "doc", "Docs", "documentation")


def _scan_docs(root: Path, rel_base: Path | None = None) -> list[dict[str, Any]]:
    """Supporting documents under `root`: top-level docs plus any docs/ subfolder.
    `rel_base` is the project root used for display paths (so an override docs
    folder still shows a path relative to the project)."""
    rel_base = rel_base or root
    candidates: list[Path] = [f for f in root.glob("*") if f.is_file()]
    for sub in _DOC_DIRS:
        d = root / sub
        if d.exists():
            candidates += [f for f in d.rglob("*") if f.is_file()]
    docs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for f in candidates:
        if f.suffix.lower() not in _DOC_EXTS:
            continue
        if f.name.lower() in ("readme.md", "readme.txt"):
            continue  # README already shown separately
        try:
            rel = str(f.relative_to(rel_base))
        except ValueError:
            rel = f.name
        if rel in seen:
            continue
        seen.add(rel)
        docs.append(
            {"name": f.stem, "rel": rel, "path": str(f), "ext": f.suffix.lower().lstrip(".")}
        )
    docs.sort(key=lambda d: d["rel"].lower())
    return docs[:24]
