"""
aegis.security.middleware.headers — Security headers FastAPI middleware.

Injects the following headers on every HTTP response:

    Content-Security-Policy    — restricts resource loading
    Strict-Transport-Security  — enforces HTTPS
    X-Frame-Options            — prevents clickjacking
    X-Content-Type-Options     — prevents MIME sniffing
    Referrer-Policy            — limits referrer leakage
    Permissions-Policy         — disables dangerous browser APIs
    Cross-Origin-Opener-Policy — COOP: isolates browsing context
    Cross-Origin-Embedder-Policy — COEP: requires opt-in cross-origin

Usage::

    from fastapi import FastAPI
    from aegis.security.middleware.headers import SecurityHeadersMiddleware

    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware)
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from aegis.security.config import SecurityConfig, get_security_config


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Starlette/FastAPI middleware that adds security headers to all responses.

    Parameters
    ----------
    app:
        The ASGI application.
    config:
        Injected config; defaults to global singleton.
    enable_hsts:
        Set to False in local HTTP development to avoid breaking browsers.
    hsts_max_age:
        HSTS ``max-age`` in seconds (default = 1 year).
    """

    def __init__(
        self,
        app: object,
        config: SecurityConfig | None = None,
        *,
        enable_hsts: bool = False,  # False by default for local dev (no TLS)
        hsts_max_age: int = 31_536_000,  # 1 year
    ) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self._cfg = config or get_security_config()
        self._enable_hsts = enable_hsts
        self._hsts_max_age = hsts_max_age
        self._headers = self._build_headers()

    def _build_csp(self) -> str:
        """Build the Content-Security-Policy header value."""
        directives: list[str] = [
            "default-src 'self'",
            # Scripts: self + inline scripts (needed for Vite dev; tighten in prod)
            "script-src 'self' 'unsafe-inline' 'unsafe-eval'",
            # Styles: self + inline (shadcn/ui injects inline styles)
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
            # Fonts
            "font-src 'self' https://fonts.gstatic.com",
            # Images: self + data URIs (chart thumbnails)
            "img-src 'self' data: blob:",
            # Connections: allow SSE back to same origin + localhost ports
            "connect-src 'self' ws://localhost:* wss://localhost:* http://localhost:*",
            # Workers (Service Worker for PWA)
            "worker-src 'self' blob:",
            # Frames: deny by default
            "frame-src 'none'",
            "frame-ancestors 'none'",
            # Forms
            "form-action 'self'",
            # Upgrade insecure requests in production
            "upgrade-insecure-requests",
        ]
        if self._cfg.csp_report_uri:
            directives.append(f"report-uri {self._cfg.csp_report_uri}")
        return "; ".join(directives)

    def _build_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {
            # Prevent MIME sniffing
            "X-Content-Type-Options": "nosniff",
            # Prevent clickjacking
            "X-Frame-Options": "DENY",
            # Reduce referrer leakage
            "Referrer-Policy": "strict-origin-when-cross-origin",
            # Disable dangerous browser features
            "Permissions-Policy": (
                "accelerometer=(), "
                "camera=(), "
                "geolocation=(), "
                "gyroscope=(), "
                "magnetometer=(), "
                "microphone=(), "
                "payment=(), "
                "usb=()"
            ),
            # COOP: prevents cross-origin opener from accessing the window object
            "Cross-Origin-Opener-Policy": "same-origin",
            # COEP: requires cross-origin resources to opt in
            # (needed for SharedArrayBuffer / Atomics in some tools)
            "Cross-Origin-Embedder-Policy": "require-corp",
            # CSP
            "Content-Security-Policy": self._build_csp(),
            # Remove server fingerprint
            "Server": "aegis",
            # Prevent caching of sensitive pages
            "Cache-Control": "no-store",
            "Pragma": "no-cache",
        }
        if self._enable_hsts:
            headers["Strict-Transport-Security"] = (
                f"max-age={self._hsts_max_age}; includeSubDomains; preload"
            )
        return headers

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        response = await call_next(request)
        # Inject security headers (do not overwrite existing server-set headers)
        for name, value in self._headers.items():
            if name not in response.headers:
                response.headers[name] = value
        return response
