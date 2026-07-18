"""
CLI subcommands for Phase 3 — exposed via `aegis predict ...`.

Subcommands:
    aegis predict run    — run inference for a single trend (DB-backed)
    aegis predict serve  — start the FastAPI server
    aegis predict eval   — run a backtest from a CSV / parquet samples file
    aegis predict bench  — synthetic latency benchmark (no DB needed)

Phase 1 already owns the top-level `aegis` CLI. This module is loaded
by Phase 1's CLI entrypoint when it sees the `predict` subcommand —
keeping Phase 3 importable without forcing a Typer dependency on
in-process Phase 2 callers.
"""

from .commands import register_subcommands

__all__ = ["register_subcommands"]
