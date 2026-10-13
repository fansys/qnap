"""Select which upstream Emby QNAP releases to process."""

from __future__ import annotations

from .upstream import ReleaseInfo


def select_upstream(
    releases: list[ReleaseInfo],
    *,
    version: str | None,
    allow_prerelease: bool,
) -> list[ReleaseInfo]:
    eligible = [
        item
        for item in releases
        if allow_prerelease or not item.prerelease
    ]
    if version:
        matched = [item for item in eligible if item.version == version]
        if not matched:
            raise ValueError(f"upstream version not found: {version}")
        return matched[:1]
    if not eligible:
        raise ValueError("no eligible upstream Emby QNAP x86_64 releases")
    return eligible[:1]
