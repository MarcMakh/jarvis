"""Detects and reports on everything wired into the AI stack:
Claude Code + its MCP servers, Hermes, OpenClaw, and the project ecosystem.

Anything not installed is reported as 'not detected' rather than failing, and the
watch list in config.toml lets new connectors light up automatically once present.

Each public scan here is one *section* of the HUD state (see state.py), cached
and refreshed on its own schedule — `claude mcp list` (slow, spawns every MCP
server) no longer reruns just because a git status went stale.
"""

from __future__ import annotations

import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from urllib.request import urlopen

from .util import clean_env, first_line, is_under, load_config, project_roots, run, which

_CLAUDE_JSON = Path.home() / ".claude.json"


# ── Claude Code ──────────────────────────────────────────────────────────

def claude_auth() -> dict[str, Any]:
    """Whether the standalone `claude` CLI is signed in — asked of the CLI
    itself (`claude auth status`, instant and free) instead of assumed from
    the exe merely existing. This is what the brain's CLI engine depends on."""
    exe = which("claude")
    if not exe:
        return {"installed": False, "logged_in": False, "method": None}
    res = run([exe, "auth", "status"], timeout=15, env=clean_env())
    try:
        data = json.loads(res.out[res.out.index("{"): res.out.rindex("}") + 1])
    except Exception:
        return {"installed": True, "logged_in": False, "method": None,
                "error": first_line(res.text) or "could not read auth status"}
    return {"installed": True, "logged_in": bool(data.get("loggedIn")),
            "method": data.get("authMethod"), "plan": data.get("subscriptionType")}


def claude_code() -> dict[str, Any]:
    exe = which("claude")
    info: dict[str, Any] = {
        "name": "Claude Code",
        "kind": "ai-cli",
        "present": bool(exe),
        "path": exe,
        "version": None,
        "status": "not detected",
        "auth": {"installed": False, "logged_in": False},
        "detail": {},
    }
    if not exe:
        return info
    with ThreadPoolExecutor(max_workers=2) as ex:
        f_ver = ex.submit(run, [exe, "--version"], 12)
        f_auth = ex.submit(claude_auth)
        info["version"] = _semver(f_ver.result().text)
        info["auth"] = f_auth.result()
    info["status"] = "online"
    return info


def mcp_servers() -> list[dict[str, Any]]:
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
    if exe and configured:
        res = run([exe, "mcp", "list"], timeout=45, env=clean_env())
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
                    elif "needs auth" in line_l or "unauthor" in line_l:
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


# ── Hermes ───────────────────────────────────────────────────────────────

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


def hermes_skills() -> dict[str, Any]:
    """Hermes may be a CLI or a skills repo. Prefer the configured repo path,
    then a `hermes` CLI, then a hermes-named folder under a project root."""
    info: dict[str, Any] = {
        "name": "Hermes Skills",
        "kind": "ai-framework",
        "present": False,
        "path": None,
        "version": None,
        "status": "not detected",
        "skills": [],
        "branch": None,
    }

    cfg_path = (load_config().get("connectors", {}) or {}).get("hermes_path", "")
    repo: Path | None = None
    if cfg_path and Path(cfg_path).exists():
        repo = Path(cfg_path)
    else:
        for root in project_roots():
            for p in _safe_iter(root):
                if p.is_dir() and re.search(r"hermes[-_ ]?skills", p.name, re.I):
                    repo = p
                    break
            if repo:
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

    cli = _generic_cli("hermes", "Hermes", "ai-framework")
    cli["skills"] = []
    cli["branch"] = None
    return cli


def openclaw() -> dict[str, Any]:
    return _generic_cli("openclaw", "OpenClaw", "ai-framework")


def hermes_chat_url() -> str:
    conn = load_config().get("connectors", {}) or {}
    return str(conn.get("hermes_chat_url") or "http://127.0.0.1:7718").rstrip("/")


def hermes_chat_path() -> str | None:
    conn = load_config().get("connectors", {}) or {}
    path = conn.get("hermes_chat_path") or ""
    if path and Path(path).exists():
        return str(path)
    for root in project_roots():
        cand = root / "Hermes"
        if cand.exists():
            return str(cand)
    return None


def hermes_chat() -> dict[str, Any]:
    """The Hermes open-LLM chat app - a separate FastAPI server (default :7718).
    Probed over HTTP so the HUD shows whether it's up, which providers/models
    are available, today's spend, and can start it when it's down."""
    url = hermes_chat_url()
    info: dict[str, Any] = {
        "name": "Hermes Chat",
        "kind": "ai-app",
        "present": False,
        "status": "offline",
        "url": url,
        "path": hermes_chat_path(),
        "version": None,
        "providers": [],
        "usage": None,
    }
    try:
        with urlopen(f"{url}/api/health", timeout=1.5) as r:
            health = json.loads(r.read().decode("utf-8"))
        info["present"] = True
        info["status"] = "online"
        info["version"] = health.get("version")
    except Exception:
        return info  # server not running; keep the offline shell (path lets us start it)

    try:
        with urlopen(f"{url}/api/providers", timeout=6) as r:
            data = json.loads(r.read().decode("utf-8"))
        info["providers"] = [
            {
                "id": p.get("id"),
                "label": p.get("label"),
                "kind": p.get("kind"),
                "available": p.get("available"),
                "models": (p.get("models") or [])[:40],
                "model_count": len(p.get("models") or []),
                "default_model": p.get("default_model"),
                "loaded": p.get("loaded") or [],
                "reason": p.get("reason", ""),
            }
            for p in data.get("providers", [])
        ]
    except Exception:
        pass
    try:
        with urlopen(f"{url}/api/usage", timeout=3) as r:
            u = json.loads(r.read().decode("utf-8"))
        info["usage"] = {
            "spent_today": u.get("spent_today", 0.0),
            "tokens_today": u.get("tokens_today", 0),
            "daily_budget_usd": u.get("daily_budget_usd", 0.0),
        }
    except Exception:
        pass
    return info


def _safe_iter(path: Path) -> list[Path]:
    try:
        return list(path.iterdir())
    except Exception:
        return []


def _semver(text: str) -> str | None:
    m = re.search(r"(\d+\.\d+(?:\.\d+)?)", text)
    return m.group(1) if m else (first_line(text) or None)


def scan_connectors() -> dict[str, Any]:
    """Every connector at once (kept for the snapshot note / scripts; the HUD
    reads the individual sections through state.py)."""
    with ThreadPoolExecutor(max_workers=5) as ex:
        f_claude = ex.submit(claude_code)
        f_mcp = ex.submit(mcp_servers)
        f_chat = ex.submit(hermes_chat)
        f_hermes = ex.submit(hermes_skills)
        f_oc = ex.submit(openclaw)
        claude = f_claude.result()
        mcp = f_mcp.result()
        claude["detail"] = {"mcp_count": len(mcp),
                            "mcp_online": sum(1 for m in mcp if m["status"] == "online")}
        return {
            "claude": claude,
            "mcp_servers": mcp,
            "hermes_chat": f_chat.result(),
            "hermes": f_hermes.result(),
            "openclaw": f_oc.result(),
        }


# ── projects ─────────────────────────────────────────────────────────────

_HEAVY_DIRS = {".git", ".venv", "venv", "env", "node_modules", "__pycache__", ".ruff_cache",
               ".pytest_cache", ".mypy_cache", ".tox", "dist", "build", ".next", ".idea",
               ".vs", "site-packages", ".playwright-mcp", "app_profile"}


def _project_roots() -> list[Path]:  # old name, kept for callers
    return project_roots()


def _project_kind(p: Path) -> str:
    if (p / "pyproject.toml").exists() or (p / "requirements.txt").exists():
        return "python"
    if (p / "package.json").exists():
        return "node"
    try:
        names = [c.name.lower() for c in p.iterdir() if c.is_file()]
    except OSError:
        names = []
    if any(n.endswith(".py") for n in names):
        return "python"
    if any(n.endswith((".sln", ".csproj")) for n in names):
        return "dotnet"
    if any(n.endswith((".ps1", ".psm1")) for n in names):
        return "powershell"
    return "other"


def _activity_mtime(p: Path) -> float:
    """Most recent change among a project's top-level entries. A folder's own
    mtime only moves when something is added/removed directly inside it, so
    editing an existing file would otherwise never count as activity."""
    best = 0.0
    try:
        with os.scandir(p) as it:
            for e in it:
                if e.name in _HEAVY_DIRS:
                    continue
                try:
                    best = max(best, e.stat().st_mtime)
                except OSError:
                    continue
    except OSError:
        pass
    if best:
        return best
    try:
        return p.stat().st_mtime
    except OSError:
        return 0.0


def _git_has_remote(p: Path) -> bool:
    cfg = p / ".git" / "config"
    try:
        return '[remote "' in cfg.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False


def _git_state(git: str, p: Path) -> dict[str, Any]:
    """Branch, upstream, ahead/behind and dirty count from ONE git process
    (`status --porcelain=v2 --branch`), plus the last commit time."""
    out: dict[str, Any] = {"branch": None, "dirty": None, "dirty_files": 0, "ahead": 0,
                           "behind": 0, "upstream": None, "last_commit": None}
    st = run([git, "-C", str(p), "status", "--porcelain=v2", "--branch"], timeout=12)
    if st.ok:
        dirty = 0
        for line in st.out.splitlines():
            if line.startswith("# branch.head "):
                out["branch"] = line[14:].strip()
            elif line.startswith("# branch.upstream "):
                out["upstream"] = line[18:].strip()
            elif line.startswith("# branch.ab "):
                m = re.search(r"\+(\d+)\s+-(\d+)", line)
                if m:
                    out["ahead"], out["behind"] = int(m.group(1)), int(m.group(2))
            elif line and not line.startswith("#"):
                dirty += 1
        out["dirty_files"] = dirty
        out["dirty"] = dirty > 0
    lc = run([git, "-C", str(p), "log", "-1", "--format=%ct"], timeout=8)
    if lc.ok and lc.out.strip().isdigit():
        out["last_commit"] = int(lc.out.strip())
    return out


def scan_projects(limit: int = 80) -> list[dict[str, Any]]:
    """The full project ecosystem across all configured roots, newest first."""
    from .launcher import detect_run

    git = which("git")
    overrides = (load_config().get("projects", {}) or {}).get("overrides", {}) or {}
    seen: set[str] = set()
    todo: list[tuple[Path, Path]] = []  # (project dir, root)
    for root in project_roots():
        for p in _safe_iter(root):
            if not p.is_dir() or p.name.startswith((".", "_")):
                continue
            key = str(p.resolve()).lower()
            if key in seen:
                continue
            seen.add(key)
            todo.append((p, root))

    def _one(item: tuple[Path, Path]) -> dict[str, Any]:
        p, root = item
        is_git = (p / ".git").exists()
        info: dict[str, Any] = {
            "name": p.name,
            "path": str(p),
            "root": root.name,
            "kind": _project_kind(p),
            "mtime": _activity_mtime(p),
            "git": is_git,
            "branch": None, "dirty": None, "dirty_files": 0, "ahead": 0, "behind": 0,
            "upstream": None, "last_commit": None,
            "remote": _git_has_remote(p) if is_git else False,
            "has_venv": (p / ".venv").exists() or (p / "venv").exists(),
            "has_pyproject": (p / "pyproject.toml").exists(),
        }
        if is_git and git:
            info.update(_git_state(git, p))
        ov = overrides.get(p.name) or {}
        run_base = p / ov["run_path"] if ov.get("run_path") and (p / ov["run_path"]).exists() else p
        try:
            plan = detect_run(run_base)
        except Exception:
            plan = None
        info["runnable"] = bool(plan)
        info["run_label"] = plan["label"] if plan else None
        info["run_path"] = str(run_base) if plan else None
        info["launch"] = ov.get("launch")
        return info

    with ThreadPoolExecutor(max_workers=8) as ex:
        out = list(ex.map(_one, todo))
    out.sort(key=lambda d: d["mtime"], reverse=True)
    return out[:limit]


def _count_files(p: Path, cap: int = 20000) -> int | None:
    """Source-file count, skipping virtualenvs / node_modules / caches. The old
    `rglob('*')` walked every file of every dependency (tens of thousands for a
    project with a .venv) just to print one number."""
    n = 0
    try:
        for _dirpath, dirnames, filenames in os.walk(p):
            dirnames[:] = [d for d in dirnames if d not in _HEAVY_DIRS]
            n += len(filenames)
            if n >= cap:
                return cap
    except OSError:
        return None
    return n


def project_detail(path: str) -> dict[str, Any]:
    """Rich drill-down for one project: git log/remote, deps, README excerpt."""
    p = Path(path)
    if not path or not p.exists() or not p.is_dir():
        return {"ok": False, "error": "project not found"}
    # Only allow drilling into projects under a known root (no arbitrary paths).
    if not is_under(p, project_roots()):
        return {"ok": False, "error": "path outside project roots"}

    git = which("git")
    info: dict[str, Any] = {"ok": True, "name": p.name, "path": str(p),
                            "kind": _project_kind(p)}
    if git and (p / ".git").exists():
        with ThreadPoolExecutor(max_workers=3) as ex:
            f_state = ex.submit(_git_state, git, p)
            f_remote = ex.submit(run, [git, "-C", str(p), "remote", "get-url", "origin"], 8)
            f_log = ex.submit(run, [git, "-C", str(p), "log", "-8",
                                    "--pretty=%h\x1f%s\x1f%cr"], 8)
            state = f_state.result()
            info.update(branch=state["branch"], dirty_files=state["dirty_files"],
                        ahead=state["ahead"], behind=state["behind"],
                        upstream=state["upstream"])
            info["remote"] = f_remote.result().text or None
            commits = []
            for ln in f_log.result().out.splitlines():
                parts = ln.split("\x1f")
                if len(parts) == 3:
                    commits.append({"hash": parts[0], "subject": parts[1], "when": parts[2]})
            info["commits"] = commits

    py = p / "pyproject.toml"
    if py.exists():
        try:
            import tomllib

            data = tomllib.loads(py.read_text(encoding="utf-8"))
            proj = data.get("project", {})
            info["pyproject"] = {
                "name": proj.get("name"),
                "version": proj.get("version"),
                "description": proj.get("description"),
                "deps": proj.get("dependencies", []),
                "requires_python": proj.get("requires-python"),
            }
        except Exception:
            pass

    for readme in ("README.md", "readme.md", "README.txt"):
        rp = p / readme
        if rp.exists():
            text = rp.read_text(encoding="utf-8", errors="ignore")
            info["readme"] = text[:6000]
            break

    info["file_count"] = _count_files(p)
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
    for child in sorted(_safe_iter(p), key=lambda c: c.name.lower()):
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
        if f.name.lower() in ("readme.md", "readme.txt", "requirements.txt"):
            continue  # README already shown separately
        try:
            rel = str(f.relative_to(rel_base))
        except ValueError:
            rel = f.name
        key = rel.lower()
        if key in seen:
            continue
        seen.add(key)
        docs.append(
            {"name": f.stem, "rel": rel, "path": str(f), "ext": f.suffix.lower().lstrip(".")}
        )
    docs.sort(key=lambda d: d["rel"].lower())
    return docs[:24]
