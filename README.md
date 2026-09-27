# Preset Manager for oh-my-opencode-slim

A tiny, single-file web UI for managing [OpenCode](https://opencode.ai) agent presets in your
`oh-my-opencode-slim` plugin config — and the model list your provider exposes via `opencode.jsonc`.
No pip installs, no build step, no Node.

```
python app.py          →  http://127.0.0.1:8765
```

## Why

`oh-my-opencode-slim` stores its agent presets (model, variant, skills, MCPs per agent) in
`~/.config/opencode/oh-my-opencode-slim.json`, while the models each provider offers live in the
sibling `opencode.jsonc`. Hand-editing either file works, but it is easy to make mistakes. This
tool gives you a local dashboard to create, edit, activate, duplicate, and delete presets safely,
and to curate a provider's model list.

## Features

- **Home screen** — pick *Presets* or *Provider Models*. The Provider option is disabled with an
  explanatory hint when `opencode.jsonc` is not found next to your config.
- **Preset cards** — see every preset at a glance with a per-agent model summary. Activate with one
  click, or edit, duplicate, or delete. Long model IDs are truncated with an ellipsis so the variant
  badge stays readable. An empty state guides you to create your first preset.
- **Split-view editor** — pick an agent on the left rail, assign its model/variant/skills/MCPs on the right.
- **Model browser** — provider pills switch a single-provider panel of model chips, so you never
  face a wall of every model at once. Search, keyboard navigation (`↑`/`↓` + `Enter`), and a
  per-provider "Show all" affordance. Click a model to assign it and jump straight to the next agent.
- **Provider Models editor** — manage the model list for the provider declared in `opencode.jsonc`.
  Models render as removable chips; add them by typing with live catalog autocomplete or by browsing
  a searchable, provider-grouped catalog you can show or hide.
- **Starter templates** — begin from `empty`, `openai`, `opencode-go`, `opencode-zen-free`, `kimi`,
  `copilot`, `zai-plan`, or `anthropic` — or clone any of your existing presets.
- **Live model lists** — pulls model IDs from your local OpenAI-compatible proxy and
  [models.dev](https://models.dev) (the same registry OpenCode uses), merged with providers declared
  in your `opencode.json` / `opencode.jsonc`. Cached (5 min live / 1 h registry), read-only.
- **"Apply to all" helpers** — copy a variant, skill list, or MCP list to every agent in one click.
- **JSONC tolerant** — reads `opencode.jsonc` with comments and trailing commas just fine.

## Safety

Your config files are treated as fragile:

- Every write first copies the file to `<config>.bak-YYYYmmdd-HHMMSS` (the 10 newest backups are kept).
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
python app.py --host 0.0.0.0
python app.py --config path\to\other.json   # useful for testing
```

Default host is `127.0.0.1`, port `8765`; default config is
`%USERPROFILE%\.config\opencode\oh-my-opencode-slim.json`.

## How it works

| Path | Purpose |
|---|---|
| `app.py` | The whole app: HTTP server, JSON API, validation, and the embedded web UI |
| `PresetManager.bat` | Windows launcher (starts server + opens browser) |

The web UI talks to a small JSON API on the same server:

| Method & path | Purpose |
|---|---|
| `GET /` | The embedded web UI |
| `GET /api/config` | Current preset config, config path, backup list, and provider model catalog |
| `GET /api/templates` | Built-in starter templates plus clones of your existing presets |
| `GET /api/opencode-config` | Parsed `opencode.jsonc` and its provider model lists |
| `POST /api/preset` | Create, update, or rename a preset |
| `POST /api/activate` | Set the active preset |
| `POST /api/delete` | Delete a preset (blocked while active) |
| `POST /api/duplicate` | Copy a preset under a new name |
| `POST /api/opencode-provider-models` | Replace a provider's model list in `opencode.jsonc` |

All mutations go through validation: preset names must match `^[a-zA-Z0-9][a-zA-Z0-9._-]*$`,
only known agents (`orchestrator`, `oracle`, `librarian`, `explorer`, `designer`, `fixer`,
`observer`, `council`) are kept, and empty agent entries are dropped.

Changes take effect on the next OpenCode run — restart OpenCode to apply them immediately.

## Project structure

```
Omslim-Preset-Manager/
├── app.py              # Single-file server + UI
├── PresetManager.bat   # Windows launcher
└── README.md
```

## License

MIT — add a `LICENSE` file before publishing if you want it to apply formally.
