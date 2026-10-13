"""Extract QNAP App Center repository_signature from a QPKG."""

from __future__ import annotations

from pathlib import Path
import sys
import tempfile

from .paths import OUT_DIR, ROOT, ensure_out_dirs

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from qnap_signing import repository_signature_from_area  # noqa: E402
from repack_emby_qpkg import QpkgLayout  # noqa: E402


def extract_repository_signature(package: Path, openssl: str) -> str:
    ensure_out_dirs()
    layout, _ = QpkgLayout.from_path(package)
    content_end = layout.script_size + layout.control_size + layout.payload_size
    with package.open("rb") as stream:
        stream.seek(content_end)
        area = stream.read(layout.footer_size - 100)
    with tempfile.TemporaryDirectory(prefix="workflow-signature-", dir=OUT_DIR) as temporary:
        return repository_signature_from_area(area, openssl, Path(temporary))
