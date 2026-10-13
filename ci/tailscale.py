"""Mirror Tailscale QNAP QPKGs from pkgs.tailscale.com into this repository."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import re
import subprocess
from typing import Any
import urllib.error

from .github_client import release_tag_exists
from .http_util import HttpError, download_file, head_ok, read_text
from .own_releases import fetch_own_catalog
from .paths import PACKAGE_DIR, ensure_out_dirs
from .qpkg_sig import extract_repository_signature
from .release_result import SyncResult, emit_released
from .signatures import save_signature_cache
from .tags import tailscale_release_tag
from .upstream import GithubError

STABLE_JSON_URL = "https://pkgs.tailscale.com/stable/?mode=json"
STABLE_BASE_URL = "https://pkgs.tailscale.com/stable/"

# QNAP platformID → Tailscale arch key (dqpr-ts / MyQNAP practice).
PLATFORM_ARCH: tuple[tuple[str, str], ...] = (
    ("TS-NASX86", "x86_64"),
    ("TS-NASARM_64", "arm_64"),
    ("TS-X16", "arm_64"),
    ("TS-X28A", "arm_64"),
    ("TS-X32", "arm_64"),
    ("TS-X32U", "arm_64"),
    ("TS-X33", "arm_64"),
    ("TS-X33EU", "arm_64"),
    ("TS-X33U", "arm_64"),
    ("TS-X35", "arm_64"),
    ("TS-X35A", "arm_64"),
    ("TS-X35EU", "arm_64"),
    ("TS-X42", "arm_64"),
    ("TS-XA28A", "arm_64"),
    ("TS-X41", "arm-x41"),
    ("TS-X31P2", "arm-x41"),
    ("TS-X31P3", "arm-x41"),
    ("TS-X31X", "arm-x41"),
    ("TS-X31XU", "arm-x41"),
    ("TS-531P", "arm-x41"),
    ("TS-X31", "arm-x31"),
    ("TS-ARM-X19", "arm-x19"),
    ("OLD_X86", "x86"),
)

TAILSCALE_ICON_80 = "https://download.qnap.com/QPKG/images/QPKG/Tailscale_80.png"
TAILSCALE_ICON_100 = "https://download.qnap.com/QPKG/images/QPKG_100/Tailscale_100.png"
TAILSCALE_CHANGELOG = "https://tailscale.com/changelog"
VERSION_IN_NAME = re.compile(
    r"^Tailscale_(?P<version>\d+\.\d+\.\d+(?:-\d+)?)_(?P<arch>.+)\.qpkg$",
    re.IGNORECASE,
)
KNOWN_ARCHES: tuple[str, ...] = tuple(
    sorted({arch for _platform_id, arch in PLATFORM_ARCH} | {"x86_ce53xx"})
)


def fetch_stable_qpkgs() -> dict[str, Any]:
    try:
        payload = json.loads(read_text(STABLE_JSON_URL))
    except (HttpError, ValueError) as error:
        raise GithubError(f"Tailscale package index request failed: {error}") from error
    if not isinstance(payload, dict):
        raise GithubError("Tailscale package index is not an object")
    qpkgs = payload.get("QPKGs")
    version = str(payload.get("QPKGsVersion") or "")
    if not isinstance(qpkgs, dict) or not version:
        raise GithubError("Tailscale QPKGs / QPKGsVersion missing from index")
    return {
        "version": version,
        "packages": {str(arch): str(name) for arch, name in qpkgs.items()},
    }


def _verify_sha256(package: Path, sha_url: str) -> None:
    try:
        text = read_text(sha_url, timeout=30).strip()
    except HttpError as error:
        if "404" in str(error):
            print(f"warning: no sha256 for {package.name}")
            return
        raise GithubError(f"Tailscale sha256 fetch failed: {error}") from error
    expected = text.split()[0].lower()
    digest = hashlib.sha256()
    with package.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    actual = digest.hexdigest()
    if actual != expected:
        raise RuntimeError(f"Tailscale SHA256 mismatch for {package.name}")


def download_tailscale_packages(
    packages: dict[str, str],
    *,
    version: str,
) -> list[Path]:
    ensure_out_dirs()
    target_dir = PACKAGE_DIR / f"tailscale-{version}"
    target_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for arch, filename in sorted(packages.items()):
        destination = target_dir / filename
        url = f"{STABLE_BASE_URL}{filename}"
        print(f"download {url}")
        download_file(url, destination)
        _verify_sha256(destination, f"{url}.sha256")
        match = VERSION_IN_NAME.fullmatch(filename)
        if match and match.group("arch") != arch:
            print(f"warning: arch key {arch} != filename arch {match.group('arch')}")
        paths.append(destination)
    return paths


def create_tailscale_release(repo: str, version: str, packages: list[Path]) -> None:
    tag = tailscale_release_tag(version)
    title = f"Tailscale {version}"
    notes = (
        f"Mirrored Tailscale QNAP packages for {version} from "
        f"{STABLE_BASE_URL}.\n\n"
        f"Changelog: {TAILSCALE_CHANGELOG}\n"
    )
    command = [
        "gh",
        "release",
        "create",
        tag,
        *[str(path) for path in packages],
        "--repo",
        repo,
        "--title",
        title,
        "--notes",
        notes,
    ]
    subprocess.run(command, check=True)


def resolve_packages(version: str | None = None) -> tuple[str, dict[str, str]]:
    """Return (product_version, arch→filename)."""
    if not version:
        index = fetch_stable_qpkgs()
        return str(index["version"]), dict(index["packages"])

    product = version.strip().removeprefix("v")
    if re.fullmatch(r"\d+\.\d+\.\d+-\d+", product):
        file_version = product
        tag_version = product.rsplit("-", 1)[0]
    elif re.fullmatch(r"\d+\.\d+\.\d+", product):
        file_version = f"{product}-1"
        tag_version = product
    else:
        raise ValueError(
            f"invalid Tailscale version {version!r}; expected like 1.104.1"
        )

    def _probe(arch: str) -> tuple[str, str] | None:
        filename = f"Tailscale_{file_version}_{arch}.qpkg"
        url = f"{STABLE_BASE_URL}{filename}"
        return (arch, filename) if head_ok(url) else None

    packages: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(_probe, arch) for arch in KNOWN_ARCHES]
        for future in as_completed(futures):
            hit = future.result()
            if hit:
                packages[hit[0]] = hit[1]
    if not packages:
        raise GithubError(
            f"no Tailscale QPKGs found on stable for version {tag_version}"
        )
    return tag_version, packages


def sync_tailscale(
    repo: str,
    *,
    openssl: str,
    version: str | None = None,
    dry_run: bool = False,
    signatures_out: Path | None = None,
    force: bool = False,
) -> SyncResult:
    """Ensure Tailscale QPKGs exist as a GitHub Release."""
    produced: dict[str, str] = {}
    resolved_version, packages = resolve_packages(version)
    tag = tailscale_release_tag(resolved_version)
    if release_tag_exists(repo, tag) and not force:
        print(f"skip existing Tailscale release tag: {tag}")
        if signatures_out is not None:
            save_signature_cache(produced, signatures_out)
        emit_released(False)
        return SyncResult(signatures=produced, released=False)
    if dry_run:
        print(
            f"dry-run: would download/publish Tailscale {resolved_version} "
            f"({len(packages)} qpkg) as {repo}@{tag}"
        )
        if signatures_out is not None:
            save_signature_cache(produced, signatures_out)
        emit_released(False)
        return SyncResult(signatures=produced, released=False)
    paths = download_tailscale_packages(packages, version=resolved_version)
    for path in paths:
        match = VERSION_IN_NAME.fullmatch(path.name)
        arch = match.group("arch") if match else path.stem
        try:
            produced[f"tailscale:{resolved_version}:{arch}"] = extract_repository_signature(
                path, openssl
            )
        except Exception as error:  # noqa: BLE001
            print(f"warning: could not extract signature from {path.name}: {error}")
    if release_tag_exists(repo, tag) and force:
        subprocess.run(
            [
                "gh",
                "release",
                "upload",
                tag,
                *[str(path) for path in paths],
                "--repo",
                repo,
                "--clobber",
            ],
            check=True,
        )
    else:
        create_tailscale_release(repo, resolved_version, paths)
    if signatures_out is not None:
        save_signature_cache(produced, signatures_out)
    emit_released(True)
    return SyncResult(signatures=produced, released=True)


def list_tailscale_release(repo: str) -> dict[str, Any] | None:
    """Newest mirrored Tailscale release (wrapper over single-pass catalog)."""
    return fetch_own_catalog(repo).tailscale
