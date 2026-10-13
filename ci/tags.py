"""Release tag conventions for mirrored packages."""

from __future__ import annotations


def emby_release_tag(version: str) -> str:
    return f"emby-{version}"


def tailscale_release_tag(version: str) -> str:
    return f"tailscale-{version}"


def parse_emby_tag(tag: str) -> str | None:
    if tag.startswith("emby-"):
        return tag.removeprefix("emby-")
    return None


def parse_tailscale_tag(tag: str) -> str | None:
    if tag.startswith("tailscale-"):
        return tag.removeprefix("tailscale-")
    return None
