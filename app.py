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


# ------------------------------------------- opencode.jsonc management

def load_opencode_config(path):
    """Load opencode.jsonc. Returns (dict, None) or (None, error)."""
    if not os.path.exists(path):
        return None, "opencode.jsonc not found"
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.loads(strip_jsonc(f.read()))
    except Exception as e:
        return None, "Invalid opencode.jsonc: %s" % e
    if not isinstance(data, dict):
        return None, "opencode.jsonc root must be a JSON object"
    return data, None


def save_opencode_config(path, data):
    """Write opencode.jsonc atomically with backup."""
    backup_config(path)
    atomic_write_json(path, data)


def get_opencode_provider_models(config_path):
    """Get the omniroute provider models from opencode.jsonc.

    Returns {providerName: {modelId: {...}}} or {}."""
    data, err = load_opencode_config(config_path)
    if err:
        return {}
    providers = data.get("provider")
    if not isinstance(providers, dict):
        return {}
    result = {}
    for pname, pdef in providers.items():
        if not isinstance(pdef, dict):
            continue
        models = pdef.get("models")
        if isinstance(models, dict) and models:
            result[pname] = models
    return result


def set_opencode_provider_models(config_path, provider_name, models):
    """Set the models for a provider in opencode.jsonc.

    config_path is the omo slim config path; opencode.jsonc is in the same dir."""
    cfg_dir = os.path.dirname(config_path)
    oc_path = os.path.join(cfg_dir, "opencode.jsonc")
    data, err = load_opencode_config(oc_path)
    if err:
        raise ApiError(err, 500)
    if "provider" not in data or not isinstance(data["provider"], dict):
        data["provider"] = {}
    if provider_name not in data["provider"]:
        data["provider"][provider_name] = {}
    data["provider"][provider_name]["models"] = models
    save_opencode_config(oc_path, data)
    return data


# ------------------------------------------------- live model list (proxy API)

MODELS_TTL = 300  # seconds

# Curated providers pulled from the public models.dev registry (the same
# registry OpenCode uses). Keys must match models.dev provider ids.
CURATED_MODELSDEV = [
    "anthropic", "openai", "google", "opencode", "opencode-go", "deepseek", "xai",
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
        # Cache the combined result so fast reads see the full catalog.
        with _live_lock:
            _live_cache["data"] = combined
            _live_cache["ts"] = time.time()
        return combined
    return merged


_warming = False


def get_provider_models_fast(config_dir):
    """Return provider models from cache only — never blocks on network.

    Falls back to static entries from opencode.jsonc (fast disk read).
    When the cache is stale/missing, triggers a background refresh."""
    now = time.time()
    with _live_lock:
        fresh = _live_cache["data"] and now - _live_cache["ts"] < MODELS_TTL
        recent_fail = now - _live_cache["fail_ts"] < 60
        cached = _live_cache["data"]
    if fresh:
        return cached
    if recent_fail:
        return cached or load_provider_models(config_dir)
    warm_provider_models(config_dir)
    return load_provider_models(config_dir)


def warm_provider_models(config_dir):
    """Populate the provider-models cache in a background thread (daemon).

    No-op if a warm-up is already in flight."""
    global _warming
    with _live_lock:
        if _warming:
            return
        _warming = True

    def _warm():
        global _warming
        try:
            get_provider_models(config_dir)
        except Exception:
            pass
        finally:
            with _live_lock:
                _warming = False

    threading.Thread(target=_warm, daemon=True).start()


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
.btn:active:not(:disabled){transform:translateY(1px)}
.btn:focus-visible{outline:none;border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-dim)}
/* --- preset cards (list view) --- */
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:14px}
.card{position:relative;background:var(--panel);border:1px solid var(--border);border-radius:12px;padding:16px;display:flex;flex-direction:column;gap:11px;transition:border-color .16s,transform .16s,box-shadow .16s;animation:cardIn .22s ease-out both}
.card:nth-child(2){animation-delay:.04s}
.card:nth-child(3){animation-delay:.08s}
.card:nth-child(4){animation-delay:.12s}
.card:hover{border-color:var(--border2);transform:translateY(-2px);box-shadow:0 10px 26px rgba(0,0,0,.32)}
.card.activecard{border-color:var(--accent);box-shadow:inset 3px 0 0 var(--accent)}
.card.activecard:hover{border-color:var(--accent);box-shadow:inset 3px 0 0 var(--accent),0 10px 26px rgba(0,0,0,.32)}
.card .head{display:flex;justify-content:space-between;align-items:center;gap:8px}
.card h3{margin:0;font-size:15px;font-family:var(--mono);font-weight:600;word-break:break-all}
.card.activecard h3{color:var(--accent)}
.summary{font-family:var(--mono);font-size:11.5px;color:var(--muted);background:var(--panel2);border:1px solid var(--border);border-radius:8px;padding:4px 10px;display:flex;flex-direction:column;flex:1;overflow:hidden}
.srow{display:flex;align-items:baseline;justify-content:space-between;gap:12px;padding:5px 0;border-bottom:1px solid var(--border)}
.srow:last-child{border-bottom:none}
.sname{color:var(--muted);font-size:10.5px;text-transform:uppercase;letter-spacing:.6px;flex:0 0 auto}
.smodel{display:flex;align-items:center;justify-content:flex-end;gap:6px;min-width:0;flex:1 1 auto}
.smodelTxt{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;text-align:right}
.summary .cb-prov{color:var(--muted)}
.summary .cb-raw{color:var(--text);font-weight:600}
.svar{flex:0 0 auto;white-space:nowrap;color:var(--accent);font-size:10.5px;border:1px solid var(--accent-dim);border-radius:999px;padding:0 6px;margin-left:0}
.smore{color:var(--muted);font-size:10.5px;padding:5px 0;font-style:italic}
.row{display:flex;gap:8px;flex-wrap:wrap}
.card .row .btn{flex:1;text-align:center;white-space:nowrap}
@keyframes cardIn{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}
/* --- empty state --- */
.emptycards{grid-column:1/-1;display:flex;flex-direction:column;align-items:center;text-align:center;gap:12px;padding:46px 22px;border:1px dashed var(--border2);border-radius:14px;background:var(--panel);animation:cardIn .22s ease-out both}
.emptycards .e-ico{color:var(--border2)}
.emptycards h3{margin:0;font-size:16px;font-weight:650}
.emptycards p{margin:0;color:var(--muted);font-size:12.5px;line-height:1.55;max-width:46ch}
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
/* chip layout for the editor model browser (mirrors provider catalog pills) */
.mb-tabs{display:flex;flex-wrap:wrap;gap:6px;padding:8px 10px;border-bottom:1px solid var(--border);background:var(--panel);max-height:112px;overflow-y:auto;scrollbar-width:thin;scrollbar-color:var(--border2) transparent}
.mb-chips{display:flex;flex-wrap:wrap;gap:5px;padding:6px}
#mbList .cb-opt{min-height:0;min-width:0;max-width:100%;padding:4px 9px;border:1px solid var(--border);background:var(--panel2);border-radius:999px;font-size:11.5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#mbList .cb-opt .cb-prov{flex:0 0 auto}
#mbList .cb-opt:hover,#mbList .cb-opt.kb-active{border-color:var(--accent);background:var(--accent-dim)}
#mbList .cb-opt.mb-sel{border-color:var(--accent)}
#mbList .cb-opt .cb-check{margin-right:1px}
#mbList .cb-more{width:100%;margin:6px 0 2px}
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
/* --- provider models view --- */
.pm-toolbar{display:flex;gap:12px;align-items:center;margin-bottom:14px;flex-wrap:wrap}
.pm-toolbar .muted{flex:1}
.pm-provider{border:1px solid var(--border);border-radius:12px;padding:16px;background:var(--panel);margin-bottom:14px}
.pm-provider h3{margin:0 0 10px;font-size:15px;font-family:var(--mono);color:var(--accent)}
.pm-model-list{display:flex;flex-wrap:wrap;gap:6px}
.pm-chip{display:inline-flex;align-items:center;gap:5px;padding:4px 10px;border-radius:999px;border:1px solid var(--border2);background:var(--panel2);font-family:var(--mono);font-size:12px;color:var(--text);cursor:pointer;transition:border-color .15s,background .15s}
.pm-chip:hover{border-color:var(--danger);background:#2a1414}
.pm-chip .pm-remove{color:var(--danger);font-weight:700}
.pm-add-section{margin-top:12px;display:flex;gap:8px;flex-wrap:wrap;align-items:flex-start}
.pm-add-section input{flex:1;min-width:200px}
.pm-empty{color:var(--muted);font-style:italic;padding:12px 0}
.pm-badge{display:inline-block;padding:3px 11px;border-radius:999px;font-size:12px;border:1px solid var(--accent);color:var(--accent);background:var(--accent-dim);margin-left:8px}
/* --- provider-models: add input + autocomplete --- */
.pm-add-wrap{position:relative;flex:1 1 260px;min-width:220px}
.pm-add-wrap input{width:100%}
.pm-ac{position:absolute;left:0;right:0;top:calc(100% + 4px);z-index:30;background:#0d1117;border:1px solid var(--border2);border-radius:10px;box-shadow:0 18px 44px rgba(0,0,0,.55);padding:4px;max-height:min(340px,50vh);overflow-y:auto;overscroll-behavior:contain;scrollbar-width:thin;scrollbar-color:var(--border2) transparent}
.pm-ac-head{padding:6px 10px 4px;font-size:10.5px;text-transform:uppercase;letter-spacing:.9px;color:var(--accent)}
.pm-ac .cb-opt{border-radius:7px}
.pm-ac .cb-opt .cb-check{margin-left:auto}
.pm-ac-hint{padding:7px 10px;color:var(--muted);font-family:var(--mono);font-size:11.5px;line-height:1.5}
/* --- provider-models: catalog (provider tabs + model panel) --- */
.pm-cat{margin-top:12px;border:1px solid var(--border);border-radius:12px;background:#0d1117;overflow:hidden}
.pm-cat-search{border-bottom:1px solid var(--border)}
.pm-cat-search input{border:none;border-radius:0;font-family:var(--mono);font-size:12.5px}
.pm-cat-search input:focus{outline:none;border:none}
.pm-cat-tabs{display:flex;flex-wrap:wrap;gap:6px;padding:10px 12px;border-bottom:1px solid var(--border);background:var(--panel)}
.pm-cat-pill{min-height:30px;display:inline-flex;align-items:center;gap:7px;padding:5px 12px;border:1px solid var(--border2);border-radius:999px;background:transparent;color:var(--muted);font:12px var(--mono);cursor:pointer;transition:border-color .12s,background .12s,color .12s}
.pm-cat-pill:hover{border-color:var(--muted);color:var(--text)}
.pm-cat-pill.on{background:var(--accent-dim);border-color:var(--accent);color:var(--accent);font-weight:600}
.pm-cat-pill.dim{opacity:.4}
.pm-cat-pill .pcount{color:var(--muted);font-size:10.5px;border:1px solid var(--border2);border-radius:999px;padding:0 6px}
.pm-cat-pill.on .pcount{color:var(--accent);border-color:var(--accent)}
.pm-cat-pill:focus-visible{outline:none;box-shadow:0 0 0 3px var(--accent-dim)}
.pm-cat-panel{max-height:min(460px,52vh);overflow-y:auto;overscroll-behavior:contain;padding:10px 12px 12px;scrollbar-width:thin;scrollbar-color:var(--border2) transparent}
.pm-cat-models{display:flex;flex-wrap:wrap;gap:5px}
.pm-cat-models .cb-opt{border:1px solid var(--border);background:var(--panel2);border-radius:999px;font-size:11.5px;padding:4px 9px}
.pm-cat-models .cb-opt.mb-sel{border-color:var(--accent)}
.pm-cat-foot{padding:7px 12px 10px;border-top:1px solid var(--border)}
/* --- home / start menu --- */
#homeView{display:flex;align-items:center;justify-content:center;min-height:calc(100vh - 260px);padding:14px 0}
.home{width:100%;max-width:840px;margin:0 auto;text-align:center}
.home .eyebrow{display:inline-flex;align-items:center;gap:7px;font-family:var(--mono);font-size:11px;text-transform:uppercase;letter-spacing:1.4px;color:var(--accent);margin-bottom:16px;padding:4px 12px;border:1px solid var(--border2);border-radius:999px;background:var(--panel);animation:fadeUp .4s ease-out both}
.home .eyebrow::before{content:"";width:6px;height:6px;border-radius:50%;background:var(--accent);box-shadow:0 0 10px var(--accent)}
.home h2{margin:0 0 12px;font-size:30px;line-height:1.15;letter-spacing:-.2px;font-weight:680;animation:fadeUp .45s ease-out .04s both}
.home .lead{color:var(--muted);font-size:13.5px;line-height:1.6;max-width:54ch;margin:0 auto 32px;animation:fadeUp .45s ease-out .08s both}
.home-options{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px;text-align:left}
.option{position:relative;width:100%;display:flex;flex-direction:column;align-items:flex-start;gap:9px;text-align:left;background:var(--panel);border:1px solid var(--border);border-radius:14px;padding:22px 22px 18px;color:var(--text);font:inherit;cursor:pointer;overflow:hidden;transition:border-color .16s,background .16s,transform .16s,box-shadow .16s}
.option:nth-child(1){animation:fadeUpIn .5s ease-out .12s both}
.option:nth-child(2){animation:fadeUpIn .5s ease-out .22s both}
.option .opt-ico{width:38px;height:38px;display:flex;align-items:center;justify-content:center;border:1px solid var(--border2);border-radius:11px;background:var(--panel2);color:var(--muted);transition:border-color .16s,color .16s,background .16s,transform .16s}
.option .opt-ico svg{width:20px;height:20px;display:block}
.option::after{content:"\2192";position:absolute;top:22px;right:22px;font-size:18px;line-height:1;color:var(--muted);transition:color .16s,transform .16s}
.option:hover{border-color:var(--accent);background:var(--panel2);transform:translateY(-3px);box-shadow:0 16px 38px rgba(0,0,0,.42),0 0 0 1px var(--accent-dim)}
.option:hover .opt-ico{border-color:var(--accent);color:var(--accent);background:var(--accent-dim);transform:scale(1.06)}
.option:hover::after{color:var(--accent);transform:translateX(5px)}
.option:active:not(.disabled){transform:translateY(-1px)}
.option:focus-visible{outline:none;border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-dim)}
.option .opt-kicker{font-family:var(--mono);font-size:10.5px;text-transform:uppercase;letter-spacing:1.1px;color:var(--accent)}
.option .opt-title{font-size:18px;font-weight:650;font-family:var(--mono)}
.option .opt-desc{color:var(--muted);font-size:12.5px;line-height:1.5;max-width:36ch}
.option .opt-meta{font-family:var(--mono);font-size:11px;color:var(--muted);border-top:1px solid var(--border);padding-top:10px;margin-top:auto;width:100%}
.option .opt-hint{font-family:var(--mono);font-size:11px;color:var(--danger);width:100%}
.option.disabled{cursor:not-allowed;opacity:.55;border-style:dashed}
.option.disabled .opt-ico{border-style:dashed}
.option.disabled::after{content:"\2014"}
.option.disabled:hover{transform:none;border-color:var(--border);background:var(--panel);box-shadow:none}
.option.disabled:hover .opt-ico{transform:none;border-color:var(--border2);color:var(--muted);background:var(--panel2)}
.option.disabled:hover::after{color:var(--muted);transform:none}
@keyframes fadeUp{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}
@keyframes fadeUpIn{from{opacity:0;transform:translateY(12px) scale(.99)}to{opacity:1;transform:none}}
@media(max-width:640px){
  #homeView{min-height:calc(100vh - 220px)}
  .home h2{font-size:23px}
  .home-options{grid-template-columns:1fr}
  .cards{grid-template-columns:1fr}
  .srow{gap:8px}
  .sname{font-size:10px}
  .smodel{font-size:11px}
}
@media(prefers-reduced-motion:reduce){
  *,*::before,*::after{animation-duration:.001ms!important;animation-delay:0s!important;transition-duration:.001ms!important}
}
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
    <section id="homeView">
      <div class="home">
        <div class="eyebrow">Start here</div>
        <h2>What do you want to manage?</h2>
        <p class="lead">Pick an area below. Presets define which model each agent uses; Provider Models control the model list available in opencode.jsonc.</p>
        <div class="home-options">
          <button type="button" class="option" data-action="go-presets" aria-label="Manage presets">
            <span class="opt-ico" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M4 6h16M4 12h16M4 18h10"/><circle cx="18" cy="18" r="2.4"/></svg></span>
            <span class="opt-kicker">Agent config</span>
            <span class="opt-title">Presets</span>
            <span class="opt-desc">Create, edit and activate agent presets.</span>
            <span class="opt-meta" id="homePresetMeta">presets &middot; model, variant, skills, MCPs</span>
          </button>
          <button type="button" class="option" data-action="pm-open" id="homeProviderOption" aria-label="Manage provider models">
            <span class="opt-ico" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="6" rx="1.6"/><rect x="3" y="14" width="18" height="6" rx="1.6"/><path d="M7 7h.01M7 17h.01"/></svg></span>
            <span class="opt-kicker">opencode.jsonc</span>
            <span class="opt-title">Provider Models</span>
            <span class="opt-desc">Manage the model list for your provider in opencode.jsonc.</span>
            <span class="opt-meta" id="homeProviderMeta">model list &middot; add or remove models</span>
          </button>
        </div>
      </div>
    </section>
    <section id="listView" hidden>
      <div class="toolbar">
        <button class="btn" data-action="home">&larr; Home</button>
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
    <section id="providerModelsView" hidden>
      <div class="pm-toolbar">
        <button class="btn" data-action="pm-back">&larr; Home</button>
        <h2 style="margin:0;font-size:16px;font-weight:600">Provider Models</h2>
        <span class="muted" id="pmProviderName"></span>
        <span class="pm-badge" id="pmBadge">opencode.jsonc</span>
      </div>
      <div id="pmContent"></div>
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
  if(!names.length){
    wrapEl.innerHTML='<div class="emptycards">'+
      '<span class="e-ico" aria-hidden="true"><svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="16" rx="2.4"/><path d="M3 9h18M8 14h8"/></svg></span>'+
      '<h3>No presets yet</h3>'+
      '<p>Presets let you switch the model, variant, skills and MCPs for every agent at once. Create your first one to get started.</p>'+
      '<button class="btn primary" data-action="add">+ Add preset</button>'+
      '</div>';
    return;
  }
  wrapEl.innerHTML=names.map(n=>{
    const p=(config.presets||{})[n]||{};
    const isActive=config.preset===n;
    const ags=AGENTS.filter(a=>p[a]);
    const shown=ags.slice(0,4);
    const rows=shown.length?shown.map(a=>{
      const model=p[a].model||"";
      const variant=p[a].variant||"";
      const modelHtmlTxt=model?modelHtml(model):'<span class="cb-raw" style="color:var(--muted);font-style:italic">no model</span>';
      return '<div class="srow"><span class="sname">'+esc(a)+'</span><span class="smodel">'+
        '<span class="smodelTxt"'+(model?' title="'+esc(model)+'"':"")+'>'+modelHtmlTxt+'</span>'+
        (variant?'<span class="svar" title="variant: '+esc(variant)+'">'+esc(variant)+'</span>':"")+'</span></div>';
    }).join(""):'<div class="srow"><span class="sname">agents</span><span class="smodel"><span class="smodelTxt" style="color:var(--muted);font-style:italic">empty preset</span></span></div>';
    const more=ags.length>4?'<div class="smore">+'+(ags.length-4)+' more agent'+(ags.length-4===1?"":"s")+'</div>':"";
    let extra="";
    if(confirmState&&confirmState.name===n&&confirmState.type==="delete"){
      extra='<div class="confirmbar" role="alertdialog" aria-label="Confirm delete preset">'+
        '<span>Delete <b>'+esc(n)+'</b>? A backup is written automatically.</span>'+
        '<button class="btn danger" data-action="del-go" data-name="'+esc(n)+'">Delete</button>'+
        '<button class="btn" data-action="del-cancel">Cancel</button></div>';
    }else if(confirmState&&confirmState.name===n&&confirmState.type==="dup"){
      extra='<div class="confirmbar neutral"><label style="flex:1;min-width:160px;flex-direction:column">New preset name'+
        '<input class="dupname" placeholder="new-preset-name" spellcheck="false" aria-label="New preset name"></label>'+
        '<button class="btn primary" data-action="dup-go" data-name="'+esc(n)+'">Create copy</button>'+
        '<button class="btn" data-action="dup-cancel">Cancel</button></div>';
    }
    return '<article class="card'+(isActive?" activecard":"")+'" data-name="'+esc(n)+'">'+
      '<div class="head"><h3>'+esc(n)+'</h3>'+(isActive?'<span class="badge active" title="This preset is currently active">active</span>':"")+'</div>'+
      '<div class="summary">'+rows+more+'</div>'+
      '<div class="row">'+
      (isActive?"":'<button class="btn primary" data-action="activate" data-name="'+esc(n)+'">Activate</button>')+
      '<button class="btn" data-action="edit" data-name="'+esc(n)+'" aria-label="Edit preset '+esc(n)+'">Edit</button>'+
      '<button class="btn" data-action="duplicate" data-name="'+esc(n)+'" aria-label="Duplicate preset '+esc(n)+'">Duplicate</button>'+
      '<button class="btn danger" data-action="delete" data-name="'+esc(n)+'"'+(isActive?' disabled title="Active preset — activate another preset first" aria-label="Delete preset '+esc(n)+' (disabled: preset is active)"':' aria-label="Delete preset '+esc(n)+'"')+'>Delete</button>'+
      '</div>'+extra+'</article>';
  }).join("");
  const dup=document.querySelector(".dupname");
  if(dup)dup.focus();
}
/* ---- provider models view state ---- */
let pmProvider="omniroute", pmModels={}, pmHasOpencodeJsonc=false;
let pmCatalogOpen=false, pmQuery="", pmActive=-1, pmCatQuery="", pmCatProvider="", pmCatShowAll=false;
const PM_CAP=8, PM_CAT_CAP=48;
let pmSuggestions=[];

/* Fully qualified catalog list: {full,provider,raw}. */
function pmCatalogList(){
  const out=[];
  const src=providerModels||{};
  Object.keys(src).forEach(prov=>{
    (src[prov]||[]).forEach(raw=>{out.push({full:prov+"/"+raw,provider:prov,raw:raw});});
  });
  return out;
}
function pmFilteredCatalog(){
  const q=pmQuery.trim().toLowerCase();
  return pmCatalogList().filter(it=>!q||it.full.toLowerCase().includes(q));
}
function pmShowAutocomplete(){
  const ac=$("#pmAc");if(!ac)return;
  const inp=$("#pmAddInput");
  const q=pmQuery.trim();
  if(!q){ac.hidden=true;ac.innerHTML="";pmSuggestions=[];pmActive=-1;if(inp){inp.setAttribute("aria-expanded","false");inp.removeAttribute("aria-activedescendant");}return;}
  const all=pmFilteredCatalog();
  const shown=all.slice(0,PM_CAP);
  pmSuggestions=shown.map(it=>it.full);
  if(pmActive>=pmSuggestions.length)pmActive=pmSuggestions.length?pmSuggestions.length-1:-1;
  let html="";
  if(!pmCatalogList().length){
    html='<div class="pm-ac-hint">Catalog unavailable (models.dev not loaded). Press Add to use the id as-is.</div>';
  }else if(!all.length){
    html='<div class="pm-ac-hint">No catalog match for &ldquo;'+esc(q)+'&rdquo;. Press Add to use it as-is.</div>';
  }else{
    html='<div class="pm-ac-head">Suggestions</div>';
    shown.forEach((it,i)=>{
      const added=!!pmModels[it.full];
      html+='<div class="cb-opt'+(added?" mb-sel":"")+(i===pmActive?" kb-active":"")+'" id="pm-ac-opt-'+i+'" role="option" aria-selected="'+(i===pmActive)+'" data-pm-sug="'+esc(it.full)+'">'+
        (added?'<span class="cb-check">\u2713 </span>':"")+modelHtml(it.full)+'</div>';
    });
    if(all.length>shown.length){
      html+='<button type="button" class="cb-more" data-pm-ac-more="1">Show all \u2014 '+(all.length-shown.length)+' more match'+(all.length-shown.length===1?"":"es")+'</button>';
    }
  }
  ac.innerHTML=html;
  ac.hidden=false;
  if(inp){inp.setAttribute("aria-expanded","true");pmApplyAcActive();}
}
function pmApplyAcActive(){
  const ac=$("#pmAc");if(!ac||ac.hidden)return;
  ac.querySelectorAll(".cb-opt.kb-active").forEach(el=>el.classList.remove("kb-active"));
  const inp=$("#pmAddInput");
  if(pmActive>=0&&pmSuggestions[pmActive]!==undefined){
    const el=ac.querySelector('.cb-opt[data-pm-sug="'+CSS.escape(pmSuggestions[pmActive])+'"]');
    if(el){el.classList.add("kb-active");el.scrollIntoView({block:"nearest"});
      if(inp)inp.setAttribute("aria-activedescendant","pm-ac-opt-"+pmActive);}
  }else if(inp){inp.removeAttribute("aria-activedescendant");}
}
function pmHideAutocomplete(){
  const ac=$("#pmAc");if(ac){ac.hidden=true;ac.innerHTML="";}
  pmActive=-1;pmSuggestions=[];
  const inp=$("#pmAddInput");
  if(inp){inp.setAttribute("aria-expanded","false");inp.removeAttribute("aria-activedescendant");}
}
/* Add one fully qualified id; returns false when blank or already present. */
function pmCommit(full){
  if(!full)return false;
  if(pmModels[full]){toast("Model already in list","error");return false;}
  pmModels[full]={name:full};
  renderPMModels();
  pmMarkCatalogItem(full,true);
  return true;
}
/* Reflect added/available state on a catalog chip without rebuilding the catalog. */
function pmMarkCatalogItem(full,added){
  const el=document.querySelector('#pmCatalog [data-pm-cat-add="'+CSS.escape(full)+'"]');
  if(!el)return;
  el.classList.toggle("mb-sel",added);
  el.title=(added?"Already added":"Add ")+full;
  const check=el.querySelector(".cb-check");
  if(added&&!check)el.insertAdjacentHTML("afterbegin",'<span class="cb-check">\u2713 </span>');
  else if(!added&&check)check.remove();
}

async function loadProviderModelsView(){
  pmCatalogOpen=false;pmQuery="";pmCatQuery="";pmCatProvider="";pmCatShowAll=false;
  try{
    const res=await fetch("/api/opencode-config");
    const data=await res.json();
    if(!data.ok){toast(data.error||"Failed to load opencode.jsonc","error");return;}
    pmHasOpencodeJsonc=true;
    const cfg=data.config;
    const providers=cfg&&cfg.provider||{};
    const provNames=Object.keys(providers);
    if(!provNames.length){toast("No providers found in opencode.jsonc","error");return;}
    pmProvider=provNames[0];
    const provData=providers[pmProvider]||{};
    pmModels=provData.models||{};
    renderProviderModelsView();
    showView("providerModels");
  }catch(e){toast("Cannot load opencode.jsonc: "+e.message,"error");}
}

/* Full (re)build of the view shell. Only called on load; add/remove update in place. */
function renderProviderModelsView(){
  const content=$("#pmContent");if(!content)return;
  const provNameEl=$("#pmProviderName");
  if(provNameEl)provNameEl.textContent=pmProvider;
  const modelEntries=Object.entries(pmModels);
  let html='<div class="pm-provider">'+
    '<h3>'+esc(pmProvider)+' <span class="muted" id="pmCount" style="font-size:11px;text-transform:none;letter-spacing:0">('+modelEntries.length+' model'+(modelEntries.length!==1?"s":"")+')</span></h3>'+
    '<div class="pm-model-list" id="pmModelList"></div>'+
    '<div class="pm-add-section">'+
    '<div class="pm-add-wrap">'+
    '<input id="pmAddInput" type="text" placeholder="Type a model id (e.g. MiniMax) or paste one" spellcheck="false" autocomplete="off" role="combobox" aria-expanded="false" aria-controls="pmAc" aria-autocomplete="list" aria-label="Add model id">'+
    '<div class="pm-ac" id="pmAc" hidden></div>'+
    '</div>'+
    '<button class="btn primary" data-action="pm-add">Add</button>'+
    '<button class="btn" data-action="pm-toggle-cat" id="pmCatToggle" aria-expanded="false">Browse catalog</button>'+
    '</div>'+
    '<div id="pmCatalog"></div>'+
    '</div>';
  html+='<div style="margin-top:14px;display:flex;gap:8px">'+
    '<button class="btn primary" data-action="pm-save">Save to opencode.jsonc</button>'+
    '<button class="btn" data-action="pm-back">Cancel</button>'+
    '</div>';
  content.innerHTML=html;
  renderPMModels();
  pmRenderCatalog();
}
/* In-place refresh of the chip list + count (never touches the input). */
function renderPMModels(){
  const list=$("#pmModelList");if(!list)return;
  const ids=Object.keys(pmModels);
  if(!ids.length){
    list.innerHTML='<span class="pm-empty">No models declared yet. Add models below.</span>';
  }else{
    list.innerHTML=ids.map(mid=>'<span class="pm-chip" data-pm-remove="'+esc(mid)+'" title="Click to remove">'+esc(mid)+' <span class="pm-remove">&times;</span></span>').join("");
  }
  const c=$("#pmCount");
  if(c)c.textContent="("+ids.length+" model"+(ids.length!==1?"s":"")+")";
}
/* Catalog: provider pills select which provider's model panel is shown.
   Regions (#pmCatTabs / #pmCatPanel / #pmCatFoot) are updated surgically so the
   search input, its focus/caret, and the page scroll position are never disturbed. */
function pmRenderCatalog(){
  const host=$("#pmCatalog");if(!host)return;
  const tgl=$("#pmCatToggle");
  if(tgl){tgl.textContent=pmCatalogOpen?"Hide catalog":"Browse catalog";tgl.setAttribute("aria-expanded",String(pmCatalogOpen));}
  if(!pmCatalogOpen){
    host.innerHTML="";
    if(tgl)tgl.hidden=false;
    return;
  }
  const all=pmCatalogList();
  if(!all.length){
    host.innerHTML='<div class="pm-cat"><div class="pm-cat-panel"><span class="pm-empty">No catalog available. Ensure models.dev is reachable.</span></div></div>';
    return;
  }
  const provs=[...new Set(all.map(it=>it.provider))].sort((a,b)=>a.localeCompare(b));
  if(!pmCatProvider||provs.indexOf(pmCatProvider)<0)pmCatProvider=provs[0];
  /* Build the shell only if it is not already present (keeps #pmCatSearch alive). */
  if(!$("#pmCatTabs")){
    host.innerHTML='<div class="pm-cat">'+
      '<div class="pm-cat-search"><input id="pmCatSearch" type="text" placeholder="Filter models in selected provider..." spellcheck="false" autocomplete="off" aria-label="Filter catalog" value="'+esc(pmCatQuery)+'"></div>'+
      '<div class="pm-cat-tabs" id="pmCatTabs" role="tablist" aria-label="Catalog providers"></div>'+
      '<div class="pm-cat-panel" id="pmCatPanel" role="tabpanel" aria-live="polite"></div>'+
      '<div class="pm-cat-foot" id="pmCatFoot"></div>'+
      '</div>';
    const cs=$("#pmCatSearch");
    if(cs)cs.value=pmCatQuery;
  }
  pmRenderPills();
  pmRenderPanel();
  pmRenderFoot();
}
function pmRenderPills(){
  const tabs=$("#pmCatTabs");if(!tabs)return;
  const q=pmCatQuery.trim().toLowerCase();
  const all=pmCatalogList();
  const counts=new Map();
  all.forEach(it=>counts.set(it.provider,(counts.get(it.provider)||0)+1));
  const provs=[...counts.keys()].sort((a,b)=>a.localeCompare(b));
  tabs.innerHTML=provs.map(prov=>{
    const on=prov===pmCatProvider;
    const n=counts.get(prov)||0;
    const matches=q?all.filter(it=>it.provider===prov&&it.full.toLowerCase().includes(q)).length:null;
    const dim=q&&matches===0;
    return '<button type="button" class="pm-cat-pill'+(on?" on":"")+(dim?" dim":"")+'" role="tab" data-pm-provider="'+esc(prov)+'" aria-selected="'+on+'" tabindex="'+(on?"0":"-1")+'">'+
      esc(prov)+' <span class="pcount">'+(q?matches:n)+'</span></button>';
  }).join("");
}
function pmRenderPanel(keepScroll){
  const panel=$("#pmCatPanel");if(!panel)return;
  const prevTop=keepScroll?panel.scrollTop:0;
  const q=pmCatQuery.trim().toLowerCase();
  if(!pmCatProvider){panel.innerHTML='<span class="pm-empty">Select a provider above.</span>';return;}
  const items=pmCatalogList().filter(it=>it.provider===pmCatProvider&&(!q||it.full.toLowerCase().includes(q)));
  if(!items.length){
    panel.innerHTML='<span class="pm-empty">'+(q?'No models match &ldquo;'+esc(pmCatQuery)+'&rdquo; in '+esc(pmCatProvider)+'.':'No models for this provider.')+'</span>';
    return;
  }
  const shown=pmCatShowAll?items:items.slice(0,PM_CAT_CAP);
  let html='<div class="pm-cat-models">';
  shown.forEach(it=>{
    const added=!!pmModels[it.full];
    html+='<span class="cb-opt'+(added?" mb-sel":"")+'" role="button" tabindex="0" data-pm-cat-add="'+esc(it.full)+'" title="'+(added?"Already added":"Add ")+esc(it.full)+'">'+
      (added?'<span class="cb-check">\u2713 </span>':"")+modelHtml(it.full)+'</span>';
  });
  html+='</div>';
  if(!pmCatShowAll&&items.length>shown.length){
    html+='<button type="button" class="cb-more" data-pm-show-all="1">Show all \u2014 '+items.length+' models</button>';
  }else if(pmCatShowAll&&items.length>PM_CAT_CAP){
    html+='<button type="button" class="cb-more" data-pm-show-less="1">Show fewer</button>';
  }
  panel.innerHTML=html;
  panel.scrollTop=prevTop;
}
function pmRenderFoot(){
  const foot=$("#pmCatFoot");if(!foot)return;
  const q=pmCatQuery.trim().toLowerCase();
  const all=pmCatalogList();
  const inProv=all.filter(it=>it.provider===pmCatProvider).length;
  let txt=inProv+' model'+(inProv===1?"":"s")+' in '+esc(pmCatProvider);
  if(q){
    const others=all.filter(it=>it.provider!==pmCatProvider&&it.full.toLowerCase().includes(q)).length;
    txt+=' &middot; &ldquo;'+esc(pmCatQuery)+'&rdquo;';
    if(others)txt+=' &middot; '+others+' match'+(others===1?"":"es")+' in other providers';
  }
  foot.innerHTML='<span class="muted">'+txt+'</span>';
}

function showView(view){
  $("#homeView").hidden=view!=="home";
  $("#listView").hidden=view!=="list";
  $("#editorView").hidden=view!=="editor";
  $("#providerModelsView").hidden=view!=="providerModels";
  $("#discardBar").hidden=true;
}

async function pmAddModel(){
  const inp=$("#pmAddInput");
  const mid=inp?inp.value.trim():"";
  if(!mid){toast("Enter a model ID","error");return;}
  let value=mid;
  if(mid.indexOf("/")<0){
    const q=mid.toLowerCase();
    const matches=pmCatalogList().filter(it=>it.raw.toLowerCase().includes(q)||it.full.toLowerCase().includes(q));
    if(matches.length===1)value=matches[0].full;
  }
  if(!pmCommit(value))return;
  pmQuery="";
  if(inp){inp.value="";}
  pmHideAutocomplete();
  if(inp)inp.focus();
}

async function pmSave(){
  try{
    const res=await api("/api/opencode-provider-models",{provider:pmProvider,models:pmModels});
    if(res.ok){toast("Provider models saved to opencode.jsonc");loadConfig();showView("home");}
    else{toast(res.error||"Failed to save","error");}
  }catch(e){toast("Error: "+e.message,"error");}
}

/* ---- split-view editor state + model catalog ---- */
const MB_CAP_NOQ=30,MB_CAP_Q=200;
const cbData={models:[],variants:[],groups:[]};
let activeAgent=AGENTS[0];
let mbState={q:"",showAll:false,provider:"",flat:[],active:-1};
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
function mbProviderOf(v){
  const i=v.indexOf("/");
  return i<0?"(other)":v.slice(0,i);
}
function ensureEdState(){
  if(edState)return;
  edState={};
  AGENTS.forEach(ag=>{edState[ag]={model:"",variant:"",skills:"",mcps:""};});
  activeAgent=AGENTS[0];
  mbState={q:"",showAll:false,provider:"",flat:[],active:-1};
}
function openEditor(name,tplAgents){
  editing=name||null;dirty=false;confirmState=null;
  $("#edTitle").textContent=name?("Edit preset: "+name):"New preset";
  const nameIn=$("#presetName");
  nameIn.value=name||"";nameIn.readOnly=false;
  const p=(name&&(config&&config.presets||{})[name])||tplAgents||{};
  edState=null;
  ensureEdState();
  AGENTS.forEach(ag=>{
    const a=p[ag]||{};
    edState[ag]={model:a.model||"",variant:a.variant||"",skills:(a.skills||[]).join("\n"),mcps:(a.mcps||[]).join("\n")};
  });
  activeAgent=AGENTS[0];
  mbState={q:"",showAll:false,provider:"",flat:[],active:-1};
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
        '<div class="mb-search"><input id="mbSearch" type="text" placeholder="Filter models in selected provider..." spellcheck="false" autocomplete="off" role="combobox" aria-expanded="true" aria-controls="mbList" aria-autocomplete="list"></div>'+
        '<div class="mb-tabs" id="mbTabs" role="tablist" aria-label="Model providers"></div>'+
        '<div class="mb-list" id="mbList" role="tabpanel" aria-live="polite" aria-label="Models"></div>'+
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
  const provs=cbData.groups.map(g=>g[0]);
  if(!mbState.provider||provs.indexOf(mbState.provider)<0)mbState.provider=mbDefaultProvider();
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
function mbDefaultProvider(){
  const m=edState[activeAgent].model;
  const p=m?mbProviderOf(m):"";
  const provs=cbData.groups.map(g=>g[0]);
  if(p&&provs.indexOf(p)>=0)return p;
  return provs[0]||"";
}
function mbRenderPills(){
  const tabs=$("#mbTabs");if(!tabs)return;
  const q=mbState.q.trim().toLowerCase();
  tabs.innerHTML=cbData.groups.map(([g,items])=>{
    const on=g===mbState.provider;
    const matches=q?items.filter(v=>v.toLowerCase().indexOf(q)>=0).length:null;
    const dim=q&&matches===0;
    return '<button type="button" class="pm-cat-pill'+(on?" on":"")+(dim?" dim":"")+'" role="tab" data-mb-provider="'+esc(g)+'" aria-selected="'+on+'" tabindex="'+(on?"0":"-1")+'">'+
      esc(g)+' <span class="pcount">'+(q?matches:items.length)+'</span></button>';
  }).join("");
}
function renderMb(keepScroll){
  const listEl=$("#mbList");if(!listEl)return;
  const prevTop=keepScroll?listEl.scrollTop:0;
  mbRenderPills();
  const q=mbState.q.trim().toLowerCase();
  const group=cbData.groups.filter(g=>g[0]===mbState.provider)[0];
  const items=group?group[1]:[];
  const matches=q?items.filter(v=>v.toLowerCase().indexOf(q)>=0):items.slice();
  const cap=mbState.showAll?Infinity:(q?MB_CAP_Q:MB_CAP_NOQ);
  const shown=matches.slice(0,cap);
  let html="",flat=[];
  shown.forEach(v=>{
    const sel=edState[activeAgent].model===v;
    html+='<div class="cb-opt'+(sel?" mb-sel":"")+'" id="mb-opt-'+flat.length+'" role="option" aria-selected="'+sel+'" data-val="'+esc(v)+'" title="'+esc(v)+'">'+
      (sel?'<span class="cb-check">\u2713 </span>':"")+modelHtml(v)+'</div>';
    flat.push(v);
  });
  const hidden=matches.length-flat.length;
  const baseCap=q?MB_CAP_Q:MB_CAP_NOQ;
  let out='<div class="mb-chips" role="listbox" aria-label="Models">'+html+'</div>';
  if(!flat.length){
    if(q){
      let others=0;
      cbData.groups.forEach(([g,it])=>{if(g!==mbState.provider)others+=it.filter(v=>v.toLowerCase().indexOf(q)>=0).length;});
      out='<div class="cb-empty">No matches for \u201c'+esc(mbState.q)+'\u201d in '+esc(mbState.provider)+'.'+
        (others?' '+others+' match'+(others===1?"":"es")+' in other providers.':'')+'</div>';
    }else{
      out='<div class="cb-empty">No models for this provider.</div>';
    }
  }else if(hidden>0&&!mbState.showAll){
    out+='<button type="button" class="cb-more" data-cbmore="1">Show all \u2014 '+hidden+' more model'+(hidden===1?"":"s")+'</button>';
  }else if(mbState.showAll&&matches.length>baseCap){
    out+='<button type="button" class="cb-more" data-cbmore-less="1">Show fewer</button>';
  }
  mbState.flat=flat;
  if(mbState.active>=flat.length)mbState.active=flat.length-1;
  listEl.innerHTML=out;
  listEl.scrollTop=prevTop;
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
  mbState.provider="";
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
  if(editing!==name&&(config&&config.presets||{})[name]){toast("Preset '"+name+"' already exists — use Edit on its card, or pick another name","error");return;}
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
    await api("/api/preset",{name:name,oldName:editing||null,agents:agents});
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
    else if(act==="home")showView("home");
    else if(act==="go-presets")showView("list");
    else if(act==="save")await saveEditor();
  }catch(err){toast(err.message,"error");}
});
window.addEventListener("beforeunload",e=>{
  if(dirty){e.preventDefault();e.returnValue="";}
});
document.addEventListener("keydown",async e=>{
  const inp=e.target.closest?e.target.closest(".dupname"):null;
  if(!inp)return;
  if(e.key==="Enter"){
    e.preventDefault();
    const bar=inp.closest(".confirmbar");
    const btn=bar&&bar.querySelector('[data-action="dup-go"]');
    if(btn)btn.click();
  }else if(e.key==="Escape"){
    e.preventDefault();
    const bar=inp.closest(".confirmbar");
    const btn=bar&&bar.querySelector('[data-action="dup-cancel"]');
    if(btn)btn.click();
  }
});
$("#agentsGrid").addEventListener("click",e=>{
  const row=e.target.closest(".agent-row");
  if(row){setActiveAgent(row.dataset.agent);return;}
  if(e.target.closest("[data-cbmore]")){mbState.showAll=true;renderMb(true);return;}
  if(e.target.closest("[data-cbmore-less]")){mbState.showAll=false;renderMb(true);return;}
  const pill=e.target.closest("[data-mb-provider]");
  if(pill){
    const g=pill.dataset.mbProvider;
    if(g===mbState.provider)return;
    mbState.provider=g;
    mbState.showAll=false;
    mbState.flat=[];
    mbState.active=-1;
    renderMb();
    const on=$("#mbTabs .pm-cat-pill.on");
    if(on){try{on.focus({preventScroll:true});}catch(err){on.focus();}}
    return;
  }
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
  }else if(t.closest&&t.closest("#mbTabs")&&(e.key==="ArrowRight"||e.key==="ArrowLeft")){
    const pills=[...document.querySelectorAll("#mbTabs .pm-cat-pill")];
    if(pills.length<2)return;
    e.preventDefault();
    const i=pills.indexOf(t.closest(".pm-cat-pill"));
    if(i<0)return;
    const ni=(i+(e.key==="ArrowRight"?1:-1)+pills.length)%pills.length;
    pills[ni].click();
  }
});
["input","change"].forEach(ev=>document.addEventListener(ev,e=>{
  if(e.target.id==="mbSearch")return;
  if(e.target.closest("#editorView"))markDirty();
  if(e.target.closest("#providerModelsView"))markDirty();
},true));

/* ---- provider models view event handlers ---- */
document.addEventListener("click",async e=>{
  const b=e.target.closest("[data-action]");
  if(!b)return;
  const act=b.dataset.action;
  if(act==="pm-open"){
    if(!pmHasOpencodeJsonc){toast("opencode.jsonc not found — provider models unavailable","error");return;}
    loadProviderModelsView();return;
  }
  if(act==="pm-back"){showView("home");return;}
  if(act==="pm-add"){await pmAddModel();return;}
  if(act==="pm-toggle-cat"){
    pmCatalogOpen=!pmCatalogOpen;
    if(pmCatalogOpen)pmCatQuery="";
    pmRenderCatalog();
    return;
  }
  if(act==="pm-save"){await pmSave();return;}
});
/* Chips, catalog provider pills, catalog items. */
document.addEventListener("click",e=>{
  const chip=e.target.closest("[data-pm-remove]");
  if(chip){
    const mid=chip.dataset.pmRemove;
    delete pmModels[mid];
    renderPMModels();
    pmMarkCatalogItem(mid,false);
    const inp=$("#pmAddInput");
    if(inp)inp.focus();
    return;
  }
  const pill=e.target.closest("[data-pm-provider]");
  if(pill){
    const prov=pill.dataset.pmProvider;
    if(prov===pmCatProvider)return;
    pmCatProvider=prov;
    pmCatShowAll=false;
    pmRenderPills();
    pmRenderPanel();
    pmRenderFoot();
    const on=$("#pmCatTabs .pm-cat-pill.on");
    if(on){try{on.focus({preventScroll:true});}catch(err){on.focus();}}
    return;
  }
  if(e.target.closest("[data-pm-show-all]")){pmCatShowAll=true;pmRenderPanel(true);return;}
  if(e.target.closest("[data-pm-show-less]")){pmCatShowAll=false;pmRenderPanel(true);return;}
  const acMore=e.target.closest("[data-pm-ac-more]");
  if(acMore){
    const all=pmFilteredCatalog();
    pmSuggestions=all.map(it=>it.full);
    pmActive=-1;
    let html='<div class="pm-ac-head">All matches</div>';
    all.forEach((it,i)=>{
      const added=!!pmModels[it.full];
      html+='<div class="cb-opt'+(added?" mb-sel":"")+'" id="pm-ac-opt-'+i+'" role="option" aria-selected="false" data-pm-sug="'+esc(it.full)+'">'+(added?'<span class="cb-check">\u2713 </span>':"")+modelHtml(it.full)+'</div>';
    });
    const ac=$("#pmAc");
    if(ac){ac.innerHTML=html;ac.hidden=false;}
    return;
  }
  const sug=e.target.closest("[data-pm-sug]");
  if(sug){
    const full=sug.dataset.pmSug;
    const inp=$("#pmAddInput");
    if(!pmCommit(full))return;
    pmQuery="";
    if(inp){inp.value="";}
    pmHideAutocomplete();
    if(inp)inp.focus();
    return;
  }
  const catItem=e.target.closest("[data-pm-cat-add]");
  if(catItem){
    const mid=catItem.dataset.pmCatAdd;
    if(pmModels[mid])return;
    pmCommit(mid);
    return;
  }
});
/* Live autocomplete + catalog filter on the provider view inputs. */
document.addEventListener("input",e=>{
  const t=e.target;
  if(t.id==="pmAddInput"){
    pmQuery=t.value;
    pmActive=-1;
    pmShowAutocomplete();
  }else if(t.id==="pmCatSearch"){
    pmCatQuery=t.value;
    pmCatShowAll=false;
    pmRenderPills();
    pmRenderPanel();
    pmRenderFoot();
  }
});
document.addEventListener("keydown",async e=>{
  const t=e.target;
  if(t.id==="pmAddInput"){
    const ac=$("#pmAc");
    const open=ac&&!ac.hidden;
    if(e.key==="ArrowDown"||e.key==="ArrowUp"){
      if(!open||!pmSuggestions.length)return;
      e.preventDefault();
      let i=pmActive;
      if(i<0)i=e.key==="ArrowDown"?0:pmSuggestions.length-1;
      else i=(i+(e.key==="ArrowDown"?1:-1)+pmSuggestions.length)%pmSuggestions.length;
      pmActive=i;pmApplyAcActive();
    }else if(e.key==="Enter"){
      e.preventDefault();
      if(open&&pmActive>=0&&pmSuggestions[pmActive]!==undefined){
        const full=pmSuggestions[pmActive];
        if(!pmCommit(full))return;
        pmQuery="";t.value="";
        pmHideAutocomplete();
      }else{
        await pmAddModel();
      }
    }else if(e.key==="Escape"){
      if(open){e.preventDefault();pmHideAutocomplete();}
    }
  }else if(t.id==="pmCatSearch"&&e.key==="Escape"){
    t.value="";pmCatQuery="";pmCatShowAll=false;pmRenderPills();pmRenderPanel();pmRenderFoot();t.focus();
  }else if(t.closest&&t.closest("#pmCatTabs")&&(e.key==="ArrowRight"||e.key==="ArrowLeft")){
    const pills=[...document.querySelectorAll("#pmCatTabs .pm-cat-pill")];
    if(pills.length<2)return;
    e.preventDefault();
    const i=pills.indexOf(t.closest(".pm-cat-pill"));
    if(i<0)return;
    const ni=(i+(e.key==="ArrowRight"?1:-1)+pills.length)%pills.length;
    pills[ni].click();
  }else if((e.key==="Enter"||e.key===" ")&&t.closest&&t.closest("[data-pm-cat-add]")){
    e.preventDefault();
    t.click();
  }
});
/* Click outside closes the autocomplete. */
document.addEventListener("mousedown",e=>{
  const ac=$("#pmAc");
  if(!ac||ac.hidden)return;
  if(e.target.closest(".pm-add-wrap"))return;
  pmHideAutocomplete();
});


async function loadConfig(){
  try{
    const res=await fetch("/api/config");
    const data=await res.json();
    if(!data.ok){config=null;showError(data.error||"Failed to load config");}
    else{config=data.config;configPath=data.configPath;backups=(data.backups||[]).length;providerModels=data.providers||{};pmHasOpencodeJsonc=data.hasOpencodeJsonc||false;clearError();}
  }catch(e){config=null;showError("Cannot reach server: "+e.message);}
  renderHeader();renderList();
  const names=Object.keys((config&&config.presets)||{});
  const presetMeta=$("#homePresetMeta");
  if(presetMeta)presetMeta.textContent=names.length+" preset"+(names.length===1?"":"s")+" \u00b7 model, variant, skills, MCPs";
  const provOpt=$("#homeProviderOption");
  if(provOpt){
    provOpt.classList.toggle("disabled",!pmHasOpencodeJsonc);
    provOpt.disabled=!pmHasOpencodeJsonc;
    const provMeta=$("#homeProviderMeta");
    if(provMeta)provMeta.textContent=pmHasOpencodeJsonc?"model list \u00b7 add or remove models":"opencode.jsonc not found";
    if(!pmHasOpencodeJsonc){
      const hint=provOpt.querySelector(".opt-hint");
      if(!hint){const h=document.createElement("span");h.className="opt-hint";h.textContent="Unavailable: no opencode.jsonc next to your config.";provOpt.appendChild(h);}
    }
  }
}
loadConfig();
showView("home");
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
                    "providers": get_provider_models_fast(os.path.dirname(app.config_path)),
                    "opencodeJsoncPath": os.path.join(os.path.dirname(app.config_path), "opencode.jsonc"),
                    "hasOpencodeJsonc": os.path.exists(os.path.join(os.path.dirname(app.config_path), "opencode.jsonc")),
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
        if path == "/api/opencode-config":
            app = self.server.app
            with app.lock:
                cfg_dir = os.path.dirname(app.config_path)
                oc_path = os.path.join(cfg_dir, "opencode.jsonc")
                data, err = load_opencode_config(oc_path)
                if err:
                    self._send(404, {"ok": False, "error": err})
                    return
                prov_models = get_opencode_provider_models(oc_path)
                self._send(200, {
                    "ok": True,
                    "config": data,
                    "providerModels": prov_models,
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
            agents = sanitize_agents(body.get("agents"))
            old = str(body.get("oldName") or "").strip()
            if old and valid_name(old, field="old name") != name:
                if old not in presets:
                    raise ApiError("Preset '%s' does not exist" % old, 404)
                if name in presets:
                    raise ApiError("Preset '%s' already exists" % name)
                del presets[old]
                if cfg.get("preset") == old:
                    cfg["preset"] = name
                msg = "Preset '%s' renamed to '%s'" % (old, name)
            else:
                msg = "Preset '%s' saved" % name
            presets[name] = agents

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

        elif path == "/api/opencode-provider-models":
            provider = valid_name(body.get("provider"), field="provider")
            models = body.get("models")
            if not isinstance(models, dict):
                raise ApiError("'models' must be an object", 400)
            # Validate model IDs
            for mid in models:
                if not isinstance(mid, str) or not mid.strip():
                    raise ApiError("Invalid model ID", 400)
            set_opencode_provider_models(app.config_path, provider, models)
            msg = "Provider '%s' models updated" % provider

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
    warm_provider_models(os.path.dirname(server.app.config_path))
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
