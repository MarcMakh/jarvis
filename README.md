# J.A.R.V.I.S

A personal command center for Mark's dev machine — an Iron-Man-style web HUD that
inventories your whole toolchain, shows the live state of everything wired into
Claude / MCP / Hermes / OpenClaw, talks back in a JARVIS voice, connects to your
Obsidian vault, and can apply updates from one place.

## What it does

- **Inventory** — Python interpreters, uv, Node/npm, and dev tools (git, gh, ripgrep,
  pandoc, ruff, pyright, VS Code, Docker), each with version + install status.
- **AI connectors** — Claude Code version, live MCP server health (`claude mcp list`),
  plus Hermes / OpenClaw detection. Anything not installed shows as *not detected*
  and lights up automatically once present.
- **Live host gauges** — CPU / memory / disk / uptime.
- **Updates** — checks pip (this env), npm globals, uv self, and Claude Code, and
  applies any of them with one click (whitelisted commands only).
- **Obsidian** — vault status, recent notes, full-text search, and a one-click
  "snapshot" that writes a dated system report into your vault.
- **Ask JARVIS** — a Claude-powered console with your live system state injected as
  context, with spoken replies via `edge-tts`.
- **Vault memory (local RAG)** — indexes your Obsidian/markdown vaults **and**
  Office/PDF documents (converted locally via markitdown) with LM Studio
  embeddings; ask-JARVIS answers are grounded in your own notes, fully offline.
- **Ecosystem map** — an animated force-directed graph of everything JARVIS knows:
  connectors, MCP servers, skills, projects, runtimes, and launchable apps.

## Run

```powershell
cd C:\Users\markj\Documents\PythonProjects\JARVIS
uv run jarvis          # installs deps on first run, opens the HUD in your browser
```

Or: `uv run python -m jarvis`. Stops with Ctrl+C. Opens at http://127.0.0.1:7717.

## The brain (no API key needed)

`[brain].mode = "auto"` routes "ask JARVIS" through your existing `claude.exe`
(using your Claude Code subscription auth). Set `ANTHROPIC_API_KEY` to use the
faster anthropic SDK path instead. Force either with `mode = "cli"` / `"sdk"`.

## Voice

`edge-tts` (free Microsoft neural TTS). Default voice `en-GB-RyanNeural`. Change
it in `config.toml`; see `/api/voices` for the full English list.

## Vault memory (local RAG)

JARVIS can ground its answers in **your own notes and documents** — with nothing
leaving your machine:

1. In LM Studio, download an embedding model (default:
   `text-embedding-nomic-embed-text-v1.5`) and start the local server (port 1234).
2. Click the **vault** pill next to the brain pill in the HUD. JARVIS chunks the
   markdown under `[rag].roots`, converts Office/PDF documents under
   `[rag].doc_roots` to markdown with your local **markitdown**, embeds everything,
   and stores a numpy index under `data/rag/`.
3. Done — ask-JARVIS answers now cite your notes, and the Hermes chat app can pull
   the same context via its 📚 VAULT toggle.

Reindexing is incremental: conversions are cached by file mtime/size and
embeddings by content hash, so unchanged files cost nothing. Endpoints:
`/api/rag/status`, `/api/rag/reindex`, `/api/rag/search?q=…`. All knobs live in
`[rag]` in `config.toml` (roots, doc_roots, embedding model/URL, top-k, and
whether the brain uses vault context).

## Config

Everything lives in [`config.toml`](config.toml): identity/honorific, voice,
brain mode + models, Obsidian vault path, server port, and the connector watch list.

## Layout

```
jarvis/
  app.py         FastAPI routes + cached state store (scans run in parallel)
  inventory.py   runtimes, dev tools, packages, live system stats
  connectors.py  Claude/MCP, Hermes, OpenClaw, project ecosystem
  rag.py         vault memory: chunk -> markitdown -> LM Studio embeddings -> search
  updater.py     check + apply updates (whitelisted)
  voice.py       edge-tts / ElevenLabs synthesis (cached)
  brain.py       ask JARVIS (claude CLI or anthropic SDK) + vault-memory grounding
  obsidian.py    vault status / search / snapshot
  launcher.py    open/run projects, tools, and library shortcuts (whitelisted)
  graph.py       ecosystem map data
static/          the HUD (index.html, jarvis.css, jarvis.js)
```

Performance note: the HUD's cold load runs all scans concurrently (inventory,
connectors, projects, Obsidian), so a full state rebuild costs the slowest scan
— in practice `claude mcp list` — rather than the sum of all of them.
