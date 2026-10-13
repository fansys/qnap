"""Emby feed provider for QNAP repo.xml."""

from __future__ import annotations

from typing import Any

from .paths import ICON_RELATIVE
from .providers import FeedContext
from .signatures import resolve_signature

PLATFORM_ID = "TS-NASX86"
INTERNAL_NAME = "EmbyServer"
DISPLAY_NAME = "Emby Server"
CATEGORY = "Fansy"
APP_TYPE = "Entertainment"
FW_VERSION = "4.3.4"


def build_emby_feed_item(
    ctx: FeedContext,
    selected: dict[str, Any],
) -> dict[str, object]:
    version = str(selected["version"])
    package_name = str(selected["package_name"])
    tag = str(selected["tag_name"])
    canonical = str(selected.get("browser_download_url") or "") or (
        f"https://github.com/{ctx.repo}/releases/download/{tag}/{package_name}"
    )
    package_url = ctx.urls.maybe_rewrite(canonical)
    signature = resolve_signature(
        version,
        package_name,
        ctx.signatures,
        ctx.openssl,
        download_url=canonical,
    )
    return {
        "name": DISPLAY_NAME,
        "internalName": INTERNAL_NAME,
        "changeLog": str(selected.get("html_url") or ""),
        "category": CATEGORY,
        "type": APP_TYPE,
        "icon80": ctx.urls.raw(ICON_RELATIVE),
        "icon100": ctx.urls.raw(ICON_RELATIVE),
        "description": str(selected.get("changelog") or f"Emby Server {version} for QNAP"),
        "fwVersion": FW_VERSION,
        "version": version,
        "platforms": [
            {
                "platformID": PLATFORM_ID,
                "location": package_url,
                "signature": signature,
            }
        ],
        "publishedDate": str(selected.get("published_at") or ""),
        "maintainer": "Emby QNAP Repository",
        "developer": "MediaBrowser",
        "forumLink": str(selected.get("html_url") or ""),
        "language": "English",
        "snapshot": "",
        "bannerImg": "",
        "tutorialLink": "",
    }


class EmbyProvider:
    name = "emby"

    def items_for(self, ctx: FeedContext) -> list[dict[str, object]]:
        selected = (
            ctx.emby_prerelease_pick if ctx.channel == "prerelease" else ctx.emby_stable
        )
        if not selected:
            return []
        return [build_emby_feed_item(ctx, selected)]
