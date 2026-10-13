"""Shared sync result helpers for Actions job outputs."""

from __future__ import annotations

from dataclasses import dataclass
import os

from .logutil import log


@dataclass(frozen=True)
class SyncResult:
    signatures: dict[str, str]
    released: bool


def emit_released(released: bool) -> None:
    """Print and optionally write GitHub Actions job output ``released``."""
    value = "true" if released else "false"
    log("status", f"released={value}")
    print(f"released={value}", flush=True)
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        return
    with open(output_path, "a", encoding="utf-8") as handle:
        handle.write(f"released={value}\n")
    log("status", f"wrote GITHUB_OUTPUT released={value}")
