# Preset Manager for oh-my-opencode-slim

A tiny, single-file web UI for managing [OpenCode](https://opencode.ai) agent presets in your
`oh-my-opencode-slim` plugin config — no pip installs, no build step, no Node.

```
python app.py          →  http://127.0.0.1:8765
```

## Why

`oh-my-opencode-slim` stores its agent presets (model, variant, skills, MCPs per agent) in
`~/.config/opencode/oh-my-opencode-slim.json`. Hand-editing that file works, but it is easy to
make mistakes. This tool gives you a local dashboard to create, edit, activate, duplicate, and
delete presets safely.

## Features

- **Preset cards** — see every preset at a glance, activate with one click, edit, duplicate, or delete.
- **Split-view editor** — pick an agent on the left rail, assign its model/variant/skills/MCPs on the right.
- **Model browser** — searchable catalog grouped by provider, with keyboard navigation (`↑`/`↓` + `Enter`).
  Click a model to assign it and jump straight to the next agent.
- **Starter templates** — begin from `empty`, `openai`, `opencode-go`, `opencode-zen-free`, `kimi`,
  `copilot`, `zai-plan`, or `anthropic` — or clone any of your existing presets.
- **Live model lists** — pulls model IDs from your local OpenAI-compatible proxy and
  [models.dev](https://models.dev) (the same registry OpenCode uses), merged with providers declared
  in your `opencode.json` / `opencode.jsonc`. Cached (5 min live / 1 h registry), read-only.
- **"Apply to all" helpers** — copy a variant, skill list, or MCP list to every agent in one click.
- **JSONC tolerant** — reads `opencode.jsonc` with comments and trailing commas just fine.

## Safety

Your config file is treated as fragile:

- Every write first copies the config to `<config>.bak-YYYYmmdd-HHMMSS` (the 10 newest backups are kept).
- Writes are atomic (temp file + `os.replace`).
- If the config is not valid JSON, nothing is written.
- Other top-level keys and untouched presets are preserved exactly.

## Requirements

- Python 3 (standard library only — no third-party packages)
- Any OS that runs Python; Windows users get a double-click launcher (`PresetManager.bat`)

## Quick start

```bash
python app.py
```

Then open <http://127.0.0.1:8765>.

On Windows you can simply double-click **`PresetManager.bat`** — it starts the server and opens
the browser for you.

### Command-line options

```
python app.py --port 9000
python app.py --config path\to\other.json   # useful for testing
```

Default port is `8765`; default config is `%USERPROFILE%\.config\opencode\oh-my-opencode-slim.json`.

## How it works

| Path | Purpose |
|---|---|
| `app.py` | The whole app: HTTP server, JSON API, validation, and the embedded web UI |
| `PresetManager.bat` | Windows launcher (starts server + opens browser) |

The web UI talks to a small JSON API on the same server (`/api/config`, `/api/templates`, …).
All mutations go through validation: preset names must match `^[a-zA-Z0-9][a-zA-Z0-9._-]*$`,
only known agents (`orchestrator`, `oracle`, `librarian`, `explorer`, `designer`, `fixer`,
`observer`, `council`) are kept, and empty agent entries are dropped.

Changes take effect on the next OpenCode run — restart OpenCode to apply them immediately.

## Project structure

```
Preset Maker OpenCode/
├── app.py              # Single-file server + UI
└── PresetManager.bat   # Windows launcher
```

## License

MIT — add a `LICENSE` file before publishing if you want it to apply formally.
