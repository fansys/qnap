"""Filesystem layout when this directory is the GitHub repository root."""

from __future__ import annotations

from pathlib import Path

# workflow/ is the repository root on GitHub.
ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "out"
SOURCE_DIR = OUT_DIR / "source"
PACKAGE_DIR = OUT_DIR / "packages"
REPO_XML_PATH = ROOT / "repo.xml"
REPO_PRE_XML_PATH = ROOT / "repo_pre.xml"
SIGNATURES_PATH = ROOT / "signatures.json"
SIGNATURES_EMBY_FRAGMENT = OUT_DIR / "signatures-emby.json"
SIGNATURES_TAILSCALE_FRAGMENT = OUT_DIR / "signatures-tailscale.json"
SIGNING_DIR = ROOT / "signing"
DEFAULT_CERT = SIGNING_DIR / "certificate.pem"
DEFAULT_KEY = SIGNING_DIR / "private_key.pem"
ICON_RELATIVE = "assets/emby-icon.gif"


def patched_package_name(version: str) -> str:
    return f"emby-server-qnap_{version}_x86_64_patched.qpkg"


def ensure_out_dirs() -> None:
    for directory in (OUT_DIR, SOURCE_DIR, PACKAGE_DIR):
        directory.mkdir(parents=True, exist_ok=True)
