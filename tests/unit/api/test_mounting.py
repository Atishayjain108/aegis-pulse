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
