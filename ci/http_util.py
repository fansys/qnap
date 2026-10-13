"""Shared HTTP helpers for downloads and probes."""

from __future__ import annotations

import os
from pathlib import Path
import urllib.error
import urllib.request

from .tls import verified_context

USER_AGENT = "emby-qnap-repository-workflow/1.0"


class HttpError(RuntimeError):
    """Raised when an HTTP download or probe fails."""


def public_headers(*, auth: bool = False) -> dict[str, str]:
    headers = {"User-Agent": USER_AGENT}
    if auth:
        token = os.environ.get("GITHUB_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
    return headers


def head_ok(url: str, *, timeout: int = 30, auth: bool = False) -> bool:
    request = urllib.request.Request(
        url, method="HEAD", headers=public_headers(auth=auth)
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=verified_context()):
            return True
    except urllib.error.HTTPError as error:
        return error.code == 200
    except (urllib.error.URLError, OSError):
        return False


def read_text(url: str, *, timeout: int = 45, auth: bool = False) -> str:
    request = urllib.request.Request(url, headers=public_headers(auth=auth))
    try:
        with urllib.request.urlopen(
            request, timeout=timeout, context=verified_context()
        ) as response:
            return response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as error:
        raise HttpError(f"GET failed ({error.code}): {url}") from error
    except (urllib.error.URLError, OSError) as error:
        raise HttpError(f"GET failed: {error}") from error


def download_file(url: str, destination: Path, *, timeout: int = 120, auth: bool = False) -> None:
    request = urllib.request.Request(url, headers=public_headers(auth=auth))
    temporary = destination.with_suffix(destination.suffix + ".part")
    try:
        with urllib.request.urlopen(
            request, timeout=timeout, context=verified_context()
        ) as response, temporary.open("wb") as output:
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
        temporary.replace(destination)
    except (urllib.error.URLError, OSError) as error:
        temporary.unlink(missing_ok=True)
        raise HttpError(f"download failed: {error}") from error
