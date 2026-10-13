"""Load workflow config with env / CLI overrides."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .paths import ROOT

CONFIG_PATH = ROOT / "config.json"

DEFAULT_ACCELERATE_PRESETS: dict[str, str] = {
    "none": "",
    "ghproxy": "https://ghproxy.net/",
    "ghproxy_mirror": "https://mirror.ghproxy.com/",
    "ghfast": "https://ghfast.top/",
    "gh_ddlc": "https://gh.ddlc.top/",
}


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _normalize_prefix(value: str) -> str:
    text = value.strip()
    if not text:
        return ""
    return text if text.endswith("/") else text + "/"


def _env_bool(name: str) -> bool | None:
    raw = os.environ.get(name)
    if raw is None:
        return None
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    data = _read_json(path)
    accelerate = dict(data.get("accelerate") or {})
    presets = dict(DEFAULT_ACCELERATE_PRESETS)
    presets.update({str(k): str(v) for k, v in dict(accelerate.get("presets") or {}).items()})
    accelerate["presets"] = presets
    accelerate.setdefault("enabled", False)
    accelerate.setdefault("mode", "prefix")
    accelerate.setdefault("preset", "none")
    accelerate.setdefault("prefix", "")
    feed = dict(data.get("feed") or {})
    feed.setdefault("providers", ["emby", "tailscale"])
    return {"accelerate": accelerate, "feed": feed}


def resolve_accelerate_prefix(
    *,
    config: dict[str, Any] | None = None,
    preset: str | None = None,
    prefix: str | None = None,
    enabled: bool | None = None,
) -> str:
    """Resolve public feed URL accelerator prefix.

    Priority for the prefix value:
    1. explicit ``prefix`` argument
    2. ``FEED_ACCELERATE_PREFIX``
    3. ``preset`` / ``FEED_ACCELERATE_PRESET`` / config preset / config prefix

    Acceleration applies when enabled in config/env, or when the caller explicitly
    passes preset/prefix / sets the env overrides.
    """
    cfg = (config or load_config())["accelerate"]
    presets: dict[str, str] = dict(cfg.get("presets") or DEFAULT_ACCELERATE_PRESETS)

    env_prefix = os.environ.get("FEED_ACCELERATE_PREFIX")
    env_preset = os.environ.get("FEED_ACCELERATE_PRESET")
    env_enabled = _env_bool("FEED_ACCELERATE_ENABLED")

    explicit = (
        (prefix is not None and prefix != "")
        or (preset is not None and preset != "")
        or bool(env_prefix)
        or bool(env_preset)
    )

    use_enabled = bool(cfg.get("enabled"))
    if env_enabled is not None:
        use_enabled = env_enabled
    if enabled is not None:
        use_enabled = enabled
    if not use_enabled and not explicit:
        return ""

    if prefix is not None and prefix != "":
        return _normalize_prefix(prefix)
    if env_prefix:
        return _normalize_prefix(env_prefix)

    chosen = (preset or env_preset or str(cfg.get("preset") or "none")).strip()
    if chosen in presets:
        return _normalize_prefix(str(presets[chosen]))
    return _normalize_prefix(str(cfg.get("prefix") or ""))


def configured_providers(config: dict[str, Any] | None = None) -> list[str]:
    cfg = config or load_config()
    providers = cfg.get("feed", {}).get("providers") or ["emby", "tailscale"]
    return [str(name) for name in providers]
