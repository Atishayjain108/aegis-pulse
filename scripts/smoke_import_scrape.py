#!/usr/bin/env python
"""Image-level import smoke test — walks every module under aegis.scrape.sources
(and the scrape package root) and imports it.

WHY THIS EXISTS: on 2026-07-02 the security remediation added `defusedxml`
imports to six adapters. The dependency landed in the host venv but never in
the autonomous container image, so every scrape topic raised ImportError from
2026-07-03 05:47 UTC onward — silently, for 12 days. No test ran inside the
built image, so nothing caught the host/container venv divergence.

This script is that test. It must run INSIDE the built image (CI and/or a
post-build step), never only on the host:

    docker compose --profile autonomous run --rm --entrypoint "" autonomous \
        python /app/scripts/smoke_import_scrape.py

Exit 0  = every module under aegis.scrape (recursively) imports.
Exit 1  = at least one ImportError; each failure is printed as
          FAIL <module>: <error>.

No fixtures, no mocks, no network. Import or die.
"""

from __future__ import annotations

import importlib
import pkgutil
import sys


def walk_and_import(package_name: str) -> list[tuple[str, str]]:
    """Import package_name and every submodule below it; return failures."""
    failures: list[tuple[str, str]] = []
    try:
        pkg = importlib.import_module(package_name)
    except Exception as exc:
        return [(package_name, f"{type(exc).__name__}: {exc}")]

    prefix = pkg.__name__ + "."
    for modinfo in pkgutil.walk_packages(pkg.__path__, prefix):
        name = modinfo.name
        try:
            importlib.import_module(name)
            print(f"OK   {name}")
        except Exception as exc:
            failures.append((name, f"{type(exc).__name__}: {exc}"))
            print(f"FAIL {name}: {type(exc).__name__}: {exc}")
    return failures


def main() -> int:
    failures = walk_and_import("aegis.scrape")
    print(f"\n{'=' * 60}")
    if failures:
        print(f"IMPORT SMOKE TEST FAILED — {len(failures)} module(s) broken:")
        for name, err in failures:
            print(f"  FAIL {name}: {err}")
        return 1
    print("IMPORT SMOKE TEST PASSED — all aegis.scrape modules import.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
