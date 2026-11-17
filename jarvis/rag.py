"""Local RAG over the Obsidian vault + Delivery Vaults.

Fully local by design (see Mark's quality-over-cost stance): markdown notes are
chunked heading-aware, embedded through LM Studio's OpenAI-compatible
/v1/embeddings endpoint (any embedding model it has downloaded), and stored as
a numpy matrix under data/rag/. Retrieval is cosine top-k. Consumers:

  - the JARVIS brain (ask-console answers get a "VAULT MEMORY" context block),
  - /api/rag/search — used by Hermes' vault-context chat toggle,
  - /api/rag/status + /api/rag/reindex — the HUD pill.

Everything degrades gracefully: no embedding server -> status says so and
search returns [], never an exception.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from pathlib import Path
from typing import Any

import httpx

from .util import ROOT, load_config

# The index lives in the real data folder regardless of JARVIS_DATA_DIR: a
# dev/test instance reads the same index instead of silently having none.
# Set JARVIS_RAG_DIR to point a test at a throwaway index instead.
import os as _os

DATA_DIR = Path(_os.environ.get("JARVIS_RAG_DIR") or (ROOT / "data" / "rag"))
META_PATH = DATA_DIR / "meta.json"
VECS_PATH = DATA_DIR / "vectors.npy"
DOCS_CACHE = DATA_DIR / "docs_md"
DOCS_MANIFEST = DATA_DIR / "docs_manifest.json"

_DEFAULT_VAULTS_DIR = Path.home() / "Documents" / "Delivery Vaults"
_DEFAULT_DOC_ROOTS = [Path.home() / "Documents" / "PythonProjects", _DEFAULT_VAULTS_DIR]
_DEFAULT_MARKITDOWN = Path.home() / "markitdown" / ".venv" / "Scripts" / "markitdown.exe"

_lock = threading.Lock()
_progress: dict[str, Any] = {"state": "idle"}
_index_cache: dict[str, Any] = {"mtime": None, "meta": None, "vecs": None}


def _cfg() -> dict[str, Any]:
    cfg = load_config()
    r = cfg.get("rag", {}) or {}
    roots = [Path(p) for p in r.get("roots", [])]
    if not roots:
        ob = (cfg.get("obsidian", {}) or {}).get("vault", "")
        if ob and Path(ob).exists():
            roots.append(Path(ob))
        if _DEFAULT_VAULTS_DIR.exists():
            roots.append(_DEFAULT_VAULTS_DIR)
    doc_roots = [Path(p) for p in r.get("doc_roots", [])] or list(_DEFAULT_DOC_ROOTS)
    return {
        "enabled": r.get("enabled", True),
        "roots": [p for p in roots if p.exists()],
        "doc_roots": [p for p in doc_roots if p.exists()],
        "doc_exts": [e.lower() for e in r.get("doc_exts", [".docx", ".pdf", ".pptx", ".xlsx"])],
        "markitdown": Path(r.get("markitdown", _DEFAULT_MARKITDOWN)),
        "embed_base_url": str(r.get("embed_base_url", "http://127.0.0.1:1234/v1")).rstrip("/"),
        "embed_model": r.get("embed_model", "text-embedding-nomic-embed-text-v1.5"),
        "chunk_chars": int(r.get("chunk_chars", 1400)),
        "top_k": int(r.get("top_k", 6)),
        "brain_context": r.get("brain_context", True),
        "brain_k": int(r.get("brain_k", 4)),
    }


# ── chunking ─────────────────────────────────────────────────────────────

_SKIP_DIRS = {".obsidian", ".trash", ".git", "__pycache__"}


def _md_files(roots: list[Path]) -> list[Path]:
    out: list[Path] = []
    for root in roots:
        for f in root.rglob("*.md"):
            if any(part in _SKIP_DIRS for part in f.parts):
                continue
            out.append(f)
    return out


def _chunk_markdown(text: str, path: str, max_chars: int) -> list[dict[str, str]]:
    """Heading-aware chunks: split on markdown headings, then wrap long
    sections at paragraph boundaries. Each chunk keeps its heading trail so a
    hit can say where in the note it came from."""
    chunks: list[dict[str, str]] = []
    heading = ""
    buf: list[str] = []

    def flush():
        body = "\n".join(buf).strip()
        buf.clear()
        if len(body) < 40:  # skip empty/near-empty sections
            return
        # wrap oversized sections at blank lines
        while len(body) > max_chars:
            cut = body.rfind("\n\n", 0, max_chars)
            if cut < max_chars // 3:
                cut = max_chars
            part, body = body[:cut].strip(), body[cut:].strip()
            if part:
                chunks.append({"path": path, "heading": heading, "text": part})
        if body:
            chunks.append({"path": path, "heading": heading, "text": body})

    for line in text.splitlines():
        m = re.match(r"^(#{1,4})\s+(.*)", line)
        if m:
            flush()
            heading = m.group(2).strip()
        buf.append(line)
    flush()
    return chunks


# ── document ingestion (markitdown) ─────────────────────────────────────

_DOC_SKIP_DIRS = {".venv", "venv", "node_modules", "__pycache__", ".git",
                  ".ruff_cache", ".obsidian", ".trash", "data"}


def _doc_files(cfg: dict[str, Any]) -> list[Path]:
    out: list[Path] = []
    for root in cfg["doc_roots"]:
        for ext in cfg["doc_exts"]:
            for f in root.rglob(f"*{ext}"):
                if any(part in _DOC_SKIP_DIRS for part in f.parts):
                    continue
                if f.name.startswith("~$"):  # Office lock files
                    continue
                # Skip docs that are exports of a sibling .md we already index
                # (e.g. the vaults' 5_documentation.docx generated from .md).
                if f.with_suffix(".md").exists():
                    continue
                out.append(f)
    return out


def _ingest_docs(cfg: dict[str, Any]) -> list[tuple[Path, str]]:
    """Convert .docx/.pdf/... to markdown in DOCS_CACHE via the local markitdown
    CLI (zero tokens, fully offline). Conversions are cached by source
    mtime+size, so unchanged documents cost nothing on later reindexes.
    Returns [(cached_md_path, original_source_path)] for every readable doc."""
    from .util import run

    exe = cfg["markitdown"]
    if not exe.exists():
        return []
    DOCS_CACHE.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {}
    if DOCS_MANIFEST.exists():
        try:
            manifest = json.loads(DOCS_MANIFEST.read_text(encoding="utf-8"))
        except Exception:
            manifest = {}

    sources = _doc_files(cfg)
    _progress.update(docs=len(sources), done_docs=0)
    seen: set[str] = set()
    out: list[tuple[Path, str]] = []
    for src in sources:
        key = str(src)
        seen.add(key)
        try:
            st = src.stat()
        except OSError:
            continue
        md_path = DOCS_CACHE / (hashlib.sha1(key.encode("utf-8")).hexdigest() + ".md")
        entry = manifest.get(key)
        if (entry is None or entry.get("mtime") != st.st_mtime
                or entry.get("size") != st.st_size or not md_path.exists()):
            res = run([str(exe), key, "-o", str(md_path)], timeout=180)
            # empty output = no text layer (e.g. scanned PDF) — record and skip
            ok = res.ok and md_path.exists() and md_path.stat().st_size > 40
            manifest[key] = {"mtime": st.st_mtime, "size": st.st_size, "ok": ok}
        _progress["done_docs"] = _progress.get("done_docs", 0) + 1
        if manifest[key].get("ok"):
            out.append((md_path, key))

    # prune cache entries whose source vanished
    for key in [k for k in manifest if k not in seen]:
        stale = DOCS_CACHE / (hashlib.sha1(key.encode("utf-8")).hexdigest() + ".md")
        try:
            stale.unlink(missing_ok=True)
        except OSError:
            pass
        del manifest[key]

    DOCS_MANIFEST.write_text(json.dumps(manifest), encoding="utf-8")
    return out


# ── embeddings ───────────────────────────────────────────────────────────

def _embed(texts: list[str], cfg: dict[str, Any]) -> list[list[float]]:
    """Embed a list of texts through the OpenAI-compatible endpoint, batched."""
    out: list[list[float]] = []
    with httpx.Client(timeout=120) as client:
        for i in range(0, len(texts), 32):
            batch = texts[i:i + 32]
            r = client.post(f"{cfg['embed_base_url']}/embeddings",
                            json={"model": cfg["embed_model"], "input": batch})
            r.raise_for_status()
            data = sorted(r.json()["data"], key=lambda d: d["index"])
            out.extend(d["embedding"] for d in data)
            _progress["done_chunks"] = _progress.get("done_chunks", 0) + len(batch)
    return out


def embedder_available(cfg: dict[str, Any] | None = None) -> bool:
    cfg = cfg or _cfg()
    try:
        with httpx.Client(timeout=2.0) as client:
            r = client.get(f"{cfg['embed_base_url']}/models")
            return r.status_code == 200
    except Exception:
        return False


# ── index build ──────────────────────────────────────────────────────────

def reindex(force: bool = False) -> dict[str, Any]:
    """(Re)build the index. Embeddings are cached by chunk content hash, so an
    incremental run only pays for new/changed text."""
    import numpy as np

    cfg = _cfg()
    if not cfg["roots"]:
        return {"ok": False, "error": "no vault roots exist"}
    if not embedder_available(cfg):
        return {"ok": False, "error": f"embedding server not reachable at {cfg['embed_base_url']} "
                                      f"(start LM Studio's server with an embedding model downloaded)"}

    _progress.update(state="scanning", files=0, chunks=0, done_chunks=0, error=None,
                     started=time.time())

    # previous vectors keyed by content hash -> reuse across runs
    old_by_hash: dict[str, Any] = {}
    if not force and META_PATH.exists() and VECS_PATH.exists():
        try:
            old_meta = json.loads(META_PATH.read_text(encoding="utf-8"))
            old_vecs = np.load(VECS_PATH)
            if old_meta.get("model") == cfg["embed_model"]:
                for h, v in zip(old_meta.get("hashes", []), old_vecs):
                    old_by_hash[h] = v
        except Exception:
            old_by_hash = {}

    chunks: list[dict[str, str]] = []
    files = _md_files(cfg["roots"])
    for f in files:
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        chunks.extend(_chunk_markdown(text, str(f), cfg["chunk_chars"]))

    # Office/PDF documents: converted through markitdown, indexed under their
    # ORIGINAL path so hits name the real file, not the cache.
    _progress.update(state="converting")
    docs = _ingest_docs(cfg)
    for md_path, source in docs:
        try:
            text = md_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        chunks.extend(_chunk_markdown(text, source, cfg["chunk_chars"]))
    n_sources = len(files) + len(docs)

    hashes = [hashlib.sha1(c["text"].encode("utf-8")).hexdigest() for c in chunks]
    todo = [i for i, h in enumerate(hashes) if h not in old_by_hash]
    _progress.update(state="embedding", files=n_sources, chunks=len(chunks),
                     new_chunks=len(todo), done_chunks=0)

    try:
        new_vecs = _embed([chunks[i]["text"] for i in todo], cfg) if todo else []
    except Exception as exc:
        _progress.update(state="error", error=str(exc))
        return {"ok": False, "error": f"embedding failed: {exc}"}

    dim = (len(new_vecs[0]) if new_vecs
           else (next(iter(old_by_hash.values())).shape[0] if old_by_hash else 0))
    if dim == 0:
        _progress.update(state="error", error="nothing to index")
        return {"ok": False, "error": "no indexable content found"}

    mat = np.zeros((len(chunks), dim), dtype=np.float32)
    it = iter(new_vecs)
    for i, h in enumerate(hashes):
        mat[i] = old_by_hash[h] if h in old_by_hash else np.asarray(next(it), dtype=np.float32)
    # normalize once so search is a plain dot product
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    mat = mat / norms

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    np.save(VECS_PATH, mat)
    META_PATH.write_text(json.dumps({
        "model": cfg["embed_model"],
        "indexed_at": time.time(),
        "files": n_sources,
        "hashes": hashes,
        "chunks": chunks,
    }), encoding="utf-8")
    _index_cache["mtime"] = None  # force reload on next search
    _progress.update(state="done", chunks=len(chunks))
    return {"ok": True, "files": n_sources, "chunks": len(chunks), "embedded": len(todo)}


def start_reindex(force: bool = False) -> dict[str, Any]:
    """Kick off a reindex in the background; the status endpoint reports progress."""
    if _os.environ.get("JARVIS_DATA_DIR") and not _os.environ.get("JARVIS_RAG_DIR"):
        # A redirected (dev/test) instance shares the real index read-only.
        return {"ok": False, "error": "reindexing is disabled on a dev/test instance"}
    if _progress.get("state") in ("scanning", "converting", "embedding"):
        return {"ok": True, "already_running": True, **_progress}

    def _run():
        with _lock:
            reindex(force=force)

    threading.Thread(target=_run, daemon=True).start()
    return {"ok": True, "started": True}


# ── search ───────────────────────────────────────────────────────────────

def _load_index():
    import numpy as np

    if not META_PATH.exists() or not VECS_PATH.exists():
        return None, None
    mtime = META_PATH.stat().st_mtime
    if _index_cache["mtime"] != mtime:
        _index_cache["meta"] = json.loads(META_PATH.read_text(encoding="utf-8"))
        _index_cache["vecs"] = np.load(VECS_PATH)
        _index_cache["mtime"] = mtime
    return _index_cache["meta"], _index_cache["vecs"]


def search(query: str, k: int | None = None) -> list[dict[str, Any]]:
    import numpy as np

    cfg = _cfg()
    if not cfg["enabled"] or not query.strip():
        return []
    meta, vecs = _load_index()
    if meta is None or vecs is None or not len(vecs):
        return []
    try:
        q = np.asarray(_embed([query], cfg)[0], dtype=np.float32)
    except Exception:
        return []
    q = q / (np.linalg.norm(q) or 1.0)
    scores = vecs @ q
    k = k or cfg["top_k"]
    top = np.argsort(scores)[::-1][:k]
    out = []
    for i in top:
        c = meta["chunks"][int(i)]
        out.append({
            "path": c["path"],
            "note": Path(c["path"]).stem,
            "heading": c["heading"],
            "text": c["text"],
            "score": round(float(scores[int(i)]), 4),
        })
    return out


def brain_context(question: str) -> str:
    """Formatted retrieval block for the JARVIS brain. Empty string when RAG is
    disabled, unindexed, or the embedder is down — never raises."""
    try:
        cfg = _cfg()
        if not cfg["brain_context"]:
            return ""
        hits = search(question, k=cfg["brain_k"])
        if not hits:
            return ""
        parts = []
        for h in hits:
            head = f" › {h['heading']}" if h["heading"] else ""
            parts.append(f"--- {h['note']}{head} ---\n{h['text'][:900]}")
        return "\n".join(parts)
    except Exception:
        return ""


def status() -> dict[str, Any]:
    cfg = _cfg()
    meta, vecs = (None, None)
    try:
        meta, vecs = _load_index()
    except Exception:
        pass
    return {
        "enabled": cfg["enabled"],
        "available": embedder_available(cfg),
        "model": cfg["embed_model"],
        "roots": [str(r) for r in cfg["roots"]],
        "indexed": meta is not None,
        "files": (meta or {}).get("files", 0),
        "chunks": len((meta or {}).get("chunks", [])),
        "indexed_at": (meta or {}).get("indexed_at"),
        "progress": dict(_progress),
    }
