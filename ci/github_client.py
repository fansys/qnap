"""GitHub REST helpers for this repository's releases."""

from __future__ import annotations

import json
import os
from typing import Any
import urllib.error
import urllib.request

from .http_util import USER_AGENT
from .tls import verified_context
from .upstream import GithubError


def github_headers() -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": USER_AGENT,
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def api_get(url: str) -> Any:
    request = urllib.request.Request(url, headers=github_headers())
    try:
        with urllib.request.urlopen(request, timeout=45, context=verified_context()) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        raise GithubError(f"GitHub API GET failed ({error.code}): {url}") from error
    except (urllib.error.URLError, ValueError) as error:
        raise GithubError(f"GitHub API GET failed: {error}") from error


def release_tag_exists(repo: str, tag: str) -> bool:
    payload = api_get(f"https://api.github.com/repos/{repo}/releases/tags/{tag}")
    return payload is not None


def default_repo() -> str:
    return os.environ.get("GITHUB_REPOSITORY") or os.environ.get("TARGET_REPO") or ""
