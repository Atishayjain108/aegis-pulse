"""
Feature engineering — Phase 1 ↔ Phase 3 bridge.

Reads `ProductSignal` rows from the Phase 1 TimescaleDB schema
(via `aegis.db.signals`) and turns them into a `FeatureWindow`
that Phase 3 models consume.

Modules:
    builder.py     — main aggregation pipeline; raw signals → tensor
    velocity.py    — multi-horizon velocity computations (logspace)
    graph.py       — creator-graph builder for the relational model

Design choices:
    * All outputs are deterministic given input — no random shuffling,
      no clock-dependent values.
    * Missing buckets are zero-filled; we never interpolate, because
      that would manufacture data the upstream agents can't see.
    * The `tenant_id` from Phase 1 is preserved end-to-end; the
      feature window MUST NOT cross tenant boundaries.
"""

from .builder import build_feature_window, build_window_from_rows
from .graph import CreatorGraph, build_creator_graph
from .velocity import VelocityWindow, compute_velocities

__all__ = [
    "VelocityWindow",
    "compute_velocities",
    "CreatorGraph",
    "build_creator_graph",
    "build_feature_window",
    "build_window_from_rows",
]
