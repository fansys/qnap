"""Load workflow config with env / CLI overrides."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .logutil import log
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
    if raw is None or raw.strip() == "":
        return None
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _unset_preset(value: str | None) -> str | None:
    """Treat none/empty as 'no preset override'."""
    if value is None:
        return None
    text = value.strip()
    if text == "" or text.lower() == "none":
        return None
    return text


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

    Priority:
    1. explicit non-empty ``prefix`` argument / ``--accelerate-prefix`` / ``--base-url``
    2. ``FEED_ACCELERATE_PREFIX`` environment variable
    3. explicit ``preset`` argument (not ``none``) / ``FEED_ACCELERATE_PRESET``
    4. ``config.json`` accelerate.preset / accelerate.prefix

    ``preset=none`` (CLI or Actions default) means "do not override" — fall through
    to env / config.json. It does **not** force an empty prefix.
    """
    cfg = (config or load_config())["accelerate"]
    presets: dict[str, str] = dict(cfg.get("presets") or DEFAULT_ACCELERATE_PRESETS)

    cli_prefix = _normalize_prefix(prefix) if prefix is not None and prefix.strip() else None
    env_prefix_raw = os.environ.get("FEED_ACCELERATE_PREFIX")
    env_prefix = (
        _normalize_prefix(env_prefix_raw)
        if env_prefix_raw is not None and env_prefix_raw.strip() != ""
        else None
    )
    cli_preset = _unset_preset(preset)
    env_preset = _unset_preset(os.environ.get("FEED_ACCELERATE_PRESET"))
    env_enabled = _env_bool("FEED_ACCELERATE_ENABLED")

    use_enabled = bool(cfg.get("enabled"))
    if env_enabled is not None:
        use_enabled = env_enabled
    if enabled is not None:
        use_enabled = enabled

    # Any concrete override means acceleration should apply even if config.enabled=false.
    has_override = bool(cli_prefix or env_prefix or cli_preset or env_preset)
    if not use_enabled and not has_override:
        log("config", "accelerate disabled (config/env) and no override")
        return ""

    if cli_prefix:
        log("config", f"accelerate source=cli-prefix value={cli_prefix}")
        return cli_prefix
    if env_prefix:
        log("config", f"accelerate source=FEED_ACCELERATE_PREFIX value={env_prefix}")
        return env_prefix

    chosen = cli_preset or env_preset or str(cfg.get("preset") or "none")
    chosen = chosen.strip()
    source = (
        "cli-preset"
        if cli_preset
        else "FEED_ACCELERATE_PRESET"
        if env_preset
        else "config.json"
    )
    if chosen in presets:
        value = _normalize_prefix(str(presets[chosen]))
    else:
        value = _normalize_prefix(str(cfg.get("prefix") or ""))
    log("config", f"accelerate source={source} preset={chosen} value={value or '-'}")
    return value


def configured_providers(config: dict[str, Any] | None = None) -> list[str]:
    cfg = config or load_config()
    providers = cfg.get("feed", {}).get("providers") or ["emby", "tailscale"]
    return [str(name) for name in providers]
