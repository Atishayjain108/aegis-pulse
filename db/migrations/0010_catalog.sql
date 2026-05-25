-- AEGIS Pulse Phase 10 — catalog SQLite DDL.
--
-- This file is informational. The actual schema is created at runtime by
-- aegis.datalake.catalog.registry.LakeCatalog._init_schema().
--
-- Why a separate file? So database admins inspecting the lake have a single
-- place to see the canonical layout. Future migrations should be added via
-- the aegis.datalake.migrations module (Python-registered) rather than as
-- separate SQL files — see docs/phase10/ARCHITECTURE.md.

PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA foreign_keys = ON;

-- Schema version singleton — exactly one row.
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY
);

-- Logical table registry. PK is (name, layer) since the same logical name
-- may appear in bronze + silver + gold (e.g. "signals").
CREATE TABLE IF NOT EXISTS tables (
    name              TEXT NOT NULL,
    layer             TEXT NOT NULL,
    schema_json       TEXT NOT NULL DEFAULT '{}',
    description       TEXT NOT NULL DEFAULT '',
    created_at        TEXT NOT NULL,
    partition_keys    TEXT NOT NULL DEFAULT 'dt,tenant_id',
    PRIMARY KEY (name, layer)
);

-- Per-batch partition tracking. Uniqueness on (table, layer, partition_key,
-- tenant_id, batch_id) so the same batch_id can't be double-recorded.
CREATE TABLE IF NOT EXISTS partitions (
    table_name     TEXT NOT NULL,
    layer          TEXT NOT NULL,
    partition_key  TEXT NOT NULL,
    tenant_id      TEXT NOT NULL,
    batch_id       TEXT NOT NULL,
    file_path      TEXT NOT NULL,
    manifest_path  TEXT NOT NULL,
    row_count      INTEGER NOT NULL,
    byte_size      INTEGER NOT NULL,
    sha256         TEXT NOT NULL,
    written_at     TEXT NOT NULL,
    PRIMARY KEY (table_name, layer, partition_key, tenant_id, batch_id)
);

CREATE INDEX IF NOT EXISTS idx_partitions_table_layer
    ON partitions(table_name, layer);

CREATE INDEX IF NOT EXISTS idx_partitions_written_at
    ON partitions(written_at);

-- Lineage edges. Recorded by SilverBuilder/GoldAggregator to support audit
-- and "what depends on this" queries.
CREATE TABLE IF NOT EXISTS lineage (
    upstream_table     TEXT NOT NULL,
    upstream_layer     TEXT NOT NULL,
    downstream_table   TEXT NOT NULL,
    downstream_layer   TEXT NOT NULL,
    transform          TEXT NOT NULL,
    PRIMARY KEY (upstream_table, upstream_layer, downstream_table, downstream_layer, transform)
);

-- Seed schema_version. The runtime catalog ensures this row exists.
INSERT OR IGNORE INTO schema_version(version) VALUES (1);
