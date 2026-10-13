"""QNAP QDK-compatible CMS signing and QPKG footer helpers."""

from __future__ import annotations

import dataclasses
import hashlib
import os
from pathlib import Path
import shutil
import struct
import subprocess
from typing import Any


QDK_CODE_SIGNING_TYPE = 254
QDK_EOF_TYPE = 255
PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_QNAP_TRUST_ANCHORS = (
    PROJECT_ROOT / "qnap_trust" / "third_party_v1.pem",
    PROJECT_ROOT / "qnap_trust" / "third_party_v2.pem",
    PROJECT_ROOT / "qnap_trust" / "third_party_v3.pem",
)


class SigningError(RuntimeError):
    """Raised when certificate generation or CMS signing fails."""


@dataclasses.dataclass(frozen=True)
class SigningOptions:
    openssl: str
    signing_dir: Path
    certificate: Path | None
    private_key: Path | None
    chain: Path | None
    common_name: str
    require_qnap_trust: bool = False
    trust_anchors: tuple[Path, ...] = ()


@dataclasses.dataclass(frozen=True)
class SigningMaterial:
    openssl: str
    certificate: Path
    private_key: Path
    chain: Path | None


def qpkg_encrypt_string(total_size: int) -> bytes:
    if total_size <= 0:
        raise SigningError("QPKG file size must be positive")
    # QDK src/qpkg_encrypt.c truncates this decimal value to its first 10 bytes.
    value = str(total_size * 3589 + 1_000_000_000).encode("ascii")[:10]
    if len(value) != 10:
        raise SigningError("invalid QPKG checksum width")
    return value


def rewrite_qpkg_footer_checksum(footer: bytes, total_size: int) -> bytes:
    if len(footer) != 100 or not footer.endswith(b"QNAPQPKG  "):
        raise SigningError("QPKG footer does not match the 100-byte QDK format")
    return footer[:40] + qpkg_encrypt_string(total_size) + footer[50:]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_checked(command: list[str], *, cwd: Path | None = None) -> None:
    display = " ".join(command)
    print(f"[run] {display}")
    result = subprocess.run(command, cwd=cwd, check=False)
    if result.returncode != 0:
        raise SigningError(f"command failed with exit code {result.returncode}: {display}")


def _run(command: list[str], *, capture: bool = True) -> subprocess.CompletedProcess[bytes]:
    result = subprocess.run(command, check=False, capture_output=capture)
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip()
        raise SigningError(f"command failed ({result.returncode}): {' '.join(command)}: {detail}")
    return result


def _validate_subject(common_name: str) -> None:
    if not common_name or any(character in common_name for character in "/\0\r\n"):
        raise SigningError("certificate common name contains unsupported characters")


def _generate_certificate(options: SigningOptions, certificate: Path, key: Path) -> None:
    _validate_subject(options.common_name)
    options.signing_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(options.signing_dir, 0o700)
    temporary_certificate = certificate.with_suffix(certificate.suffix + ".tmp")
    temporary_key = key.with_suffix(key.suffix + ".tmp")
    command = [
        options.openssl,
        "req",
        "-x509",
        "-newkey",
        "rsa:3072",
        "-sha256",
        "-days",
        "3650",
        "-nodes",
        "-subj",
        f"/CN={options.common_name}/O=Local QNAP Package Builder",
        "-addext",
        "basicConstraints=critical,CA:FALSE",
        "-addext",
        "keyUsage=critical,digitalSignature",
        "-addext",
        "extendedKeyUsage=codeSigning",
        "-addext",
        "subjectKeyIdentifier=hash",
        "-keyout",
        str(temporary_key),
        "-out",
        str(temporary_certificate),
    ]
    try:
        _run(command)
        os.chmod(temporary_key, 0o600)
        os.chmod(temporary_certificate, 0o644)
        os.replace(temporary_key, key)
        os.replace(temporary_certificate, certificate)
    finally:
        for temporary in (temporary_key, temporary_certificate):
            if temporary.exists():
                temporary.unlink()


def _validate_material(material: SigningMaterial) -> None:
    for label, path in (
        ("certificate", material.certificate),
        ("private key", material.private_key),
    ):
        if not path.is_file():
            raise SigningError(f"{label} does not exist: {path}")
    if material.chain and not material.chain.is_file():
        raise SigningError(f"certificate chain does not exist: {material.chain}")
    _run([material.openssl, "x509", "-in", str(material.certificate), "-noout", "-checkend", "0"])
    _run([material.openssl, "pkey", "-in", str(material.private_key), "-noout"])
    certificate_key = _run(
        [material.openssl, "x509", "-in", str(material.certificate), "-pubkey", "-noout"]
    ).stdout
    private_key = _run(
        [material.openssl, "pkey", "-in", str(material.private_key), "-pubout"]
    ).stdout
    if certificate_key != private_key:
        raise SigningError("certificate and private key do not match")


def _write_trust_bundle(trust_anchors: tuple[Path, ...], destination: Path) -> Path:
    anchors = trust_anchors or DEFAULT_QNAP_TRUST_ANCHORS
    missing = [str(path) for path in anchors if not path.is_file()]
    if missing:
        raise SigningError(f"QNAP trust anchor does not exist: {', '.join(missing)}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("wb") as output:
        for anchor in anchors:
            data = anchor.read_bytes().rstrip() + b"\n"
            output.write(data)
    return destination


def verify_qnap_certificate_trust(
    material: SigningMaterial,
    work_dir: Path,
    trust_anchors: tuple[Path, ...] = (),
) -> None:
    work_dir.mkdir(parents=True, exist_ok=True)
    bundle = _write_trust_bundle(trust_anchors, work_dir / "qnap-third-party-roots.pem")
    command = [
        material.openssl,
        "verify",
        "-purpose",
        "any",
        "-CAfile",
        str(bundle),
    ]
    if material.chain:
        command.extend(["-untrusted", str(material.chain)])
    command.append(str(material.certificate))
    try:
        _run(command)
    except SigningError as error:
        raise SigningError(
            "signer certificate is not issued by a QNAP third-party code-signing root"
        ) from error


def is_qnap_certificate_trusted(
    material: SigningMaterial,
    work_dir: Path,
    trust_anchors: tuple[Path, ...] = (),
) -> bool:
    try:
        verify_qnap_certificate_trust(material, work_dir, trust_anchors)
        return True
    except SigningError:
        return False


def ensure_certificate_request(
    material: SigningMaterial,
    common_name: str,
    destination: Path,
) -> Path:
    _validate_subject(common_name)
    if destination.is_file():
        return destination
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    try:
        _run(
            [
                material.openssl,
                "req",
                "-new",
                "-sha256",
                "-key",
                str(material.private_key),
                "-subj",
                f"/CN={common_name}/O=QNAP Third-Party Package Publisher",
                "-out",
                str(temporary),
            ]
        )
        os.chmod(temporary, 0o644)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def ensure_signing_material(options: SigningOptions) -> SigningMaterial:
    if not shutil.which(options.openssl) and not Path(options.openssl).is_file():
        raise SigningError(f"openssl executable not found: {options.openssl}")
    if options.certificate:
        certificate = options.certificate.expanduser().resolve()
        key = (options.private_key or options.certificate).expanduser().resolve()
    elif options.private_key:
        raise SigningError("--sign-key requires --sign-cert")
    else:
        certificate = (options.signing_dir / "certificate.pem").resolve()
        key = (options.signing_dir / "private_key.pem").resolve()
        if certificate.exists() != key.exists():
            raise SigningError("default certificate and private key must both exist or both be absent")
        if not certificate.exists():
            _generate_certificate(options, certificate, key)
    material = SigningMaterial(
        openssl=options.openssl,
        certificate=certificate,
        private_key=key,
        chain=options.chain.expanduser().resolve() if options.chain else None,
    )
    _validate_material(material)
    return material


def build_qdk_code_signing_area(cms_message: bytes) -> bytes:
    if not cms_message or len(cms_message) > 0xFFFFFFFF:
        raise SigningError("CMS message size is invalid")
    return (
        b"QDK"
        + bytes([QDK_CODE_SIGNING_TYPE])
        + struct.pack(">I", len(cms_message))
        + cms_message
        + bytes([QDK_EOF_TYPE])
    )


def parse_qdk_area(area: bytes) -> dict[int, list[bytes]]:
    if not area.startswith(b"QDK"):
        raise SigningError("QDK signature area marker is missing")
    records: dict[int, list[bytes]] = {}
    cursor = 3
    while cursor < len(area):
        record_type = area[cursor]
        cursor += 1
        if record_type == QDK_EOF_TYPE:
            if cursor != len(area):
                raise SigningError("unexpected bytes after QDK signature EOF")
            return records
        if cursor + 4 > len(area):
            raise SigningError("truncated QDK signature record size")
        size = struct.unpack_from(">I", area, cursor)[0]
        cursor += 4
        end = cursor + size
        if end > len(area):
            raise SigningError("truncated QDK signature record")
        records.setdefault(record_type, []).append(area[cursor:end])
        cursor = end
    raise SigningError("QDK signature EOF is missing")


def repository_signature_from_area(area: bytes, openssl: str, work_dir: Path) -> str:
    records = parse_qdk_area(area).get(QDK_CODE_SIGNING_TYPE, [])
    if len(records) != 1:
        raise SigningError(f"expected one QDK CMS record, found {len(records)}")
    work_dir.mkdir(parents=True, exist_ok=True)
    cms_path = work_dir / "repository-signature.msg"
    cms_path.write_bytes(records[0])
    encoded = _run([openssl, "cms", "-cmsout", "-in", str(cms_path)]).stdout.replace(b"\n", b"")
    if len(encoded) < 32:
        raise SigningError("CMS repository signature is shorter than 32 bytes")
    return encoded[-32:].decode("ascii")


def _sha1_file(path: Path) -> bytes:
    digest = hashlib.sha1()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.digest()


def verify_qdk_code_signing_area(
    content: Path,
    area: bytes,
    material: SigningMaterial,
    work_dir: Path,
    require_qnap_trust: bool = False,
    trust_anchors: tuple[Path, ...] = (),
) -> None:
    verify_qdk_code_signing_digest(
        _sha1_file(content),
        area,
        material.openssl,
        work_dir,
        require_qnap_trust=require_qnap_trust,
        trust_anchors=trust_anchors,
    )


def verify_qdk_code_signing_digest(
    expected_digest: bytes,
    area: bytes,
    openssl: str,
    work_dir: Path,
    *,
    require_qnap_trust: bool = True,
    trust_anchors: tuple[Path, ...] = (),
) -> None:
    if len(expected_digest) != hashlib.sha1().digest_size:
        raise SigningError("QPKG SHA-1 digest has an invalid size")
    records = parse_qdk_area(area)
    cms_records = records.get(QDK_CODE_SIGNING_TYPE, [])
    if len(cms_records) != 1:
        raise SigningError(f"expected one QDK CMS record, found {len(cms_records)}")
    work_dir.mkdir(parents=True, exist_ok=True)
    cms_path = work_dir / "signature.msg"
    verified_path = work_dir / "verified-digest.bin"
    cms_path.write_bytes(cms_records[0])
    command = [openssl, "cms", "-verify", "-binary"]
    if require_qnap_trust:
        bundle = _write_trust_bundle(
            trust_anchors, work_dir / "qnap-third-party-roots.pem"
        )
        command.extend(["-CAfile", str(bundle)])
    else:
        command.append("-noverify")
    command.extend(["-in", str(cms_path), "-out", str(verified_path)])
    try:
        _run(command)
    except SigningError as error:
        if require_qnap_trust:
            raise SigningError(
                "CMS signer is not trusted by QNAP third-party code-signing roots"
            ) from error
        raise
    if verified_path.read_bytes() != expected_digest:
        raise SigningError("CMS signed digest does not match QPKG content")


def sign_qpkg_content(
    content: Path,
    options: SigningOptions,
    work_dir: Path,
) -> tuple[bytes, SigningMaterial]:
    material = ensure_signing_material(options)
    work_dir.mkdir(parents=True, exist_ok=True)
    if options.require_qnap_trust:
        verify_qnap_certificate_trust(
            material,
            work_dir / "certificate-trust",
            options.trust_anchors,
        )
    digest_path = work_dir / "qpkg.sha1"
    cms_path = work_dir / "qpkg.msg"
    digest_path.write_bytes(_sha1_file(content))
    command = [
        material.openssl,
        "cms",
        "-sign",
        "-binary",
        "-nodetach",
        "-in",
        str(digest_path),
        "-out",
        str(cms_path),
        "-signer",
        str(material.certificate),
        "-inkey",
        str(material.private_key),
    ]
    if material.chain:
        command.extend(["-certfile", str(material.chain)])
    _run(command)
    area = build_qdk_code_signing_area(cms_path.read_bytes())
    verify_qdk_code_signing_area(
        content,
        area,
        material,
        work_dir / "verify",
        require_qnap_trust=options.require_qnap_trust,
        trust_anchors=options.trust_anchors,
    )
    return area, material


def add_signing_arguments(parser: Any) -> None:
    group = parser.add_argument_group("QNAP CMS signing")
    group.add_argument("--no-sign", action="store_true", help="build without a QDK CMS signature")
    group.add_argument("--signing-dir", type=Path, help="reusable auto-generated certificate directory")
    group.add_argument("--sign-cert", type=Path, help="PEM signer certificate or combined PEM")
    group.add_argument("--sign-key", type=Path, help="PEM private key for --sign-cert")
    group.add_argument("--sign-chain", type=Path, help="optional PEM intermediate certificate chain")
    group.add_argument(
        "--allow-untrusted-signing",
        action="store_true",
        help="allow a local self-signed certificate for fixture testing only",
    )
    group.add_argument(
        "--sign-common-name",
        default="EmbyServer QPKG Code Signing",
        help="common name for an auto-generated certificate",
    )
    group.add_argument(
        "--openssl",
        default=shutil.which("openssl") or "openssl",
        help="openssl executable path",
    )


def signing_options_from_args(args: Any, output: Path) -> SigningOptions | None:
    if args.no_sign:
        return None
    signing_dir = (
        args.signing_dir.expanduser().resolve()
        if args.signing_dir
        else (output.parent / ".qnap-signing").resolve()
    )
    return SigningOptions(
        openssl=args.openssl,
        signing_dir=signing_dir,
        certificate=args.sign_cert,
        private_key=args.sign_key,
        chain=args.sign_chain,
        common_name=args.sign_common_name,
        require_qnap_trust=not args.allow_untrusted_signing,
        trust_anchors=DEFAULT_QNAP_TRUST_ANCHORS,
    )
