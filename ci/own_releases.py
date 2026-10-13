"""Single-pass listing of this repository's Emby / Tailscale releases."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from .github_client import api_get
from .tags import emby_release_tag, parse_tailscale_tag
from .upstream import GithubError, version_key

EMBY_PATCHED_ASSET = re.compile(
    r"^emby-server-qnap_(?P<version>[0-9][0-9A-Za-z.-]*)_x86_64_patched\.qpkg$",
    re.IGNORECASE,
)
TAILSCALE_ASSET = re.compile(
    r"^Tailscale_(?P<version>\d+\.\d+\.\d+(?:-\d+)?)_(?P<arch>.+)\.qpkg$",
    re.IGNORECASE,
)


@dataclass
class OwnCatalog:
    emby: list[dict[str, Any]]
    tailscale: dict[str, Any] | None


def fetch_own_catalog(repo: str, *, pages: int = 3) -> OwnCatalog:
    """Paginate own releases once; classify Emby and Tailscale assets."""
    emby: list[dict[str, Any]] = []
    best_tailscale: dict[str, Any] | None = None

    for page in range(1, pages + 1):
        batch = api_get(
            f"https://api.github.com/repos/{repo}/releases?per_page=100&page={page}"
        )
        if batch is None:
            break
        if not isinstance(batch, list):
            raise GithubError("own releases response is not a list")

        for release in batch:
            if release.get("draft"):
                continue
            tag = str(release.get("tag_name") or "")
            assets = release.get("assets") or []
            published = str(
                release.get("published_at") or release.get("created_at") or ""
            )
            html_url = str(release.get("html_url") or "")
            prerelease = bool(release.get("prerelease"))

            tailscale_version = parse_tailscale_tag(tag)
            if tailscale_version is not None:
                packages: dict[str, str] = {}
                download_urls: dict[str, str] = {}
                for asset in assets:
                    name = str(asset.get("name") or "")
                    match = TAILSCALE_ASSET.fullmatch(name)
                    if not match:
                        continue
                    arch = match.group("arch")
                    packages[arch] = name
                    download_urls[arch] = str(asset.get("browser_download_url") or "")
                if packages:
                    candidate = {
                        "version": tailscale_version,
                        "tag_name": tag,
                        "packages": packages,
                        "prerelease": prerelease,
                        "published_at": published,
                        "html_url": html_url,
                        "browser_download_urls": download_urls,
                    }
                    if (
                        best_tailscale is None
                        or version_key(tailscale_version)
                        > version_key(best_tailscale["version"])
                    ):
                        best_tailscale = candidate
                continue

            for asset in assets:
                name = str(asset.get("name") or "")
                match = EMBY_PATCHED_ASSET.fullmatch(name)
                if not match:
                    continue
                version = match.group("version")
                if tag.startswith("emby-"):
                    tag_name = tag
                elif tag == version:
                    tag_name = emby_release_tag(version)
                else:
                    tag_name = tag or emby_release_tag(version)
                emby.append(
                    {
                        "version": version,
                        "tag_name": tag_name,
                        "package_name": name,
                        "html_url": html_url,
                        "changelog": str(release.get("body") or ""),
                        "published_at": published,
                        "browser_download_url": str(
                            asset.get("browser_download_url") or ""
                        ),
                        "prerelease": prerelease,
                    }
                )

        if len(batch) < 100:
            break

    emby.sort(key=lambda item: version_key(item["version"]), reverse=True)
    return OwnCatalog(emby=emby, tailscale=best_tailscale)
