"""Structured status logging for workflow scripts."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys
import time
from typing import Any


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S")


def log(scope: str, message: str, *, level: str = "info") -> None:
    prefix = f"[{_ts()}][{scope}]"
    if level == "warn":
        prefix = f"[{_ts()}][{scope}][warn]"
    elif level == "error":
        prefix = f"[{_ts()}][{scope}][error]"
    stream = sys.stderr if level == "error" else sys.stdout
    print(f"{prefix} {message}", file=stream, flush=True)


def log_kv(scope: str, title: str, fields: dict[str, Any]) -> None:
    parts = [f"{key}={_fmt(value)}" for key, value in fields.items()]
    log(scope, f"{title}: " + " ".join(parts))


def _fmt(value: Any) -> str:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "-"
    text = str(value)
    if " " in text or text == "":
        return repr(text)
    return text


class StepTimer:
    def __init__(self, scope: str, title: str) -> None:
        self.scope = scope
        self.title = title
        self.started = 0.0

    def __enter__(self) -> "StepTimer":
        self.started = time.perf_counter()
        log(self.scope, f"start {self.title}")
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        elapsed = time.perf_counter() - self.started
        if exc_type is None:
            log(self.scope, f"done {self.title} ({elapsed:.1f}s)")
        else:
            log(
                self.scope,
                f"failed {self.title} ({elapsed:.1f}s): {exc}",
                level="error",
            )
        return None


def file_size(path: Path) -> str:
    if not path.is_file():
        return "-"
    size = path.stat().st_size
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):.1f}MiB"
    if size >= 1024:
        return f"{size / 1024:.1f}KiB"
    return f"{size}B"
