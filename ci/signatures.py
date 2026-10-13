"""Persist and resolve repository_signature values for repo feeds."""

from __future__ import annotations

import json
from pathlib import Path

from .logutil import log, log_kv
from .paths import PACKAGE_DIR, SIGNATURES_PATH, ensure_out_dirs
from .qpkg_sig import extract_repository_signature
from .upstream import download_asset


def load_signature_cache(path: Path = SIGNATURES_PATH) -> dict[str, str]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return {str(key): str(value) for key, value in payload.items()}


def save_signature_cache(
    cache: dict[str, str],
    path: Path = SIGNATURES_PATH,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(cache, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    log_kv("signatures", "saved", {"path": path, "keys": len(cache)})


def merge_signature_caches(*caches: dict[str, str]) -> dict[str, str]:
    merged: dict[str, str] = {}
    for cache in caches:
        merged.update(cache)
    return merged


def resolve_signature(
    version: str,
    package_name: str,
    cache: dict[str, str],
    openssl: str,
    *,
    download_url: str | None = None,
    cache_key: str | None = None,
) -> str:
    key = cache_key or version
    if key in cache:
        log("signatures", f"cache hit key={key}")
        return cache[key]
    ensure_out_dirs()
    local = PACKAGE_DIR / package_name
    if not local.is_file() and download_url:
        log("signatures", f"download signature source url={download_url}")
        download_asset(download_url, local)
    if local.is_file():
        log("signatures", f"extract signature from {local}")
        signature = extract_repository_signature(local, openssl)
        cache[key] = signature
        log_kv("signatures", "extracted", {"key": key, "len": len(signature)})
        return signature
    raise RuntimeError(
        f"repository signature for {key} is missing; "
        "rebuild the package or update signatures.json"
    )
