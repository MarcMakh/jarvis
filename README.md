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

## Config

Everything lives in [`config.toml`](config.toml): identity/honorific, voice,
brain mode + models, Obsidian vault path, server port, and the connector watch list.

## Layout

```
jarvis/
  app.py         FastAPI routes + cached state store
  inventory.py   runtimes, dev tools, packages, live system stats
  connectors.py  Claude/MCP, Hermes, OpenClaw, project ecosystem
  updater.py     check + apply updates (whitelisted)
  voice.py       edge-tts synthesis (cached)
  brain.py       ask JARVIS (claude CLI or anthropic SDK)
  obsidian.py    vault status / search / snapshot
static/          the HUD (index.html, jarvis.css, jarvis.js)
```
