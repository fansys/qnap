"""Load workflow config with env / CLI overrides."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .logutil import log
from .paths import ROOT

CONFIG_PATH = ROOT / "config.json"

# Named mirror prefixes. ``env`` / ``none`` are special and handled in code.
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


def _env_nonempty(name: str) -> str | None:
    raw = os.environ.get(name)
    if raw is None:
        return None
    text = raw.strip()
    return text if text else None


def load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    data = _read_json(path)
    accelerate = dict(data.get("accelerate") or {})
    presets = dict(DEFAULT_ACCELERATE_PRESETS)
    presets.update({str(k): str(v) for k, v in dict(accelerate.get("presets") or {}).items()})
    # Keep special names reserved even if config overrides them accidentally.
    presets["none"] = ""
    accelerate["presets"] = presets
    accelerate.setdefault("enabled", True)
    accelerate.setdefault("mode", "prefix")
    accelerate.setdefault("preset", "env")
    accelerate.setdefault("prefix", "")
    feed = dict(data.get("feed") or {})
    feed.setdefault("providers", ["emby", "tailscale"])
    return {"accelerate": accelerate, "feed": feed}


def _resolve_env_accelerate(presets: dict[str, str]) -> tuple[str, str]:
    """Return (prefix, source) from environment, or ("", "none") when unset."""
    env_prefix = _env_nonempty("FEED_ACCELERATE_PREFIX")
    if env_prefix:
        return _normalize_prefix(env_prefix), "FEED_ACCELERATE_PREFIX"
    env_preset = _env_nonempty("FEED_ACCELERATE_PRESET")
    if env_preset:
        if env_preset in {"env", "none"}:
            return "", f"FEED_ACCELERATE_PRESET={env_preset}"
        if env_preset in presets:
            return _normalize_prefix(str(presets[env_preset])), f"FEED_ACCELERATE_PRESET={env_preset}"
        log(
            "config",
            f"unknown FEED_ACCELERATE_PRESET={env_preset}, fallback none",
            level="warn",
        )
        return "", "none"
    return "", "none"


def resolve_accelerate_prefix(
    *,
    config: dict[str, Any] | None = None,
    preset: str | None = None,
    prefix: str | None = None,
    enabled: bool | None = None,
) -> str:
    """Resolve public feed URL accelerator prefix.

    Preset meanings:
    - ``env`` (default): read ``FEED_ACCELERATE_PREFIX`` / ``FEED_ACCELERATE_PRESET``;
      if neither is set, fallback to ``none`` (empty prefix).
    - ``none``: empty prefix (no acceleration).
    - other names: look up ``config.json`` / built-in presets.

    Priority for concrete values:
    1. explicit non-empty ``prefix`` / ``--accelerate-prefix`` / ``--base-url``
    2. selected preset behavior (``env`` / ``none`` / named mirror)
    """
    cfg = (config or load_config())["accelerate"]
    presets: dict[str, str] = dict(cfg.get("presets") or DEFAULT_ACCELERATE_PRESETS)
    presets["none"] = ""

    cli_prefix = _normalize_prefix(prefix) if prefix is not None and prefix.strip() else None
    chosen = (preset if preset is not None else str(cfg.get("preset") or "env")).strip() or "env"
    chosen_lower = chosen.lower()

    env_enabled = _env_bool("FEED_ACCELERATE_ENABLED")
    use_enabled = bool(cfg.get("enabled"))
    if env_enabled is not None:
        use_enabled = env_enabled
    if enabled is not None:
        use_enabled = enabled

    if cli_prefix:
        log("config", f"accelerate source=cli-prefix value={cli_prefix}")
        return cli_prefix

    # Explicit none: always empty, regardless of env/config mirrors.
    if chosen_lower == "none":
        log("config", "accelerate source=preset:none value=-")
        return ""

    # Default env: environment first, otherwise fallback to none.
    if chosen_lower == "env":
        if not use_enabled:
            # Still allow env override when enabled=false? User asked env preset to read env.
            # If FEED_ACCELERATE_ENABLED=false, honor that and return empty.
            if env_enabled is False:
                log("config", "accelerate disabled by FEED_ACCELERATE_ENABLED=false")
                return ""
        value, source = _resolve_env_accelerate(presets)
        log("config", f"accelerate source=preset:env/{source} value={value or '-'}")
        return value

    # Named mirror preset.
    if not use_enabled and env_enabled is not True:
        # Named preset from CLI/Actions still counts as an explicit request.
        if preset is None:
            log("config", "accelerate disabled (config/env)")
            return ""

    if chosen in presets:
        value = _normalize_prefix(str(presets[chosen]))
        log("config", f"accelerate source=preset:{chosen} value={value or '-'}")
        return value

    value = _normalize_prefix(str(cfg.get("prefix") or ""))
    log("config", f"accelerate source=config.prefix value={value or '-'}")
    return value


def configured_providers(config: dict[str, Any] | None = None) -> list[str]:
    cfg = config or load_config()
    providers = cfg.get("feed", {}).get("providers") or ["emby", "tailscale"]
    return [str(name) for name in providers]
