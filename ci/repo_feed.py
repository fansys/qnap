"""Generate QNAP App Center feeds: repo.xml and repo_pre.xml."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import re
from typing import Any
import xml.etree.ElementTree as ET

from .config import configured_providers, load_config, resolve_accelerate_prefix
from .own_releases import fetch_own_catalog
from .paths import REPO_PRE_XML_PATH, REPO_XML_PATH
from .providers import FeedContext, get_provider, load_builtin_providers
from .signatures import save_signature_cache
from .upstream import version_key
from .urls import FeedUrls


def _set_text(parent: ET.Element, tag: str, value: str) -> ET.Element:
    child = ET.SubElement(parent, tag)
    child.text = value
    return child


def _format_published_date(value: str) -> str:
    raw = value.replace("T", " ").replace("Z", "").strip()
    if not raw:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", raw):
        return raw
    if re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+", raw):
        return raw.split(".", 1)[0]
    if re.fullmatch(r"\d{4}/\d{2}/\d{2}", raw):
        return raw.replace("/", "-") + " 00:00:00"
    return raw


def empty_repository_xml() -> bytes:
    root = ET.Element("plugins")
    _set_text(root, "cachechk", datetime.now(timezone.utc).strftime("%Y%m%d%H%M"))
    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _append_item(root: ET.Element, item_data: dict[str, object]) -> None:
    item = ET.SubElement(root, "item")
    for tag in (
        "name",
        "internalName",
        "changeLog",
        "category",
        "type",
        "icon80",
        "icon100",
        "description",
    ):
        _set_text(item, tag, str(item_data.get(tag) or ""))
    fw = str(item_data.get("fwVersion") or "")
    if fw:
        _set_text(item, "fwVersion", fw)
    _set_text(item, "version", str(item_data["version"]))
    platforms = item_data.get("platforms") or []
    if not isinstance(platforms, list) or not platforms:
        raise ValueError(f"item {item_data.get('internalName')} has no platforms")
    for platform_data in platforms:
        if not isinstance(platform_data, dict):
            continue
        platform = ET.SubElement(item, "platform")
        _set_text(platform, "platformID", str(platform_data["platformID"]))
        _set_text(platform, "location", str(platform_data["location"]))
        signature = str(platform_data.get("signature") or "")
        if signature:
            _set_text(platform, "signature", signature)
    _set_text(
        item,
        "publishedDate",
        _format_published_date(str(item_data.get("publishedDate") or "")),
    )
    for tag in ("maintainer", "developer", "forumLink", "language", "snapshot", "bannerImg", "tutorialLink"):
        default = "English" if tag == "language" else ""
        _set_text(item, tag, str(item_data.get(tag) or default))


def build_plugins_xml(items: list[dict[str, object]]) -> bytes:
    root = ET.Element("plugins")
    _set_text(root, "cachechk", datetime.now(timezone.utc).strftime("%Y%m%d%H%M"))
    for item in items:
        _append_item(root, item)
    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _pick_latest(items: list[dict[str, Any]]) -> dict[str, Any] | None:
    return items[0] if items else None


def _collect_feed_items(ctx: FeedContext, provider_names: list[str]) -> list[dict[str, object]]:
    load_builtin_providers()
    items: list[dict[str, object]] = []
    for name in provider_names:
        items.extend(get_provider(name).items_for(ctx))
    return items


def _write_items(destination: Path, items: list[dict[str, object]]) -> Path:
    xml = build_plugins_xml(items) if items else empty_repository_xml()
    destination.write_bytes(xml)
    names = ", ".join(str(item["internalName"]) for item in items) or "empty"
    print(f"wrote {destination} ({names})")
    return destination


def write_repo_feeds(
    repo: str,
    *,
    ref: str,
    signature_by_version: dict[str, str],
    openssl: str,
    base_url: str | None = None,
    accelerate_preset: str | None = None,
    accelerate_prefix: str | None = None,
    accelerate_enabled: bool | None = None,
    providers: list[str] | None = None,
    stable_destination: Path = REPO_XML_PATH,
    prerelease_destination: Path = REPO_PRE_XML_PATH,
) -> tuple[Path, Path]:
    """Write repo.xml and repo_pre.xml via registered providers."""
    config = load_config()
    effective_prefix = accelerate_prefix if accelerate_prefix is not None else base_url
    prefix = resolve_accelerate_prefix(
        config=config,
        preset=accelerate_preset,
        prefix=effective_prefix,
        enabled=accelerate_enabled,
    )
    urls = FeedUrls(repo=repo, ref=ref, accelerate_prefix=prefix)
    provider_names = providers or configured_providers(config)

    catalog = fetch_own_catalog(repo)
    stable = [item for item in catalog.emby if not item.get("prerelease")]
    prerelease = [item for item in catalog.emby if item.get("prerelease")]
    stable_emby = _pick_latest(stable)
    pre_candidates = [item for item in (stable_emby, _pick_latest(prerelease)) if item]
    pre_emby = _pick_latest(
        sorted(pre_candidates, key=lambda item: version_key(item["version"]), reverse=True)
    )

    if prefix:
        print(f"feed accelerate prefix: {prefix}")

    common = {
        "repo": repo,
        "ref": ref,
        "urls": urls,
        "signatures": signature_by_version,
        "openssl": openssl,
        "emby_stable": stable_emby,
        "emby_prerelease_pick": pre_emby,
        "own_tailscale": catalog.tailscale,
    }
    stable_items = _collect_feed_items(
        FeedContext(**common, channel="stable"), provider_names
    )
    pre_items = _collect_feed_items(
        FeedContext(**common, channel="prerelease"), provider_names
    )
    save_signature_cache(signature_by_version)
    return (
        _write_items(stable_destination, stable_items),
        _write_items(prerelease_destination, pre_items),
    )
