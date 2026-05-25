"""Query engines for the data lake."""

from aegis.datalake.query.duckdb_engine import DuckDBQueryEngine, QueryResult

__all__ = ["DuckDBQueryEngine", "QueryResult"]
