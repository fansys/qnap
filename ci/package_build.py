"""Download upstream Emby QPKG, patch, and self-sign for integrity only."""

from __future__ import annotations

from pathlib import Path
import sys
import tempfile

from .logutil import StepTimer, file_size, log, log_kv
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

    log_kv(
        "build",
        "download upstream QPKG",
        {"url": release.asset_url, "dest": source, "expected_size": release.asset_size},
    )
    with StepTimer("build", f"download {release.asset_name}"):
        download_asset(release.asset_url, source)
    log_kv("build", "downloaded", {"path": source, "size": file_size(source)})

    expected = release.asset_digest
    if expected.startswith("sha256:"):
        actual = sha256_file(source)
        log_kv(
            "build",
            "sha256 check",
            {"expected": expected.removeprefix("sha256:"), "actual": actual},
        )
        if actual != expected.removeprefix("sha256:"):
            raise RuntimeError("downloaded QPKG SHA256 does not match GitHub digest")
    else:
        log("build", "sha256 check skipped (no digest on release asset)")

    options = SigningOptions(
        openssl=openssl,
        signing_dir=SIGNING_DIR,
        certificate=certificate,
        private_key=private_key,
        chain=None,
        common_name="EmbyServer QPKG Code Signing",
        require_qnap_trust=False,
    )
    log_kv(
        "build",
        "patch+sign",
        {
            "source": source,
            "output": output,
            "seven_zip": seven_zip,
            "openssl": openssl,
            "require_qnap_trust": False,
        },
    )
    with tempfile.TemporaryDirectory(prefix="workflow-repack-", dir=OUT_DIR) as temporary:
        work = Path(temporary)
        with StepTimer("build", f"repack {release.version}"):
            rebuild_package(source, output, seven_zip, work, options)
        with StepTimer("build", "extract repository signature"):
            signature = extract_repository_signature(output, openssl)
    digest = sha256_file(output)
    log_kv(
        "build",
        "patched package ready",
        {
            "path": output,
            "size": file_size(output),
            "sha256": digest,
            "signature_len": len(signature),
        },
    )
    return output, digest, signature
