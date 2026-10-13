"""Discover Emby x86_64 QNAP packages from MediaBrowser/Emby.Releases."""

from __future__ import annotations

import dataclasses
import json
import os
import re
from typing import Any
import urllib.error
import urllib.request

from .http_util import USER_AGENT
from .tls import verified_context

RELEASES_API = "https://api.github.com/repos/MediaBrowser/Emby.Releases/releases"
ASSET_PATTERN = re.compile(
    r"^emby-server-qnap_(?P<version>[0-9][0-9A-Za-z.-]*)_x86_64\.qpkg$",
    re.IGNORECASE,
)


class GithubError(RuntimeError):
    """Raised when GitHub discovery or download fails."""


@dataclasses.dataclass(frozen=True)
class ReleaseInfo:
    github_release_id: int
    asset_id: int
    version: str
    tag_name: str
    release_name: str
    changelog: str
    html_url: str
    published_at: str
    prerelease: bool
    asset_name: str
    asset_url: str
    asset_size: int
    asset_digest: str


def version_key(value: str) -> tuple[tuple[int, ...], int, str]:
    """Sort key: numeric core ascending; release (no suffix) sorts after prerelease.

    Examples: 4.10.0.0 > 4.10.0.0-beta; 4.11.0.0-beta > 4.10.0.0.
    """
    text = value.lstrip("vV")
    match = re.match(r"^(\d+(?:\.\d+)*)(?:[-.]?(.*))?$", text)
    if not match:
        return ((0,), 1, text.lower())
    numbers = tuple(int(part) for part in match.group(1).split("."))
    suffix = (match.group(2) or "").lower()
    # 1 = final release, 0 = prerelease/suffix → final sorts higher when nums equal.
    return (numbers, 1 if not suffix else 0, suffix)


def parse_releases(payload: list[dict[str, Any]]) -> list[ReleaseInfo]:
    releases: list[ReleaseInfo] = []
    for release in payload:
        if release.get("draft"):
            continue
        for asset in release.get("assets") or []:
            match = ASSET_PATTERN.fullmatch(str(asset.get("name") or ""))
            if not match:
                continue
            releases.append(
                ReleaseInfo(
                    github_release_id=int(release["id"]),
                    asset_id=int(asset["id"]),
                    version=match.group("version"),
                    tag_name=str(release.get("tag_name") or match.group("version")),
                    release_name=str(release.get("name") or match.group("version")),
                    changelog=str(release.get("body") or ""),
                    html_url=str(release.get("html_url") or ""),
                    published_at=str(
                        release.get("published_at") or release.get("created_at") or ""
                    ),
                    prerelease=bool(release.get("prerelease")),
                    asset_name=str(asset["name"]),
                    asset_url=str(asset["browser_download_url"]),
                    asset_size=int(asset.get("size") or 0),
                    asset_digest=str(asset.get("digest") or ""),
                )
            )
    return sorted(releases, key=lambda item: version_key(item.version), reverse=True)


def fetch_releases(api_url: str = RELEASES_API, pages: int = 3) -> list[ReleaseInfo]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": USER_AGENT,
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    payload: list[dict[str, Any]] = []
    for page in range(1, pages + 1):
        separator = "&" if "?" in api_url else "?"
        request = urllib.request.Request(
            f"{api_url}{separator}per_page=100&page={page}", headers=headers
        )
        try:
            with urllib.request.urlopen(
                request, timeout=45, context=verified_context()
            ) as response:
                batch = json.load(response)
        except (urllib.error.URLError, ValueError) as error:
            raise GithubError(f"GitHub release request failed: {error}") from error
        if not isinstance(batch, list):
            raise GithubError("GitHub releases response is not a list")
        payload.extend(batch)
        if len(batch) < 100:
            break
    return parse_releases(payload)


def download_asset(url: str, destination: Any) -> None:
    from .http_util import HttpError, download_file

    try:
        download_file(url, destination, auth=True)
    except HttpError as error:
        raise GithubError(str(error)) from error
