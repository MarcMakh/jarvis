# J.A.R.V.I.S

A personal command center for Mark's dev machine — an Iron-Man-style HUD that
watches the machine and everything wired into it, launches your apps, keeps your
projects safe, talks back in a JARVIS voice, and answers questions about it all.

## What it does

- **Overview** — a spoken **briefing** of what actually needs attention (failing
  MCP servers, full drives, failed scheduled tasks, unprotected projects, the
  brain on a fallback…), live **vitals** with two-minute sparklines (CPU with
  per-core bars, memory, **GPU + VRAM via NVML**, every drive, network), what's
  using the machine, an **app launcher** with live running status, the ecosystem
  map, recent projects and the AI stack.
- **Ask JARVIS** — streamed, multi-turn answers grounded in live system state and
  your vault notes. The brain is an **engine chain**: Claude via your
  subscription CLI → Anthropic API → Hermes (any model it can reach, preferring
  one already loaded) → a local LM Studio model — so it keeps answering when one
  is signed out or down, and says which engine answered.
- **It acts** — “open QuantFin”, “stop QuantFlow”, “open JARVIS in VS Code”,
  “remind me in 20 minutes to stretch”, “note: …”, “back up chess training”,
  “check for updates”, “brief me”. Plain commands run instantly with no model;
  the brain can trigger the same whitelisted actions itself.
- **Alerts & reminders** — a watchdog turns state into events (raised once,
  resolved automatically), shown in the bell and activity feed, spoken by
  severity, mutable per alert. Reminders survive restarts and pop a Windows
  notification if no HUD is open.
- **Projects + Shield** — every project with git branch, uncommitted / unpushed
  work and a protection grade (pushed · snapshotted · unpushed · local-only ·
  no backup). One click zips a **snapshot** of any project (or all at-risk ones)
  to `Documents\JARVIS Backups`.
- **Systems** — runtimes, dev tools, AI connectors (incl. whether the Claude CLI
  is actually signed in), updates (winget toolchain, npm globals one by one,
  JARVIS's own deps, Claude Code), your scheduled tasks, storage.
- **Vault** — keyword + semantic search over your notes (local RAG via LM Studio
  embeddings, Office/PDF docs via markitdown), quick capture to an inbox note.
- **Command palette** (`Ctrl+K`) — open any app, project, note or action.

## Run

```powershell
cd C:\Users\markj\Documents\PythonProjects\JARVIS
uv run jarvis          # opens the HUD as an app window (voice works without a click)
```

`uv run jarvis --no-browser` runs the server only (the auto-start shortcut does
this). `--port`, `--config` and `--data-dir` start a fully separate second
instance for testing. Opens at http://127.0.0.1:7717.

## The brain

`[brain].chain` sets the order; ⚙ Settings in the HUD can put any engine first.
The Claude CLI engine needs `claude auth login` once (JARVIS checks with
`claude auth status`, so it never pretends to be ready). For the API engine put
`ANTHROPIC_API_KEY` in `.env`.

## Vault memory (local RAG)

1. In LM Studio, download an embedding model (default
   `text-embedding-nomic-embed-text-v1.5`) and start the local server (port 1234).
2. Vault view → “reindex”. Markdown under `[rag].roots` and Office/PDF documents
   under `[rag].doc_roots` are chunked, embedded and stored under `data/rag/`.
3. Answers now cite your notes, and Hermes' 📚 Vault toggle uses the same index.

Reindexing is incremental (conversions cached by mtime/size, embeddings by
content hash).

## Config

Everything lives in [`config.toml`](config.toml): identity, voice, brain chain,
alerts, shield, automations, updates, your own `[[apps]]` tiles, Obsidian vault,
project roots and per-project overrides, library roots, RAG. Choices made in the
HUD (brain engine, alert policy, muted alerts) are saved to `data/settings.json`.

## Layout

```
jarvis/
  app.py         FastAPI routes; local-only request guard; live SSE stream
  state.py       per-section cached state, refreshed in the background
  telemetry.py   1 Hz sampler: CPU, memory, NVML GPU, disks, network, processes
  brain.py       engine chain + streaming + context; actions.py whitelist + intents
  alerts.py      watchdog, activity feed, reminders, Windows notifications
  briefing.py    the "what needs attention" rundown
  apps.py        app registry with running status; shield.py project protection
  connectors.py  Claude/MCP/Hermes/OpenClaw + project scan; tasks.py automations
  updater.py     winget / npm / uv / Claude checks + whitelisted upgrades
  rag.py, obsidian.py, voice.py, ears.py, launcher.py, graph.py, library.py, icons.py
static/
  index.html, jarvis.css, js/ (app, views, map, console, voice, orb, md, …)
```

Performance: a HUD read never waits on a scan; each section (MCP health, git,
apps…) refreshes on its own schedule, and nothing is scanned or sampled while no
HUD is open.
