"""Sync patched Emby QNAP packages into this repository's GitHub Releases."""

from __future__ import annotations

from pathlib import Path

from .discover import select_upstream
from .github_client import release_tag_exists
from .package_build import build_patched_package
from .publish import publish_emby_package
from .release_result import SyncResult, emit_released
from .signatures import save_signature_cache
from .tags import emby_release_tag
from .upstream import fetch_releases


def sync_emby(
    repo: str,
    *,
    certificate: Path,
    private_key: Path,
    seven_zip: str,
    openssl: str,
    version: str | None = None,
    allow_prerelease: bool = False,
    dry_run: bool = False,
    force: bool = False,
    signatures_out: Path | None = None,
) -> SyncResult:
    """Publish missing (or force-refresh) Emby releases."""
    produced: dict[str, str] = {}
    released = False
    upstream = select_upstream(
        fetch_releases(),
        version=version,
        allow_prerelease=allow_prerelease,
    )
    for release in upstream:
        tag = emby_release_tag(release.version)
        exists = release_tag_exists(repo, tag)
        if exists and not force:
            print(f"skip existing Emby release tag: {tag}")
            continue
        if dry_run:
            action = "force-upload" if exists and force else "create"
            print(
                f"dry-run: would {action} "
                f"{release.asset_name} as {repo}@{tag}"
            )
            continue
        package, _digest, signature = build_patched_package(
            release,
            certificate=certificate,
            private_key=private_key,
            seven_zip=seven_zip,
            openssl=openssl,
        )
        produced[release.version] = signature
        publish_emby_package(
            repo,
            release,
            package,
            force=force,
            tag_exists=exists,
        )
        released = True
    if signatures_out is not None:
        save_signature_cache(produced, signatures_out)
    emit_released(released)
    return SyncResult(signatures=produced, released=released)
