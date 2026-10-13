"""Sync patched Emby QNAP packages into this repository's GitHub Releases."""

from __future__ import annotations

from pathlib import Path

from .discover import select_upstream
from .github_client import release_tag_exists
from .logutil import StepTimer, file_size, log, log_kv
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
    log_kv(
        "emby",
        "sync config",
        {
            "repo": repo,
            "version": version or "latest",
            "allow_prerelease": allow_prerelease,
            "force": force,
            "dry_run": dry_run,
            "cert": certificate,
            "key": private_key,
            "seven_zip": seven_zip,
            "openssl": openssl,
        },
    )
    with StepTimer("emby", "fetch upstream Emby.Releases"):
        upstream_all = fetch_releases()
    log("emby", f"upstream candidates scanned={len(upstream_all)}")
    upstream = select_upstream(
        upstream_all,
        version=version,
        allow_prerelease=allow_prerelease,
    )
    for item in upstream:
        log_kv(
            "emby",
            "selected upstream",
            {
                "version": item.version,
                "tag": item.tag_name,
                "prerelease": item.prerelease,
                "asset": item.asset_name,
                "size": item.asset_size,
                "url": item.asset_url,
                "html": item.html_url,
            },
        )

    for release in upstream:
        tag = emby_release_tag(release.version)
        with StepTimer("emby", f"check release tag {tag}"):
            exists = release_tag_exists(repo, tag)
        if exists and not force:
            log("emby", f"skip existing release tag={tag}")
            continue
        if dry_run:
            action = "force-upload" if exists and force else "create"
            log(
                "emby",
                f"dry-run would {action} asset={release.asset_name} repo={repo} tag={tag}",
            )
            continue
        with StepTimer("emby", f"build patched package {release.version}"):
            package, digest, signature = build_patched_package(
                release,
                certificate=certificate,
                private_key=private_key,
                seven_zip=seven_zip,
                openssl=openssl,
            )
        log_kv(
            "emby",
            "package ready",
            {
                "path": package,
                "size": file_size(package),
                "sha256": digest,
                "signature": signature[:16] + "..." if len(signature) > 16 else signature,
            },
        )
        produced[release.version] = signature
        action = "force-upload" if exists and force else "create"
        with StepTimer("emby", f"{action} GitHub release {tag}"):
            publish_emby_package(
                repo,
                release,
                package,
                force=force,
                tag_exists=exists,
            )
        log("emby", f"published tag={tag} action={action}")
        released = True

    if signatures_out is not None:
        save_signature_cache(produced, signatures_out)
        log_kv(
            "emby",
            "wrote signature fragment",
            {"path": signatures_out, "keys": len(produced)},
        )
    emit_released(released)
    log_kv(
        "emby",
        "sync summary",
        {"released": released, "signature_keys": sorted(produced.keys()) or "-"},
    )
    return SyncResult(signatures=produced, released=released)
