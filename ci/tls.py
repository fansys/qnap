"""Verified TLS context with portable CA bundle discovery."""

from __future__ import annotations

import os
from pathlib import Path
import ssl


CA_BUNDLE_CANDIDATES = (
    "/etc/ssl/cert.pem",
    "/etc/ssl/certs/ca-certificates.crt",
    "/opt/homebrew/etc/ca-certificates/cert.pem",
    "/etc/pki/tls/certs/ca-bundle.crt",
)


def verified_context() -> ssl.SSLContext:
    configured = os.environ.get("SSL_CERT_FILE") or os.environ.get("REQUESTS_CA_BUNDLE")
    if configured:
        return ssl.create_default_context(cafile=configured)

    context = ssl.create_default_context()
    if context.cert_store_stats()["x509_ca"]:
        return context

    for candidate in CA_BUNDLE_CANDIDATES:
        if Path(candidate).is_file():
            return ssl.create_default_context(cafile=candidate)
    return context
