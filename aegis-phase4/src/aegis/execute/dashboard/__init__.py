"""Static assets for the Phase 4 operator dashboard.

This package is import-only as a way of telling ``setuptools`` to ship
the bundled ``static/`` directory as ``package-data``. There is no
Python API here.

The single static file (``static/index.html``) is a self-contained
vanilla-JS page: it pulls a snapshot from ``GET /snapshot`` and
subscribes to live updates over Server-Sent Events at ``/stream``. No
build tooling, no bundler, no external dependencies — by design.

The FastAPI app mounts this directory at ``/dashboard`` in
``aegis.execute.api.app.build_app``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

#: Absolute path to the bundled static dashboard directory.
STATIC_DIR: Final[Path] = Path(__file__).resolve().parent / "static"

__all__ = ["STATIC_DIR"]
