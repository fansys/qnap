"""Extensible feed item providers for repo.xml generation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol

from .urls import FeedUrls


@dataclass(frozen=True)
class FeedContext:
    repo: str
    ref: str
    urls: FeedUrls
    signatures: dict[str, str]
    openssl: str
    emby_stable: dict[str, Any] | None = None
    emby_prerelease_pick: dict[str, Any] | None = None
    own_tailscale: dict[str, Any] | None = None
    channel: str = "stable"  # stable | prerelease


class FeedProvider(Protocol):
    name: str

    def items_for(self, ctx: FeedContext) -> list[dict[str, object]]:
        ...


ProviderFactory = Callable[[], FeedProvider]
_REGISTRY: dict[str, ProviderFactory] = {}


def register_provider(name: str, factory: ProviderFactory) -> None:
    _REGISTRY[name] = factory


def get_provider(name: str) -> FeedProvider:
    if name not in _REGISTRY:
        raise KeyError(f"unknown feed provider: {name}")
    return _REGISTRY[name]()


def list_provider_names() -> list[str]:
    return sorted(_REGISTRY)


def load_builtin_providers() -> None:
    """Register built-in providers (lazy import to avoid cycles)."""
    if _REGISTRY:
        return

    from . import emby_feed, tailscale_feed

    register_provider("emby", emby_feed.EmbyProvider)
    register_provider("tailscale", tailscale_feed.TailscaleProvider)
