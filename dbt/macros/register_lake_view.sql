-- AEGIS Pulse Phase 10 — register Parquet views directly in DuckDB.
--
-- This macro lets dbt see the lake's Parquet files without needing the
-- LakeCatalog. Call from an on-run-start hook in dbt_project.yml when
-- running dbt against a fresh DuckDB database.
--
-- Inputs:
--   layer: "bronze" | "silver" | "gold"
--   table: logical table name (e.g. "signals")
--   storage_uri: e.g. "s3://aegis-datalake" or "/var/lib/aegis-datalake"

{% macro register_lake_view(layer, table, storage_uri) %}
    {% set glob_uri = storage_uri ~ '/' ~ layer ~ '/' ~ table ~ '/**/*.parquet' %}
    {% set view_name = layer ~ '_' ~ table %}
    create or replace view {{ view_name }} as
        select *
        from read_parquet('{{ glob_uri }}',
                          union_by_name=true,
                          hive_partitioning=true);
{% endmacro %}
