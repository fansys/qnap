"""Tailscale feed provider for QNAP repo.xml."""

from __future__ import annotations

from .paths import PACKAGE_DIR
from .providers import FeedContext
from .qpkg_sig import extract_repository_signature
from .tailscale import (
    PLATFORM_ARCH,
    TAILSCALE_CHANGELOG,
    TAILSCALE_ICON_100,
    TAILSCALE_ICON_80,
)


class TailscaleProvider:
    name = "tailscale"

    def items_for(self, ctx: FeedContext) -> list[dict[str, object]]:
        release = ctx.own_tailscale
        if not release:
            return []
        return [build_tailscale_feed_item(ctx, release)]


def build_tailscale_feed_item(
    ctx: FeedContext,
    release: dict,
) -> dict[str, object]:
    version = str(release["version"])
    tag = str(release["tag_name"])
    packages: dict[str, str] = release["packages"]
    urls: dict[str, str] = dict(release.get("browser_download_urls") or {})
    platforms: list[dict[str, str]] = []
    seen_platform: set[str] = set()
    for platform_id, arch in PLATFORM_ARCH:
        if platform_id in seen_platform:
            continue
        filename = packages.get(arch)
        if not filename:
            continue
        seen_platform.add(platform_id)
        canonical = urls.get(arch) or (
            f"https://github.com/{ctx.repo}/releases/download/{tag}/{filename}"
        )
        location = ctx.urls.maybe_rewrite(canonical)
        sig_key = f"tailscale:{version}:{arch}"
        signature = ctx.signatures.get(sig_key, "")
        if not signature:
            local = PACKAGE_DIR / f"tailscale-{version}" / filename
            if local.is_file():
                try:
                    signature = extract_repository_signature(local, ctx.openssl)
                    ctx.signatures[sig_key] = signature
                except Exception as error:  # noqa: BLE001
                    print(f"warning: signature extract failed for {filename}: {error}")
        platforms.append(
            {
                "platformID": platform_id,
                "location": location,
                "signature": signature,
            }
        )
    if not platforms:
        raise RuntimeError(f"no Tailscale platforms available for {version}")
    return {
        "name": "Tailscale",
        "internalName": "Tailscale",
        "changeLog": TAILSCALE_CHANGELOG,
        "category": "Communications",
        "type": "Communications",
        "icon80": TAILSCALE_ICON_80,
        "icon100": TAILSCALE_ICON_100,
        "description": (
            "Tailscale is a mesh VPN that makes it easy to connect your devices, "
            "wherever they are."
        ),
        "fwVersion": "4.3.4",
        "version": version,
        "platforms": platforms,
        "publishedDate": str(release.get("published_at") or ""),
        "maintainer": "Tailscale mirror",
        "developer": "Tailscale, Inc.",
        "forumLink": "https://tailscale.com/docs/integrations/qnap",
        "language": "English",
        "snapshot": "https://download.qnap.com/QPKG/img/Tailscale_640x400_1.png",
        "bannerImg": "",
        "tutorialLink": "https://tailscale.com/docs/integrations/qnap",
    }
