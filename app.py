#!/usr/bin/env python3
"""
Preset Manager for oh-my-opencode-slim (OpenCode plugin).

Single-file, Python 3 standard library only. No pip installs.

Usage:
    python app.py                 # manage %USERPROFILE%\\.config\\opencode\\oh-my-opencode-slim.json
    python app.py --port 9000
    python app.py --config path\\to\\other.json   # useful for testing

Then open http://127.0.0.1:8765 in a browser.

Safety:
- Every write first copies the config to <config>.bak-YYYYmmdd-HHMMSS (keeps 10 newest).
- Writes are atomic (temp file + os.replace).
- If the config is not valid JSON, nothing is written.
- Other top-level keys and untouched presets are preserved exactly.
"""

import argparse
import copy
import json
import os
import re
import shutil
import tempfile
import threading
import time
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

AGENT_NAMES = [
    "orchestrator", "oracle", "librarian", "explorer",
    "designer", "fixer", "observer", "council",
]
NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$")

# Built-in / provider starter templates shown in the "New preset" picker.
# Sources (verified 2026-09):
#  - Plugin generator src/cli/providers.ts (alvinunreal/oh-my-opencode-slim):
#    GENERATED_PRESETS = openai, opencode-go; MODEL_MAPPINGS = kimi, copilot, zai-plan
#  - Documented example preset: opencode-zen-free (docs/opencode-zen-free-preset.md)
# Model IDs adjusted to the current models.dev registry where the source ID is stale.
BUILTIN_TEMPLATES = [
    {
        "id": "empty",
        "label": "Empty preset",
        "description": "Start from scratch — fill agents yourself.",
        "agents": {},
    },
    {
        "id": "openai",
        "label": "openai",
        "description": "Plugin installer default: GPT-5.6 family (terra/sol/luna).",
        "agents": {
            "orchestrator": {"model": "openai/gpt-5.6-terra", "variant": "high", "skills": ["*"], "mcps": ["*", "!context7"]},
            "oracle": {"model": "openai/gpt-5.6-sol", "variant": "high", "skills": [], "mcps": []},
            "librarian": {"model": "openai/gpt-5.6-luna", "variant": "low", "skills": [], "mcps": ["websearch", "context7", "gh_grep"]},
            "explorer": {"model": "openai/gpt-5.6-luna", "variant": "low", "skills": [], "mcps": []},
            "designer": {"model": "openai/gpt-5.6-luna", "variant": "medium", "skills": [], "mcps": []},
            "fixer": {"model": "openai/gpt-5.6-luna", "variant": "high", "skills": [], "mcps": []},
        },
    },
    {
        "id": "opencode-go",
        "label": "opencode-go",
        "description": "Plugin generated preset: MiniMax/Qwen/DeepSeek mix, observer enabled.",
        "agents": {
            "orchestrator": {"model": "opencode-go/minimax-m3", "variant": "thinking", "skills": ["*"], "mcps": ["*", "!context7"]},
            "oracle": {"model": "opencode-go/qwen3.7-max", "variant": "max", "skills": [], "mcps": []},
            "librarian": {"model": "opencode-go/deepseek-v4-flash", "variant": "high", "skills": [], "mcps": ["websearch", "context7", "gh_grep"]},
            "explorer": {"model": "opencode-go/deepseek-v4-flash", "variant": "high", "skills": [], "mcps": []},
            "designer": {"model": "opencode-go/kimi-k2.7-code", "variant": "", "skills": [], "mcps": []},
            "fixer": {"model": "opencode-go/deepseek-v4-flash", "variant": "high", "skills": [], "mcps": []},
            "observer": {"model": "opencode-go/mimo-v2.5", "variant": "", "skills": [], "mcps": []},
        },
    },
    {
        "id": "opencode-zen-free",
        "label": "opencode-zen-free",
        "description": "Documented example: OpenCode Zen free-tier models, zero cost.",
        "agents": {
            "orchestrator": {"model": "opencode/x-preview-f-free", "variant": "high", "skills": ["*"], "mcps": ["*", "!context7"]},
            "oracle": {"model": "opencode/big-pickle", "variant": "max", "skills": [], "mcps": []},
            "librarian": {"model": "opencode/nemotron-3.5-lightning-free", "variant": "low", "skills": [], "mcps": ["websearch", "context7", "gh_grep"]},
            "explorer": {"model": "opencode/nemotron-3.5-lightning-free", "variant": "low", "skills": [], "mcps": []},
            "designer": {"model": "opencode/mimo-v2.5-free", "variant": "medium", "skills": [], "mcps": []},
            "fixer": {"model": "opencode/hy3-free", "variant": "high", "skills": [], "mcps": []},
            "observer": {"model": "opencode/mimo-v2.5-free", "variant": "low", "skills": [], "mcps": []},
        },
    },
    {
        "id": "kimi",
        "label": "kimi (plan)",
        "description": "Plugin MODEL_MAPPINGS: Kimi for Coding on all agents (k3).",
        "agents": {
            "orchestrator": {"model": "kimi-for-coding/k3", "variant": "max", "skills": ["*"], "mcps": ["*", "!context7"]},
            "oracle": {"model": "kimi-for-coding/k3", "variant": "high", "skills": [], "mcps": []},
            "librarian": {"model": "kimi-for-coding/k3", "variant": "low", "skills": [], "mcps": ["websearch", "context7", "gh_grep"]},
            "explorer": {"model": "kimi-for-coding/k3", "variant": "low", "skills": [], "mcps": []},
            "designer": {"model": "kimi-for-coding/k3", "variant": "medium", "skills": [], "mcps": []},
            "fixer": {"model": "kimi-for-coding/k3", "variant": "low", "skills": [], "mcps": []},
        },
    },
    {
        "id": "copilot",
        "label": "copilot",
        "description": "Plugin MODEL_MAPPINGS, IDs updated to current registry (Opus/Haiku/Gemini).",
        "agents": {
            "orchestrator": {"model": "github-copilot/claude-opus-4.8", "variant": "max", "skills": ["*"], "mcps": ["*", "!context7"]},
            "oracle": {"model": "github-copilot/claude-opus-4.8", "variant": "high", "skills": [], "mcps": []},
            "librarian": {"model": "github-copilot/claude-haiku-4.5", "variant": "low", "skills": [], "mcps": ["websearch", "context7", "gh_grep"]},
            "explorer": {"model": "github-copilot/claude-haiku-4.5", "variant": "low", "skills": [], "mcps": []},
            "designer": {"model": "github-copilot/gemini-3.8-flash", "variant": "medium", "skills": [], "mcps": []},
            "fixer": {"model": "github-copilot/claude-sonnet-5", "variant": "low", "skills": [], "mcps": []},
        },
    },
    {
        "id": "zai-plan",
        "label": "zai-plan",
        "description": "Plugin MODEL_MAPPINGS: GLM coding plan on all agents (glm-5.3).",
        "agents": {
            "orchestrator": {"model": "zai-coding-plan/glm-5.3", "variant": "high", "skills": ["*"], "mcps": ["*", "!context7"]},
            "oracle": {"model": "zai-coding-plan/glm-5.3", "variant": "max", "skills": [], "mcps": []},
            "librarian": {"model": "zai-coding-plan/glm-5.3", "variant": "low", "skills": [], "mcps": ["websearch", "context7", "gh_grep"]},
            "explorer": {"model": "zai-coding-plan/glm-5.3", "variant": "low", "skills": [], "mcps": []},
            "designer": {"model": "zai-coding-plan/glm-5.3", "variant": "medium", "skills": [], "mcps": []},
            "fixer": {"model": "zai-coding-plan/glm-5.3", "variant": "high", "skills": [], "mcps": []},
        },
    },
    {
        "id": "anthropic",
        "label": "anthropic",
        "description": "Classic Claude setup: Opus for strategy, Sonnet for the rest.",
        "agents": {
            "orchestrator": {"model": "anthropic/claude-opus-4-8", "variant": "high", "skills": ["*"], "mcps": ["*", "!context7"]},
            "oracle": {"model": "anthropic/claude-opus-4-8", "variant": "max", "skills": [], "mcps": []},
            "librarian": {"model": "anthropic/claude-haiku-4-5", "variant": "low", "skills": [], "mcps": ["websearch", "context7", "gh_grep"]},
            "explorer": {"model": "anthropic/claude-haiku-4-5", "variant": "low", "skills": [], "mcps": []},
            "designer": {"model": "anthropic/claude-sonnet-5", "variant": "medium", "skills": [], "mcps": []},
            "fixer": {"model": "anthropic/claude-sonnet-5", "variant": "high", "skills": [], "mcps": []},
        },
    },
]
DEFAULT_CONFIG = os.path.join(
    os.path.expanduser("~"), ".config", "opencode", "oh-my-opencode-slim.json"
)
BACKUP_KEEP = 10
MAX_BODY = 10 * 1024 * 1024


class ApiError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


class App:
    def __init__(self, config_path):
        self.config_path = os.path.abspath(config_path)
        self.lock = threading.Lock()


# ---------------------------------------------------------------- config I/O

def load_config(path):
    """Load config; returns (dict, None) or (None, error)."""
    if not os.path.exists(path):
        return {"$schema": "https://unpkg.com/oh-my-opencode-slim@latest/oh-my-opencode-slim.schema.json",
                "preset": "", "presets": {}}, None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        return None, "Config is not valid JSON, refusing to touch it: %s" % e
    if not isinstance(data, dict):
        return None, "Config root must be a JSON object"
    return data, None


def backup_config(path):
    """Copy config to .bak-<stamp>; rotate, keep newest BACKUP_KEEP files."""
    if not os.path.exists(path):
        return None
    d = os.path.dirname(path)
    base = os.path.basename(path)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    shutil.copy2(path, os.path.join(d, "%s.bak-%s" % (base, stamp)))
    prefix = base + ".bak-"
    baks = sorted((f for f in os.listdir(d) if f.startswith(prefix)), reverse=True)
    for old in baks[BACKUP_KEEP:]:
        try:
            os.remove(os.path.join(d, old))
        except OSError:
            pass
    return "%s.bak-%s" % (base, stamp)


def list_backups(path):
    d = os.path.dirname(path)
    prefix = os.path.basename(path) + ".bak-"
    try:
        return sorted((f for f in os.listdir(d) if f.startswith(prefix)), reverse=True)
    except OSError:
        return []


def atomic_write_json(path, data):
    d = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(prefix=".presetmgr-", suffix=".tmp", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def strip_jsonc(text):
    """Remove // and /* */ comments plus trailing commas so JSONC parses as JSON."""
    out = []
    i, n = 0, len(text)
    in_str = False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if c == '"':
                in_str = False
            i += 1
            continue
        if c == '"':
            in_str = True
            out.append(c)
            i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i += 2
            continue
        out.append(c)
        i += 1
    s = "".join(out)
    s = re.sub(r",(\s*[}\]])", r"\1", s)
    return s


def load_provider_models(config_dir):
    """Read provider model lists from sibling opencode.json / opencode.jsonc.

    Returns {providerName: [modelId, ...]} — read-only, best effort."""
    result = {}
    for fname in ("opencode.jsonc", "opencode.json"):
        p = os.path.join(config_dir, fname)
        if not os.path.exists(p):
            continue
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.loads(strip_jsonc(f.read()))
        except Exception:
            continue
        providers = data.get("provider")
        if not isinstance(providers, dict):
            continue
        for pname, pdef in providers.items():
            models = pdef.get("models") if isinstance(pdef, dict) else None
            if isinstance(models, dict) and models:
                result[str(pname)] = [str(k) for k in models.keys()]
    return result


# ------------------------------------------------- live model list (proxy API)

MODELS_TTL = 300  # seconds

# Curated providers pulled from the public models.dev registry (the same
# registry OpenCode uses). Keys must match models.dev provider ids.
CURATED_MODELSDEV = [
    "anthropic", "openai", "google", "opencode", "deepseek", "xai",
    "moonshotai", "kimi-for-coding", "zai-coding-plan", "github-copilot",
    "groq", "mistral", "openrouter", "cerebras", "together", "qwen",
]
MODELSDEV_URL = "https://models.dev/api.json"
MODELSDEV_TTL = 3600  # seconds


def fetch_modelsdev():
    """Fetch curated provider->model-id lists from models.dev.

    Returns ({provider: [ids]}, None) or (None, error)."""
    req = urllib.request.Request(MODELSDEV_URL, headers={"User-Agent": "preset-manager/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except Exception as e:
        return None, "%s: %s" % (type(e).__name__, e)
    if not isinstance(data, dict):
        return None, "Unexpected models.dev response"
    out = {}
    for prov in CURATED_MODELSDEV:
        pdef = data.get(prov)
        models = pdef.get("models") if isinstance(pdef, dict) else None
        if isinstance(models, dict) and models:
            out[prov] = sorted(str(k) for k in models.keys())
    if not out:
        return None, "No curated providers found in models.dev"
    return out, None


def read_proxy_settings(config_dir):
    """Best-effort: read baseURL/apiKey of the first OpenAI-compatible provider
    from opencode.jsonc / opencode.json. Falls back to local OmniRoute proxy."""
    base, key = "http://localhost:20128/v1", ""
    for fname in ("opencode.jsonc", "opencode.json"):
        p = os.path.join(config_dir, fname)
        if not os.path.exists(p):
            continue
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.loads(strip_jsonc(f.read()))
        except Exception:
            continue
        providers = data.get("provider")
        if not isinstance(providers, dict):
            continue
        for pdef in providers.values():
            opts = pdef.get("options") if isinstance(pdef, dict) else None
            if isinstance(opts, dict) and opts.get("baseURL"):
                base = str(opts["baseURL"]).rstrip("/")
                key = str(opts.get("apiKey") or "")
                break
        break
    return base, key


def fetch_live_models(config_dir):
    """Fetch model ids from the local proxy /models endpoint (OpenAI-compatible).

    Returns (models_dict_or_None, error_or_None). models_dict = {provider: [ids]}
    with the provider name derived from the baseURL host."""
    base, key = read_proxy_settings(config_dir)
    url = base + "/models"
    req = urllib.request.Request(url)
    if key:
        req.add_header("Authorization", "Bearer " + key)
    try:
        with urllib.request.urlopen(req, timeout=4) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except Exception as e:
        return None, "%s: %s" % (type(e).__name__, e)
    items = data.get("data") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return None, "Unexpected /models response shape"
    ids = []
    for it in items:
        mid = it.get("id") if isinstance(it, dict) else None
        if mid:
            ids.append(str(mid))
    if not ids:
        return None, "Proxy returned no models"
    # Derive a provider label from the proxy host, e.g. localhost:20128 -> "omniroute-like".
    # Better: reuse the provider name(s) declared in opencode.jsonc that point at this baseURL.
    declared = load_provider_models(config_dir)
    if declared and len(declared) == 1:
        pname = next(iter(declared))
    else:
        try:
            host = urlparse(base).hostname or "proxy"
        except Exception:
            host = "proxy"
        pname = host
    # Merge: declared (static, curated order) first, then everything else live.
    seen = set(declared.get(pname, []))
    merged = list(declared.get(pname, []))
    for mid in sorted(ids):
        if mid not in seen:
            seen.add(mid)
            merged.append(mid)
    return {pname: merged}, None


_live_cache = {"data": None, "ts": 0.0, "fail_ts": 0.0}
_live_lock = threading.Lock()
_mdev_cache = {"data": None, "ts": 0.0, "fail_ts": 0.0}


def get_modelsdev():
    """models.dev curated lists with 1h cache; on failure retry after 10 min."""
    now = time.time()
    with _live_lock:
        if _mdev_cache["data"] and now - _mdev_cache["ts"] < MODELSDEV_TTL:
            return _mdev_cache["data"]
        if now - _mdev_cache["fail_ts"] < 600:
            return _mdev_cache["data"]
    data, _err = fetch_modelsdev()
    with _live_lock:
        if data:
            _mdev_cache["data"] = data
            _mdev_cache["ts"] = time.time()
        else:
            _mdev_cache["fail_ts"] = time.time()
    return data or _mdev_cache["data"]


def get_provider_models(config_dir):
    """Combined model suggestions:
    1. live proxy models (5-min cache) + static opencode.jsonc entries
    2. curated models.dev registry (1-h cache) as additional provider groups"""
    now = time.time()
    with _live_lock:
        if _live_cache["data"] and now - _live_cache["ts"] < MODELS_TTL:
            merged = _live_cache["data"]
        elif now - _live_cache["fail_ts"] < 60:
            merged = _live_cache["data"] or load_provider_models(config_dir)
        else:
            merged = None
    if merged is None:
        data, _err = fetch_live_models(config_dir)
        with _live_lock:
            if data:
                _live_cache["data"] = data
                _live_cache["ts"] = time.time()
            else:
                _live_cache["fail_ts"] = time.time()
        merged = data or _live_cache["data"] or load_provider_models(config_dir)
    extra = get_modelsdev() or {}
    if extra:
        combined = dict(merged)
        for prov, ids in extra.items():
            if prov in combined:
                seen = set(combined[prov])
                combined[prov] = combined[prov] + [i for i in ids if i not in seen]
            else:
                combined[prov] = list(ids)
        return combined
    return merged


# ---------------------------------------------------------------- validation

def valid_name(value, field="name"):
    value = str(value or "").strip()
    if not NAME_RE.match(value):
        raise ApiError(
            "Invalid preset %s: use letters, digits, '.', '-' or '_' "
            "(must start with a letter or digit)" % field
        )
    return value


def sanitize_agents(agents):
    """Keep only known agents; drop empty entries; normalise list fields."""
    if agents is None:
        agents = {}
    if not isinstance(agents, dict):
        raise ApiError("'agents' must be an object")
    out = {}
    for name in AGENT_NAMES:
        a = agents.get(name)
        if not isinstance(a, dict):
            continue
        model = str(a.get("model") or "").strip()
        variant = str(a.get("variant") or "").strip()
        entry = {}
        if model:
            entry["model"] = model
        if variant:
            entry["variant"] = variant
        for key in ("skills", "mcps"):
            v = a.get(key)
            if isinstance(v, str):
                v = v.splitlines()
            if not isinstance(v, list):
                v = []
            v = [str(x).strip() for x in v if str(x).strip()]
            if v:
                entry[key] = v
        if entry:
            out[name] = entry
    return out


# ---------------------------------------------------------------- HTTP layer

HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Preset Manager — oh-my-opencode-slim</title>
<style>
:root{
  --bg:#0b0e14; --panel:#11151d; --panel2:#151a24; --border:#232a38; --border2:#2c3446;
  --text:#d7dde8; --muted:#8b94a7; --accent:#4ade80; --accent-dim:#16281d;
  --danger:#f87171; --mono:ui-monospace,"Cascadia Mono",Consolas,"Courier New",monospace;
}
*{box-sizing:border-box}
[hidden]{display:none!important}
.hidden{display:none!important}
body{margin:0;background:var(--bg);color:var(--text);font:14px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
.wrap{max-width:1500px;margin:0 auto;padding:22px}
header.top{display:flex;flex-wrap:wrap;gap:12px;align-items:flex-start;justify-content:space-between;border-bottom:1px solid var(--border);padding-bottom:14px;margin-bottom:16px}
h1{font-size:19px;margin:0;letter-spacing:.3px;font-weight:650}
h1 .dot{color:var(--accent)}
.path{font-family:var(--mono);font-size:11.5px;color:var(--muted);word-break:break-all;margin-top:3px}
.badge{display:inline-block;padding:3px 11px;border-radius:999px;font-size:12px;border:1px solid var(--border2);color:var(--muted);white-space:nowrap}
.badge.active{border-color:var(--accent);color:var(--accent);background:var(--accent-dim)}
.errorbox{background:#2a1414;border:1px solid #5b2b2b;color:#fca5a5;border-radius:10px;padding:10px 14px;margin-bottom:14px;font-size:13px}
.toolbar{display:flex;gap:12px;align-items:center;margin-bottom:14px;flex-wrap:wrap}
.toolbar.space{justify-content:space-between}
.muted{color:var(--muted);font-size:12.5px}
.btn{appearance:none;border:1px solid var(--border2);background:transparent;color:var(--text);padding:6px 13px;border-radius:8px;font-size:13px;cursor:pointer;transition:border-color .15s,background .15s;font-family:inherit}
.btn:hover{border-color:var(--muted);background:#1a2030}
.btn.primary{background:var(--accent);border-color:var(--accent);color:#08130c;font-weight:600}
.btn.primary:hover{filter:brightness(1.12);background:var(--accent)}
.btn.danger{color:var(--danger);border-color:#5b2b2b}
.btn.danger:hover{background:#2a1414;border-color:var(--danger)}
.btn:disabled{opacity:.4;cursor:not-allowed}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:14px}
.card{background:var(--panel);border:1px solid var(--border);border-radius:12px;padding:16px;display:flex;flex-direction:column;gap:10px}
.card:hover{border-color:var(--border2)}
.card.activecard{border-color:var(--accent)}
.card .head{display:flex;justify-content:space-between;align-items:center;gap:8px}
.card h3{margin:0;font-size:15px;font-family:var(--mono);font-weight:600;word-break:break-all}
.summary{font-family:var(--mono);font-size:11.5px;color:var(--muted);background:var(--panel2);border:1px solid var(--border);border-radius:8px;padding:8px 10px;white-space:pre-wrap;word-break:break-all;flex:1}
.row{display:flex;gap:8px;flex-wrap:wrap}
.confirmbar{display:flex;align-items:center;gap:10px;background:#2a1414;border:1px solid #5b2b2b;border-radius:8px;padding:8px 10px;font-size:13px;flex-wrap:wrap}
.confirmbar.neutral{background:#1a2030;border-color:var(--border2)}
.confirmbar input{width:auto;flex:1;min-width:160px}
.edtitle{margin:0;font-size:16px;font-weight:600}
.unsaved{color:var(--warn,#fbbf24);font-size:12px}
#editorView .namefield{margin-bottom:14px;max-width:420px}
/* --- split-view model assignment editor --- */
#agentsGrid{display:grid;grid-template-columns:340px minmax(0,1fr);gap:14px;align-items:start}
.agent-rail{position:sticky;top:14px;display:flex;flex-direction:column;gap:5px;max-height:calc(100vh - 28px);overflow:auto;background:var(--panel);border:1px solid var(--border);border-radius:12px;padding:10px;scrollbar-width:thin;scrollbar-color:var(--border2) transparent}
.arail-head{display:flex;justify-content:space-between;align-items:baseline;gap:8px;padding:2px 4px 6px;font-size:11px;text-transform:uppercase;letter-spacing:.9px;color:var(--accent)}
.arail-head .muted{font-size:10px;text-transform:none;letter-spacing:.2px}
.agent-row{display:flex;flex-direction:column;align-items:flex-start;gap:1px;width:100%;min-height:44px;padding:7px 10px;border:1px solid transparent;border-radius:9px;background:transparent;color:var(--text);font-family:inherit;text-align:left;cursor:pointer;transition:background .12s,border-color .12s}
.agent-row:hover{background:var(--panel2);border-color:var(--border2)}
.agent-row.on{background:var(--accent-dim);border-color:var(--accent)}
.agent-row .ar-name{font-size:11px;text-transform:uppercase;letter-spacing:.8px;color:var(--muted)}
.agent-row.on .ar-name{color:var(--accent)}
.ar-model{font-family:var(--mono);font-size:12px;max-width:100%;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ar-model .cb-prov{color:var(--muted)}
.ar-model .cb-raw{color:var(--text);font-weight:600}
.ar-model.ar-none{color:var(--muted);font-style:italic;font-weight:400}
.ar-chip{display:inline-block;margin-left:7px;padding:0 7px;border-radius:999px;border:1px solid var(--border2);background:var(--panel2);color:var(--muted);font-size:10px;text-transform:none;letter-spacing:.2px}
.agent-row.on .ar-chip{border-color:var(--accent);color:var(--accent)}
.agent-pane{background:var(--panel);border:1px solid var(--border);border-radius:12px;padding:16px;display:flex;flex-direction:column;gap:14px;min-width:0}
.pane-target{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;border-bottom:1px solid var(--border);padding-bottom:10px}
.pane-target h3{margin:0;font-size:15px;font-family:var(--mono);color:var(--accent);letter-spacing:.3px}
.pane-target .muted{font-family:var(--mono);font-size:11.5px}
.pane-block{display:flex;flex-direction:column;gap:6px;min-width:0}
.pane-label{display:flex;justify-content:space-between;align-items:center;gap:8px;font-size:11px;color:var(--accent);text-transform:uppercase;letter-spacing:.9px}
.pane-label .muted{text-transform:none;letter-spacing:.2px;font-size:10.5px}
.linkbtn{appearance:none;background:transparent;border:none;color:var(--muted);font-size:11px;font-family:inherit;cursor:pointer;padding:2px 4px;text-transform:none;letter-spacing:.2px;text-decoration:underline dotted;text-underline-offset:3px}
.linkbtn:hover{color:var(--accent)}
.mb-wrap{border:1px solid var(--border);border-radius:10px;background:#0d1117;overflow:hidden}
.mb-search{border-bottom:1px solid var(--border)}
.mb-search input{border:none;border-radius:0;padding:9px 11px;font-family:var(--mono);font-size:12.5px}
.mb-search input:focus{outline:none;border:none}
.mb-list{max-height:min(460px,52vh);overflow-y:auto;overscroll-behavior:contain;padding:4px;scrollbar-width:thin;scrollbar-color:var(--border2) transparent}
.mb-list::-webkit-scrollbar{width:10px}
.mb-list::-webkit-scrollbar-track{background:transparent}
.mb-list::-webkit-scrollbar-thumb{background:var(--border2);border-radius:6px;border:2px solid #0d1117}
.seg{display:flex;flex-wrap:wrap;align-items:center;gap:6px}
.seg-btn{min-height:32px;padding:6px 14px;border:1px solid var(--border2);border-radius:999px;background:transparent;color:var(--muted);font:12.5px var(--mono);cursor:pointer;transition:border-color .12s,background .12s,color .12s}
.seg-btn:hover{border-color:var(--muted);color:var(--text)}
.seg-btn.on{background:var(--accent-dim);border-color:var(--accent);color:var(--accent);font-weight:600}
.seg-custom input{width:160px;min-height:32px;padding:6px 10px;font-family:var(--mono);font-size:12.5px}
@media(max-width:1099px){
  #agentsGrid{grid-template-columns:1fr}
  .agent-rail{position:static;flex-direction:row;align-items:center;max-height:none;padding:8px;overflow-x:auto;overflow-y:hidden}
  .arail-head{display:none}
  .agent-row{width:auto;min-width:170px;flex:0 0 auto;border-color:var(--border);min-height:44px}
  .mb-list{max-height:300px}
}
label{display:flex;flex-direction:column;gap:4px;font-size:11.5px;color:var(--muted)}
input,textarea{background:#0d1117;border:1px solid var(--border2);border-radius:8px;color:var(--text);padding:7px 9px;font-size:13px;width:100%;font-family:inherit}
input:focus,textarea:focus,.confirmbar input:focus{outline:none;border-color:var(--accent)}
input[readonly]{color:var(--muted)}
textarea{font-family:var(--mono);font-size:12px;resize:vertical;min-height:58px}
footer{margin-top:26px;padding-top:12px;border-top:1px solid var(--border);color:var(--muted);font-size:12px}
#toasts{position:fixed;right:16px;bottom:16px;display:flex;flex-direction:column;gap:8px;z-index:50}
.toast{background:var(--panel);border:1px solid var(--border2);border-left:3px solid var(--accent);border-radius:10px;padding:10px 14px;font-size:13px;box-shadow:0 8px 24px rgba(0,0,0,.45);animation:slide .18s ease-out;max-width:380px;word-break:break-word}
.toast.error{border-left-color:var(--danger)}
@keyframes slide{from{transform:translateY(8px);opacity:0}to{transform:none;opacity:1}}
.overlay{position:fixed;inset:0;background:rgba(5,8,12,.72);display:flex;align-items:flex-start;justify-content:center;padding:6vh 16px;z-index:40;overflow:auto}
.modal{background:var(--panel);border:1px solid var(--border2);border-radius:14px;max-width:860px;width:100%;padding:18px;box-shadow:0 16px 48px rgba(0,0,0,.55)}
.modal h3{margin:0 0 4px;font-size:16px}
.modal .sub{color:var(--muted);font-size:12.5px;margin-bottom:14px}
.tplgroup{margin-bottom:16px}
.tplgroup h4{margin:0 0 8px;font-size:11.5px;text-transform:uppercase;letter-spacing:.9px;color:var(--accent)}
.tpls{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:10px}
.tpl{background:var(--panel2);border:1px solid var(--border2);border-radius:10px;padding:12px;cursor:pointer;text-align:left;color:var(--text);font-family:inherit;transition:border-color .15s,background .15s;display:flex;flex-direction:column;gap:4px}
.tpl:hover{border-color:var(--accent);background:#182030}
.tpl b{font-size:13.5px;font-family:var(--mono);word-break:break-all}
.tpl span{font-size:11.5px;color:var(--muted);line-height:1.45}
/* --- model browser list (persistent, split-view) --- */
.cb-group{position:sticky;top:0;z-index:1;display:flex;justify-content:space-between;align-items:baseline;gap:10px;background:#0d1117;margin:0 -4px;padding:6px 12px 4px;font-size:10.5px;text-transform:uppercase;letter-spacing:.9px;color:var(--accent);border-bottom:1px solid var(--border);cursor:default;user-select:none}
.cb-group .cb-count{color:var(--muted);font-size:10px;letter-spacing:.2px;text-transform:none}
.cb-opt{min-height:32px;display:flex;align-items:center;gap:8px;padding:6px 10px;border:1px solid transparent;border-radius:7px;font-family:var(--mono);font-size:12.5px;white-space:nowrap;overflow:hidden;cursor:pointer;user-select:none}
.cb-opt .cb-prov{color:var(--muted);flex:0 0 auto}
.cb-opt .cb-raw{color:var(--text);font-weight:600}
.cb-opt:hover,.cb-opt.kb-active{background:var(--accent-dim)}
.cb-opt:hover .cb-raw,.cb-opt.kb-active .cb-raw{color:#b8f5cd}
.cb-opt.mb-sel{border-color:var(--accent)}
.cb-check{color:var(--accent);font-weight:700;flex:0 0 auto}
.cb-empty{min-height:32px;display:flex;align-items:center;padding:7px 10px;color:var(--muted);font-family:var(--mono);font-size:12px;cursor:default}
.cb-more{display:block;width:calc(100% - 16px);margin:4px 8px 8px;padding:7px 10px;border:1px solid var(--border2);border-radius:7px;background:transparent;color:var(--muted);font:12px var(--mono);text-align:center;cursor:pointer}
.cb-more:hover{border-color:var(--accent);color:var(--accent);background:var(--accent-dim)}
</style>
</head>
<body>
<div class="wrap">
  <header class="top">
    <div>
      <h1>Preset <span class="dot">Manager</span></h1>
      <div class="path" id="cfgPath">loading...</div>
    </div>
    <span class="badge" id="activeBadge">Active: ...</span>
  </header>
  <div id="errorBanner" class="errorbox" hidden></div>
  <main>
    <section id="listView">
      <div class="toolbar">
        <button class="btn primary" data-action="add">+ Add preset</button>
        <span class="muted" id="countInfo"></span>
      </div>
      <div class="cards" id="cards"></div>
    </section>
    <section id="editorView" hidden>
      <div class="toolbar space">
        <div class="row">
          <button class="btn" data-action="back">&larr; Back</button>
          <h2 class="edtitle" id="edTitle">New preset</h2>
        </div>
        <div class="row">
          <span class="unsaved" id="unsaved" hidden>&#9679; unsaved changes</span>
          <button class="btn primary" data-action="save">Save preset</button>
        </div>
      </div>
      <div class="confirmbar neutral" id="discardBar" hidden style="margin-bottom:14px">
        Discard unsaved changes?
        <button class="btn danger" id="discardYes">Discard</button>
        <button class="btn" id="discardNo">Keep editing</button>
      </div>
      <div class="namefield">
        <label>Preset name
          <input id="presetName" placeholder="my-preset" spellcheck="false">
        </label>
      </div>
      <div id="agentsGrid">
        <nav class="agent-rail" id="agentRail" aria-label="Agents"></nav>
        <div class="agent-pane" id="agentPane"></div>
      </div>
    </section>
  </main>
  <footer>
    Changes apply on the next OpenCode run; restart OpenCode to apply immediately.
    &nbsp;&middot;&nbsp; Backups kept: 10 (<span id="bakCount">0</span> on disk)
  </footer>
</div>
<div id="tplOverlay" class="overlay" hidden>
  <div class="modal">
    <div class="toolbar space">
      <h3>New preset</h3>
      <button class="btn" id="tplClose" data-action="tpl-close">&times;</button>
    </div>
    <div class="sub">Start from a template, clone one of your presets, or begin empty. You can edit everything afterwards.</div>
    <div id="tplBody"><span class="muted">Loading templates...</span></div>
  </div>
</div>
<div id="toasts"></div>
<script>
"use strict";
const AGENTS=["orchestrator","oracle","librarian","explorer","designer","fixer","observer","council"];
const NAME_RE=/^[a-zA-Z0-9][a-zA-Z0-9._-]*$/;
let config=null, configPath="", backups=0, providerModels={}, editing=null, dirty=false, confirmState=null;
const $=s=>document.querySelector(s);
const esc=s=>String(s).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const lines=v=>v.split("\n").map(t=>t.trim()).filter(Boolean);

function toast(msg,type){
  const d=document.createElement("div");
  d.className="toast"+(type==="error"?" error":"");
  d.textContent=msg;
  $("#toasts").appendChild(d);
  setTimeout(()=>d.remove(),3800);
}
function showError(msg){const b=$("#errorBanner");b.textContent=msg;b.hidden=false;}
function clearError(){$("#errorBanner").hidden=true;}

async function api(path,body){
  const res=await fetch(path,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body||{})});
  let data;try{data=await res.json();}catch(e){data={ok:false,error:"Bad response ("+res.status+")"};}
  if(!data.ok)throw new Error(data.error||"Request failed");
  return data;
}
async function loadConfig(){
  try{
    const res=await fetch("/api/config");
    const data=await res.json();
    if(!data.ok){config=null;showError(data.error||"Failed to load config");}
    else{config=data.config;configPath=data.configPath;backups=(data.backups||[]).length;providerModels=data.providers||{};clearError();}
  }catch(e){config=null;showError("Cannot reach server: "+e.message);}
  renderHeader();renderList();
}
function renderHeader(){
  $("#cfgPath").textContent=configPath||"(config not loaded)";
  $("#activeBadge").textContent="Active: "+(config?(config.preset||"(none)"):"(not loaded)");
  $("#bakCount").textContent=backups;
}
function renderList(){
  const wrapEl=$("#cards");
  if(!config){wrapEl.innerHTML="";$("#countInfo").textContent="";return;}
  const names=Object.keys(config.presets||{});
  $("#countInfo").textContent=names.length+" preset"+(names.length===1?"":"s");
  wrapEl.innerHTML=names.map(n=>{
    const p=(config.presets||{})[n]||{};
    const isActive=config.preset===n;
    const ags=AGENTS.filter(a=>p[a]);
    const ln=ags.slice(0,4).map(a=>a+" \u2192 "+esc(p[a].model||"\u2014")+(p[a].variant?" \u00b7 "+esc(p[a].variant):""));
    if(ags.length>4)ln.push("+"+(ags.length-4)+" more agents");
    let extra="";
    if(confirmState&&confirmState.name===n&&confirmState.type==="delete"){
      extra='<div class="confirmbar">Delete <b>'+esc(n)+'</b>? A backup is written automatically. '+
        '<button class="btn danger" data-action="del-go" data-name="'+esc(n)+'">Delete</button>'+
        '<button class="btn" data-action="del-cancel">Cancel</button></div>';
    }else if(confirmState&&confirmState.name===n&&confirmState.type==="dup"){
      extra='<div class="confirmbar neutral"><input class="dupname" placeholder="new-preset-name" spellcheck="false">'+
        '<button class="btn primary" data-action="dup-go" data-name="'+esc(n)+'">Create copy</button>'+
        '<button class="btn" data-action="dup-cancel">Cancel</button></div>';
    }
    return '<article class="card'+(isActive?" activecard":"")+'">'+
      '<div class="head"><h3>'+esc(n)+'</h3>'+(isActive?'<span class="badge active">active</span>':"")+'</div>'+
      '<div class="summary">'+(ln.length?ln.join("\n"):"(empty preset)")+'</div>'+
      '<div class="row">'+
      (isActive?"":'<button class="btn primary" data-action="activate" data-name="'+esc(n)+'">Activate</button>')+
      '<button class="btn" data-action="edit" data-name="'+esc(n)+'">Edit</button>'+
      '<button class="btn" data-action="duplicate" data-name="'+esc(n)+'">Duplicate</button>'+
      '<button class="btn danger" data-action="delete" data-name="'+esc(n)+'"'+(isActive?' disabled title="Active preset \u2014 activate another first"':'')+'>Delete</button>'+
      '</div>'+extra+'</article>';
  }).join("");
  const dup=document.querySelector(".dupname");
  if(dup)dup.focus();
}
/* ---- split-view editor state + model catalog ---- */
const MB_CAP_NOQ=30,MB_CAP_Q=200;
const cbData={models:[],variants:[],groups:[]};
let activeAgent=AGENTS[0];
let mbState={q:"",showAll:false,flat:[],active:-1};
let edState=null;
let customOpen=false;

function rebuildDatalists(){
  const models=new Set(),variants=new Set(["low","medium","high","max"]);
  const presets=(config&&config.presets)||{};
  Object.values(presets).forEach(p=>Object.values(p).forEach(a=>{
    if(a.model)models.add(a.model);
    if(a.variant)variants.add(a.variant);
  }));
  Object.entries(providerModels||{}).forEach(([prov,list])=>{
    (list||[]).forEach(m=>models.add(prov+"/"+m));
  });
  cbData.models=[...models].sort();
  cbData.variants=[...variants].sort();
  cbData.groups=groupModels(cbData.models);
}
function groupModels(list){
  const map=new Map();
  list.forEach(m=>{const i=m.indexOf("/");const g=i<0?"(other)":m.slice(0,i);if(!map.has(g))map.set(g,[]);map.get(g).push(m);});
  return [...map.entries()].sort((a,b)=>a[0].localeCompare(b[0]));
}
function modelHtml(v){
  const i=v.indexOf("/");
  if(i>0)return '<span class="cb-prov">'+esc(v.slice(0,i+1))+'</span><span class="cb-raw">'+esc(v.slice(i+1))+'</span>';
  return esc(v);
}
function ensureEdState(){
  if(edState)return;
  edState={};
  AGENTS.forEach(ag=>{edState[ag]={model:"",variant:"",skills:"",mcps:""};});
  activeAgent=AGENTS[0];
  mbState={q:"",showAll:false,flat:[],active:-1};
}
function openEditor(name,tplAgents){
  editing=name||null;dirty=false;confirmState=null;
  $("#edTitle").textContent=name?("Edit preset: "+name):"New preset";
  const nameIn=$("#presetName");
  nameIn.value=name||"";nameIn.readOnly=!!name;
  const p=(name&&(config&&config.presets||{})[name])||tplAgents||{};
  edState=null;
  ensureEdState();
  AGENTS.forEach(ag=>{
    const a=p[ag]||{};
    edState[ag]={model:a.model||"",variant:a.variant||"",skills:(a.skills||[]).join("\n"),mcps:(a.mcps||[]).join("\n")};
  });
  activeAgent=AGENTS[0];
  mbState={q:"",showAll:false,flat:[],active:-1};
  customOpen=false;
  $("#unsaved").hidden=true;
  rebuildDatalists();
  showView("editor");
  renderRail();
  renderPane();
  const ms=$("#mbSearch");if(ms)ms.value="";
  window.scrollTo(0,0);
}
function renderRail(){
  const r=$("#agentRail");if(!r)return;
  const done=AGENTS.filter(ag=>edState[ag].model||edState[ag].variant||edState[ag].skills||edState[ag].mcps).length;
  r.innerHTML='<div class="arail-head"><span>Agents</span><span class="muted">'+done+'/8 assigned</span></div>'+
    AGENTS.map(ag=>{
      const st=edState[ag],m=st.model,hasm=!!m;
      const chip=st.variant?'<span class="ar-chip">'+esc(st.variant)+'</span>':"";
      return '<button type="button" class="agent-row'+(ag===activeAgent?" on":"")+'" data-agent="'+esc(ag)+'" aria-pressed="'+(ag===activeAgent)+'">'+
        '<span class="ar-name">'+esc(ag)+'</span>'+
        '<span class="ar-model'+(hasm?"":" ar-none")+'">'+(hasm?modelHtml(m):"unassigned")+'</span>'+chip+'</button>';
    }).join("");
}
function stSummary(){
  const st=edState[activeAgent];
  return st.model?(st.model+(st.variant?" \u00b7 "+st.variant:"")):"unassigned";
}
function paneHtml(){
  const ag=activeAgent,st=edState[ag];
  const isStd=["low","medium","high","max"].indexOf(st.variant)>=0;
  const showCustom=customOpen||(!!st.variant&&!isStd);
  const segs=["low","medium","high","max"].map(v=>'<button type="button" class="seg-btn'+(st.variant===v?" on":"")+'" data-seg="'+v+'" aria-pressed="'+(st.variant===v)+'">'+v+'</button>').join("")+
    '<button type="button" class="seg-btn'+(showCustom?" on":"")+'" data-seg="__custom" aria-pressed="'+showCustom+'">custom</button>';
  return '<div class="pane-target"><h3>'+esc(ag)+'</h3><span class="muted">'+esc(stSummary())+'</span></div>'+
    '<div class="pane-block">'+
      '<div class="pane-label"><span>Model</span><span class="muted">click = assign &amp; next agent &middot; &uarr;/&darr; + Enter</span></div>'+
      '<div class="mb-wrap" id="mbWrap">'+
        '<div class="mb-search"><input id="mbSearch" type="text" placeholder="Filter models (provider or id)..." spellcheck="false" autocomplete="off" role="combobox" aria-expanded="true" aria-controls="mbList" aria-autocomplete="list"></div>'+
        '<div class="mb-list" id="mbList" role="listbox" aria-label="Models"></div>'+
      '</div>'+
    '</div>'+
    '<div class="pane-block">'+
      '<div class="pane-label"><span>Variant</span><span class="muted">low / medium / high / max &middot; or custom</span>'+
        '<button type="button" class="linkbtn" data-allvariant="1" title="Assign this variant to all agents">apply to all</button></div>'+
      '<div class="seg" id="segRow">'+segs+
        '<span class="seg-custom'+(showCustom?"":" hidden")+'" id="segCustom"><input id="variantCustom" type="text" placeholder="custom variant..." spellcheck="false" autocomplete="off" value="'+esc(showCustom?(st.variant||""):"")+'"></span>'+
      '</div>'+
    '</div>'+
    '<div class="pane-block">'+
      '<div class="pane-label"><span>Skills (one per line; * = all, !name = exclude)</span>'+
        '<button type="button" class="linkbtn" data-allskills="1" title="Copy these skills to all agents">apply to all</button></div>'+
      '<textarea id="paneSkills" rows="3" spellcheck="false">'+esc(st.skills)+'</textarea>'+
    '</div>'+
    '<div class="pane-block">'+
      '<div class="pane-label"><span>MCPs (one per line; * = all, !name = exclude)</span>'+
        '<button type="button" class="linkbtn" data-allmcps="1" title="Copy these MCPs to all agents">apply to all</button></div>'+
      '<textarea id="paneMcps" rows="3" spellcheck="false">'+esc(st.mcps)+'</textarea>'+
    '</div>';
}
function renderPane(){
  const pane=$("#agentPane");if(!pane)return;
  pane.innerHTML=paneHtml();
  mbState.flat=[];
  mbState.active=-1;
  renderMb();
  mbSyncActiveToSelection();
}
function mbSyncActiveToSelection(){
  const v=edState[activeAgent].model;
  if(!v){mbSetActive(-1);return;}
  const i=mbState.flat.indexOf(v);
  mbSetActive(i);
}
function mbFilteredGroups(){
  const q=mbState.q.toLowerCase();
  const out=[];
  cbData.groups.forEach(([g,items])=>{
    const shown=q?items.filter(v=>v.toLowerCase().indexOf(q)>=0):items;
    if(shown.length)out.push([g,shown]);
  });
  return out;
}
function renderMb(){
  const listEl=$("#mbList");if(!listEl)return;
  const q=mbState.q;
  const groups=mbFilteredGroups();
  const total=groups.reduce((n,g)=>n+g[1].length,0);
  const cap=mbState.showAll?Infinity:(q?MB_CAP_Q:MB_CAP_NOQ);
  let html="",flat=[];
  groups.forEach(([g,items])=>{
    if(flat.length>=cap)return;
    const shown=items.slice(0,cap-flat.length);
    html+='<div class="cb-group"><span>'+esc(g)+'</span><span class="cb-count">'+items.length+" model"+(items.length===1?"":"s")+'</span></div>';
    shown.forEach(v=>{
      const sel=edState[activeAgent].model===v;
      html+='<div class="cb-opt'+(sel?" mb-sel":"")+'" id="mb-opt-'+flat.length+'" role="option" aria-selected="'+sel+'" data-val="'+esc(v)+'">'+
        (sel?'<span class="cb-check">\u2713 </span>':"")+modelHtml(v)+'</div>';
      flat.push(v);
    });
  });
  let hidden=total-flat.length;
  if(hidden>0&&!mbState.showAll)html+='<button type="button" class="cb-more" data-cbmore="1">Show all \u2014 '+hidden+' more model'+(hidden===1?"":"s")+'</button>';
  if(!flat.length)html='<div class="cb-empty">No matches for \u201c'+esc(q)+'\u201d</div>';
  mbState.flat=flat;
  if(mbState.active>=flat.length)mbState.active=flat.length-1;
  listEl.innerHTML=html;
  mbApplyActive();
}
function mbApplyActive(){
  const listEl=$("#mbList");if(!listEl)return;
  listEl.querySelectorAll(".cb-opt.kb-active").forEach(el=>el.classList.remove("kb-active"));
  const inp=$("#mbSearch");
  if(mbState.active>=0&&mbState.flat[mbState.active]!==undefined){
    const el=listEl.querySelector(".cb-opt[data-val=\""+CSS.escape(mbState.flat[mbState.active])+"\"]");
    if(el){el.classList.add("kb-active");
      if(el.scrollIntoViewIfNeeded)el.scrollIntoViewIfNeeded(false);else el.scrollIntoView({block:"nearest"});
      if(inp)inp.setAttribute("aria-activedescendant",el.id);}
  }else if(inp){
    inp.removeAttribute("aria-activedescendant");
  }
}
function mbSetActive(i){mbState.active=i;mbApplyActive();}
function mbAssign(v){
  edState[activeAgent].model=v;
  markDirty();
  renderRail();
  const tgt=$("#agentPane .pane-target .muted");if(tgt)tgt.textContent=stSummary();
  const nxt=AGENTS[(AGENTS.indexOf(activeAgent)+1)%AGENTS.length];
  setActiveAgent(nxt,{keepScroll:true});
  const ms=$("#mbSearch");
  if(ms)ms.focus();
}
function setActiveAgent(ag,opts){
  opts=opts||{};
  activeAgent=ag;
  mbState.showAll=false;
  customOpen=false;
  const wrap=$("#mbWrap");
  if(opts.keepScroll&&wrap){
    const top=wrap.getBoundingClientRect().top;
    renderPane();
    const d=$("#mbWrap").getBoundingClientRect().top-top;
    window.scrollBy(0,Math.round(d));
  }else{
    renderPane();
  }
  renderRail();
  const ms2=$("#mbSearch");
  if(ms2){ms2.value=mbState.q;if(!opts.keepScroll)ms2.focus();}
}
function segSet(v){
  if(v==="__custom"){
    customOpen=true;
    refreshVariantUI();
    const cinp=$("#variantCustom");
    if(cinp)cinp.focus();
    return;
  }
  customOpen=false;
  const cur=edState[activeAgent].variant;
  edState[activeAgent].variant=v;
  if(cur!==v)markDirty();
  renderRail();refreshVariantUI();
}
function refreshVariantUI(){
  const pane=$("#agentPane");if(!pane)return;
  const st=edState[activeAgent];
  const isStd=["low","medium","high","max"].indexOf(st.variant)>=0;
  const showCustom=customOpen||(!!st.variant&&!isStd);
  pane.querySelectorAll(".seg-btn").forEach(b=>{
    const effOn=b.dataset.seg===st.variant||(b.dataset.seg==="__custom"&&showCustom&&!isStd);
    b.classList.toggle("on",effOn);b.setAttribute("aria-pressed",String(effOn));
  });
  const cwrap=$("#segCustom"),cinp=$("#variantCustom");
  if(cwrap){
    cwrap.classList.toggle("hidden",!showCustom);
    if(showCustom&&cinp&&document.activeElement!==cinp&&cinp.value!==st.variant)cinp.value=st.variant;
  }
  const tgt=pane.querySelector(".pane-target .muted");if(tgt)tgt.textContent=stSummary();
}
function copyToAll(kind){
  const st=edState[activeAgent];
  let v,changed=false;
  if(kind==="variant"){v=st.variant;if(!v){toast("Set a variant first","error");return;}}
  else if(kind==="skills"){v=st.skills;if(!v.trim()){toast("Skills field is empty","error");return;}}
  else{v=st.mcps;if(!v.trim()){toast("MCPs field is empty","error");return;}}
  AGENTS.forEach(ag=>{if(ag!==activeAgent&&edState[ag][kind]!==v){edState[ag][kind]=v;changed=true;}});
  if(changed){markDirty();renderRail();}
  toast(kind+" applied to all agents");
}
function showView(v){
  $("#listView").hidden=v!=="list";
  $("#editorView").hidden=v!=="editor";
  $("#discardBar").hidden=true;
}
async function openTemplatePicker(){
  const ov=$("#tplOverlay");
  ov.hidden=false;
  $("#tplBody").innerHTML='<span class="muted">Loading templates...</span>';
  let data;
  try{
    const res=await fetch("/api/templates");
    data=await res.json();
  }catch(e){$("#tplBody").innerHTML='<span class="muted">Failed to load templates: '+esc(e.message)+'</span>';return;}
  if(!data.ok){$("#tplBody").innerHTML='<span class="muted">Failed to load templates</span>';return;}
  const card=t=>'<button class="tpl" data-action="tpl-use" data-id="'+esc(t.id)+'"><b>'+esc(t.label)+'</b><span>'+esc(t.description||"")+'</span></button>';
  let html="";
  const ex=data.existing||[];
  if(ex.length)html+='<div class="tplgroup"><h4>Clone from your presets</h4><div class="tpls">'+ex.map(card).join("")+'</div></div>';
  const bi=data.builtin||[];
  if(bi.length)html+='<div class="tplgroup"><h4>Starter templates</h4><div class="tpls">'+bi.map(card).join("")+'</div></div>';
  $("#tplBody").innerHTML=html||'<span class="muted">No templates available.</span>';
  window.tplMap={};
  (data.builtin||[]).forEach(t=>window.tplMap[t.id]=t);
  (data.existing||[]).forEach(t=>window.tplMap[t.id]=t);
}
function closeTemplatePicker(){$("#tplOverlay").hidden=true;}
$("#tplOverlay").addEventListener("mousedown",e=>{if(e.target.id==="tplOverlay")closeTemplatePicker();});
document.addEventListener("keydown",e=>{if(e.key==="Escape"&&!$("#tplOverlay").hidden)closeTemplatePicker();});
function markDirty(){if(!dirty){dirty=true;$("#unsaved").hidden=false;}}
async function saveEditor(){
  const name=$("#presetName").value.trim();
  if(!NAME_RE.test(name)){toast("Invalid preset name: use letters, digits, '.', '-' or '_' (start with letter/digit)","error");return;}
  if(!editing&&(config&&config.presets||{})[name]){toast("Preset '"+name+"' already exists — use Edit on its card, or pick another name","error");return;}
  const agents={};
  AGENTS.forEach(ag=>{
    const st=edState?edState[ag]:{model:"",variant:"",skills:"",mcps:""};
    const model=st.model.trim();
    const variant=st.variant.trim();
    const skills=lines(st.skills);
    const mcps=lines(st.mcps);
    if(model||variant||skills.length||mcps.length)agents[ag]={model:model,variant:variant,skills:skills,mcps:mcps};
  });
  try{
    await api("/api/preset",{name:name,agents:agents});
    dirty=false;toast("Preset '"+name+"' saved");
    await loadConfig();showView("list");
  }catch(e){toast(e.message,"error");}
}
async function activate(name){
  try{await api("/api/activate",{name:name});toast("Preset '"+name+"' is now active");await loadConfig();}
  catch(e){toast(e.message,"error");}
}
async function doDelete(name){
  try{await api("/api/delete",{name:name});toast("Preset '"+name+"' deleted");await loadConfig();}
  catch(e){toast(e.message,"error");renderList();}
}
async function doDuplicate(name,newName){
  try{await api("/api/duplicate",{name:name,newName:newName});toast("Copied '"+name+"' to '"+newName+"'");await loadConfig();}
  catch(e){toast(e.message,"error");renderList();}
}
function onBack(){
  if(dirty){$("#discardBar").hidden=false;}
  else showView("list");
}
document.addEventListener("click",async e=>{
  if(e.target.id==="discardYes"){dirty=false;$("#unsaved").hidden=true;showView("list");return;}
  if(e.target.id==="discardNo"){$("#discardBar").hidden=true;return;}
  const b=e.target.closest("[data-action]");if(!b||b.disabled)return;
  const act=b.dataset.action,name=b.dataset.name;
  try{
    if(act==="add")await openTemplatePicker();
    else if(act==="tpl-close")closeTemplatePicker();
    else if(act==="tpl-use"){
      const t=window.tplMap&&window.tplMap[b.dataset.id];
      closeTemplatePicker();
      if(t)openEditor(null,t.agents||{});else openEditor(null);
    }
    else if(act==="edit"){confirmState=null;openEditor(name);}
    else if(act==="activate")await activate(name);
    else if(act==="delete"){confirmState={type:"delete",name:name};renderList();}
    else if(act==="del-cancel"){confirmState=null;renderList();}
    else if(act==="del-go"){confirmState=null;await doDelete(name);}
    else if(act==="duplicate"){confirmState={type:"dup",name:name};renderList();}
    else if(act==="dup-cancel"){confirmState=null;renderList();}
    else if(act==="dup-go"){
      const input=b.closest(".confirmbar").querySelector(".dupname");
      const newName=input.value.trim();
      if(!NAME_RE.test(newName)){toast("Invalid new name: use letters, digits, '.', '-' or '_' (start with letter/digit)","error");input.focus();return;}
      confirmState=null;await doDuplicate(name,newName);
    }
    else if(act==="back")onBack();
    else if(act==="save")await saveEditor();
  }catch(err){toast(err.message,"error");}
});
window.addEventListener("beforeunload",e=>{
  if(dirty){e.preventDefault();e.returnValue="";}
});
$("#agentsGrid").addEventListener("click",e=>{
  const row=e.target.closest(".agent-row");
  if(row){setActiveAgent(row.dataset.agent);return;}
  if(e.target.closest("[data-cbmore]")){mbState.showAll=true;renderMb();return;}
  const seg=e.target.closest("[data-seg]");
  if(seg){segSet(seg.dataset.seg);return;}
  if(e.target.closest("[data-allvariant]")){copyToAll("variant");return;}
  if(e.target.closest("[data-allskills]")){copyToAll("skills");return;}
  if(e.target.closest("[data-allmcps]")){copyToAll("mcps");return;}
  const opt=e.target.closest(".cb-opt");
  if(opt&&opt.dataset.val!==undefined){mbAssign(opt.dataset.val);return;}
});
$("#agentsGrid").addEventListener("input",e=>{
  const t=e.target;
  if(t.id==="mbSearch"){
    mbState.q=t.value;
    mbState.showAll=false;
    renderMb();
  }else if(t.id==="paneSkills"){
    edState[activeAgent].skills=t.value;markDirty();renderRail();
  }else if(t.id==="paneMcps"){
    edState[activeAgent].mcps=t.value;markDirty();renderRail();
  }else if(t.id==="variantCustom"){
    edState[activeAgent].variant=t.value.trim();markDirty();renderRail();
  }
});
$("#agentsGrid").addEventListener("keydown",e=>{
  const t=e.target;
  if(t.id==="mbSearch"){
    if(e.key==="ArrowDown"||e.key==="ArrowUp"){
      e.preventDefault();
      const n=mbState.flat.length;
      if(!n)return;
      let i=mbState.active;
      if(i<0)i=e.key==="ArrowDown"?0:n-1;
      else i=(i+(e.key==="ArrowDown"?1:-1)+n)%n;
      mbSetActive(i);
    }else if(e.key==="Enter"){
      e.preventDefault();
      if(mbState.active>=0&&mbState.flat[mbState.active]!==undefined)mbAssign(mbState.flat[mbState.active]);
    }else if(e.key==="Escape"){
      e.preventDefault();t.blur();
    }
  }else if(t.id==="variantCustom"&&e.key==="Enter"){
    e.preventDefault();t.blur();
  }
});
["input","change"].forEach(ev=>document.addEventListener(ev,e=>{
  if(e.target.id==="mbSearch")return;
  if(e.target.closest("#editorView"))markDirty();
},true));
loadConfig();
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    server_version = "PresetManager/1.0"

    def log_message(self, fmt, *args):  # keep the console clean
        pass

    def _send(self, code, payload, ctype="application/json; charset=utf-8"):
        if isinstance(payload, str):
            body = payload.encode("utf-8")
        else:
            body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/":
            self._send(200, HTML, "text/html; charset=utf-8")
            return
        if path == "/api/config":
            app = self.server.app
            with app.lock:
                cfg, err = load_config(app.config_path)
                if err:
                    self._send(500, {"ok": False, "error": err})
                    return
                self._send(200, {
                    "ok": True,
                    "config": cfg,
                    "configPath": app.config_path,
                    "backups": list_backups(app.config_path),
                    "providers": get_provider_models(os.path.dirname(app.config_path)),
                })
            return
        if path == "/api/templates":
            app = self.server.app
            with app.lock:
                cfg, err = load_config(app.config_path)
                if err:
                    cfg = {}
                existing = []
                presets = cfg.get("presets") or {}
                if isinstance(presets, dict):
                    for name in sorted(presets):
                        p = presets[name] or {}
                        ags = [a for a in AGENT_NAMES if p.get(a)]
                        desc = ", ".join(ags[:4]) + ("…" if len(ags) > 4 else "")
                        existing.append({
                            "id": "clone:" + name,
                            "label": name,
                            "description": "Clone existing preset (%s)" % (desc or "empty"),
                            "agents": copy.deepcopy(p),
                        })
                self._send(200, {
                    "ok": True,
                    "builtin": BUILTIN_TEMPLATES,
                    "existing": existing,
                })
            return
        self._send(404, {"ok": False, "error": "Not found"})

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                raise ApiError("Payload too large", 413)
            raw = self.rfile.read(length) if length else b""
            try:
                body = json.loads(raw.decode("utf-8")) if raw else {}
            except Exception:
                raise ApiError("Body must be valid JSON")
            if not isinstance(body, dict):
                raise ApiError("Body must be a JSON object")
            app = self.server.app
            with app.lock:
                message = self._mutate(app, path, body)
            self._send(200, {"ok": True, "message": message})
        except ApiError as e:
            self._send(e.status, {"ok": False, "error": str(e)})
        except BrokenPipeError:
            pass
        except Exception as e:
            self._send(500, {"ok": False, "error": "%s: %s" % (type(e).__name__, e)})

    def _mutate(self, app, path, body):
        cfg, err = load_config(app.config_path)
        if err:
            raise ApiError(err, 500)
        presets = cfg.setdefault("presets", {})
        if not isinstance(presets, dict):
            raise ApiError("'presets' in config must be an object", 500)

        if path == "/api/preset":
            name = valid_name(body.get("name"))
            presets[name] = sanitize_agents(body.get("agents"))
            msg = "Preset '%s' saved" % name

        elif path == "/api/activate":
            name = valid_name(body.get("name"))
            if name not in presets:
                raise ApiError("Preset '%s' does not exist" % name, 404)
            cfg["preset"] = name
            msg = "Preset '%s' activated" % name

        elif path == "/api/delete":
            name = valid_name(body.get("name"))
            if name not in presets:
                raise ApiError("Preset '%s' does not exist" % name, 404)
            if cfg.get("preset") == name:
                raise ApiError("Preset is active — activate another preset first")
            del presets[name]
            msg = "Preset '%s' deleted" % name

        elif path == "/api/duplicate":
            name = valid_name(body.get("name"))
            new = valid_name(body.get("newName"), field="new name")
            if name not in presets:
                raise ApiError("Preset '%s' does not exist" % name, 404)
            if new in presets:
                raise ApiError("Preset '%s' already exists" % new)
            presets[new] = copy.deepcopy(presets[name])
            msg = "Preset '%s' copied to '%s'" % (name, new)

        else:
            raise ApiError("Not found", 404)

        backup_config(app.config_path)
        atomic_write_json(app.config_path, cfg)
        return msg


def main():
    parser = argparse.ArgumentParser(description="Preset Manager for oh-my-opencode-slim")
    parser.add_argument("--config", default=DEFAULT_CONFIG, help="path to oh-my-opencode-slim.json")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.app = App(args.config)
    print("Preset Manager  ->  http://%s:%d" % (args.host, args.port))
    print("Config file     ->  %s" % server.app.config_path)
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
