"""Download upstream Emby QPKG, patch, and self-sign for integrity only."""

from __future__ import annotations

from pathlib import Path
import sys
import tempfile

from .paths import OUT_DIR, PACKAGE_DIR, ROOT, SIGNING_DIR, SOURCE_DIR, ensure_out_dirs, patched_package_name
from .qpkg_sig import extract_repository_signature
from .upstream import ReleaseInfo, download_asset

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from qnap_signing import SigningOptions, sha256_file  # noqa: E402
from repack_emby_qpkg import rebuild_package  # noqa: E402


def build_patched_package(
    release: ReleaseInfo,
    *,
    certificate: Path,
    private_key: Path,
    seven_zip: str,
    openssl: str,
) -> tuple[Path, str, str]:
    ensure_out_dirs()
    source = SOURCE_DIR / release.asset_name
    output = PACKAGE_DIR / patched_package_name(release.version)

    print(f"download {release.asset_url}")
    download_asset(release.asset_url, source)
    expected = release.asset_digest
    if expected.startswith("sha256:"):
        actual = sha256_file(source)
        if actual != expected.removeprefix("sha256:"):
            raise RuntimeError("downloaded QPKG SHA256 does not match GitHub digest")

    options = SigningOptions(
        openssl=openssl,
        signing_dir=SIGNING_DIR,
        certificate=certificate,
        private_key=private_key,
        chain=None,
        common_name="EmbyServer QPKG Code Signing",
        require_qnap_trust=False,
    )
    with tempfile.TemporaryDirectory(prefix="workflow-repack-", dir=OUT_DIR) as temporary:
        work = Path(temporary)
        rebuild_package(source, output, seven_zip, work, options)
        signature = extract_repository_signature(output, openssl)
    digest = sha256_file(output)
    print(f"built {output} sha256={digest}")
    return output, digest, signature
