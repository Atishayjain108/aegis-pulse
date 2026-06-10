"""
aegis.security.tls.helpers — mkcert + local TLS certificate management.

Provides utilities for:
    - Generating locally-trusted TLS certs via mkcert
    - Verifying cert validity / expiry
    - Loading certs into FastAPI / uvicorn config

FREE-TIER PATH: Uses mkcert (OSS) for local development TLS.
PAID PATH (OPTIONAL): Traefik + Let's Encrypt ACME for production.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import structlog

_log = structlog.get_logger(__name__)

# Default cert output directory (relative to project root)
_CERTS_DIR = Path("certs")


def ensure_mkcert_installed() -> bool:
    """Check if mkcert is available on PATH.

    Returns True if found, False otherwise.
    """
    return shutil.which("mkcert") is not None


def install_mkcert_ca() -> bool:
    """Install mkcert's local CA into the system trust store.

    Must be run once per machine (requires sudo on Linux).

    Returns True on success.
    """
    if not ensure_mkcert_installed():
        _log.error(
            "tls.mkcert_not_found",
            note="Install with: apt install mkcert OR brew install mkcert",
        )
        return False
    try:
        subprocess.run(["mkcert", "-install"], check=True, capture_output=True)
        _log.info("tls.ca_installed")
        return True
    except subprocess.CalledProcessError as exc:
        _log.error("tls.ca_install_failed", stderr=exc.stderr.decode()[:200])
        return False


def generate_local_cert(
    *domains: str,
    output_dir: Path | str = _CERTS_DIR,
    cert_name: str = "server",
) -> tuple[Path, Path]:
    """Generate a locally-trusted TLS certificate for ``domains``.

    Parameters
    ----------
    *domains:
        Hostnames / IPs to include in the cert SAN.
        Defaults to ``localhost 127.0.0.1 ::1``.
    output_dir:
        Directory to write ``<cert_name>.crt`` and ``<cert_name>.key``.
    cert_name:
        Base filename for the cert/key pair.

    Returns
    -------
    tuple[Path, Path]
        ``(cert_path, key_path)``

    Raises
    ------
    RuntimeError
        If mkcert is not installed.
    """
    if not ensure_mkcert_installed():
        raise RuntimeError(
            "[AEGIS-SEC-0071] mkcert not found.  "
            "Install with: apt install mkcert"
        )

    if not domains:
        domains = ("localhost", "127.0.0.1", "::1")

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    cert_file = out / f"{cert_name}.crt"
    key_file = out / f"{cert_name}.key"

    cmd = [
        "mkcert",
        f"-cert-file={cert_file}",
        f"-key-file={key_file}",
        *domains,
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
        _log.info(
            "tls.cert_generated",
            cert=str(cert_file),
            domains=domains,
        )
        return cert_file, key_file
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"[AEGIS-SEC-0072] mkcert failed: {exc.stderr.decode()[:300]}"
        ) from exc


def cert_days_remaining(cert_path: str | Path) -> int:
    """Return the number of days until the certificate expires.

    Returns -1 if the cert cannot be read or has already expired.
    """
    try:
        import datetime
        import ssl

        ssl.DER_cert_to_PEM_cert(
            ssl.get_server_certificate(("localhost", 443)).encode()
        )
        # Use cryptography library for parsing if available
        try:
            from cryptography import x509
            from cryptography.hazmat.backends import default_backend

            pem = Path(cert_path).read_bytes()
            c = x509.load_pem_x509_certificate(pem, default_backend())
            delta = c.not_valid_after_utc - datetime.datetime.now(tz=datetime.UTC)
            return max(int(delta.days), -1)
        except ImportError:
            _log.debug("tls.cryptography_not_installed")
            return -1
    except Exception:
        return -1


def uvicorn_ssl_kwargs(cert_path: str | Path, key_path: str | Path) -> dict[str, str]:
    """Return uvicorn SSL kwargs for ``uvicorn.run()``.

    Usage::

        uvicorn.run(app, host="0.0.0.0", port=8300, **uvicorn_ssl_kwargs(...))
    """
    return {
        "ssl_certfile": str(cert_path),
        "ssl_keyfile": str(key_path),
    }
