"""Public URL builders for QNAP feed location / icon fields.

Supports optional GitHub download accelerators (prefix mode), so App Center on
restricted networks can fetch release assets and raw icons via a mirror.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from urllib.parse import urlparse


_GITHUB_RELEASE_RE = re.compile(
    r"^https://github\.com/(?P<repo>[^/]+/[^/]+)/releases/download/"
    r"(?P<tag>[^/]+)/(?P<name>.+)$"
)
_RAW_GITHUB_RE = re.compile(
    r"^https://raw\.githubusercontent\.com/(?P<repo>[^/]+/[^/]+)/"
    r"(?P<ref>[^/]+)/(?P<path>.+)$"
)


@dataclass(frozen=True)
class FeedUrls:
    """Build and rewrite public URLs written into repo.xml."""

    repo: str
    ref: str
    accelerate_prefix: str = ""

    def accelerate(self, url: str) -> str:
        if not url or not self.accelerate_prefix:
            return url
        prefix = self.accelerate_prefix
        if url.startswith(prefix):
            return url
        host = (urlparse(url).netloc or "").lower()
        # Only rewrite canonical GitHub download/raw hosts.
        if host not in {
            "github.com",
            "raw.githubusercontent.com",
            "objects.githubusercontent.com",
        }:
            return url
        return f"{prefix}{url}"

    def raw(self, relative_path: str) -> str:
        path = relative_path.lstrip("/")
        return self.accelerate(
            f"https://raw.githubusercontent.com/{self.repo}/{self.ref}/{path}"
        )

    def release_asset(self, tag: str, filename: str) -> str:
        return self.accelerate(
            f"https://github.com/{self.repo}/releases/download/{tag}/{filename}"
        )

    def maybe_rewrite(self, url: str) -> str:
        """Rewrite GitHub release/raw URLs; pass through others after accelerate()."""
        if not url:
            return url
        release = _GITHUB_RELEASE_RE.fullmatch(url)
        if release:
            return self.release_asset(release.group("tag"), release.group("name"))
        raw = _RAW_GITHUB_RE.fullmatch(url)
        if raw:
            return self.raw(raw.group("path")) if raw.group("repo") == self.repo else self.accelerate(url)
        if "github.com" in url or "githubusercontent.com" in url:
            return self.accelerate(url)
        return url
