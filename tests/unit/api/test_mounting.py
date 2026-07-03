"""ORPH-1: assert the unified API mounts the phase routers."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")


def test_create_app_mounts_phase_routers() -> None:
    from aegis.api.main import create_app, mounted_routers

    app = create_app()
    mounted = mounted_routers()

    # geo / compliance / evolve have no required optional deps — must mount.
    for phase in ("geo", "compliance", "evolve"):
        assert phase in mounted, f"{phase} router failed to mount: {mounted}"

    paths = {route.path for route in app.routes}  # type: ignore[attr-defined]
    assert "/healthz" in paths
    # Each phase router carries a /<phase>/health route.
    assert any(p.startswith("/geo") for p in paths)
    assert any(p.startswith("/compliance") for p in paths)
    assert any(p.startswith("/evolve") for p in paths)


async def test_bearer_guard_dev_permissive(monkeypatch) -> None:
    """audit P2-3: with no token in dev/test, the guard is permissive."""
    monkeypatch.setenv("AEGIS_ENV", "test")
    monkeypatch.delenv("AEGIS_API_BEARER_TOKEN", raising=False)
    from aegis.api.main import _bearer_guard

    guard = _bearer_guard()
    assert await guard(None) == "anonymous"


def test_bearer_guard_prod_requires_token(monkeypatch) -> None:
    """audit P2-3: prod without a token must refuse (fail fast)."""
    monkeypatch.setenv("AEGIS_ENV", "prod")
    monkeypatch.delenv("AEGIS_API_BEARER_TOKEN", raising=False)
    from aegis.api.main import _bearer_guard

    with pytest.raises(RuntimeError, match="AEGIS_API_BEARER_TOKEN"):
        _bearer_guard()


async def test_bearer_guard_validates_token(monkeypatch) -> None:
    monkeypatch.setenv("AEGIS_ENV", "prod")
    monkeypatch.setenv("AEGIS_API_BEARER_TOKEN", "s3cret")
    from fastapi import HTTPException

    from aegis.api.main import _bearer_guard

    guard = _bearer_guard()
    assert await guard("Bearer s3cret") == "bearer"
    with pytest.raises(HTTPException):
        await guard("Bearer wrong")
    with pytest.raises(HTTPException):
        await guard(None)
    with pytest.raises(HTTPException):
        await guard("Basic xyz")
