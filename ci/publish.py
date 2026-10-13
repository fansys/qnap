"""Publish a patched Emby package to this repository's GitHub Release."""

from __future__ import annotations

from pathlib import Path
import subprocess

from .tags import emby_release_tag
from .upstream import ReleaseInfo


def create_github_release(repo: str, release: ReleaseInfo, package: Path) -> None:
    tag = emby_release_tag(release.version)
    title = f"Emby Server QNAP {release.version} (patched)"
    notes = (
        f"Patched Emby QNAP x86_64 package for {release.version}.\n\n"
        f"Upstream: {release.html_url}\n\n"
        "Signed with the repository self-signed certificate for integrity only; "
        "this signature does not pass QNAP QoS trust verification.\n"
    )
    command = [
        "gh",
        "release",
        "create",
        tag,
        str(package),
        "--repo",
        repo,
        "--title",
        title,
        "--notes",
        notes,
    ]
    if release.prerelease:
        command.append("--prerelease")
    subprocess.run(command, check=True)
