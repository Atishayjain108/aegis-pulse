"""Typed error hierarchy for Phase 10.

Every error has a stable machine code (AEGIS-DATALAKE-NNNN) so dashboards
and runbooks can pin diagnostics. Codes are append-only — never recycle.
"""

from __future__ import annotations

from typing import Final

from aegis.datalake.constants import ERR_PREFIX


class DataLakeError(Exception):
    """Base class. All Phase 10 exceptions descend from here.

    Carries a stable error code (`code`) for structured logging and
    a free-form remediation hint (`hint`) for runbook authoring.
    """

    code: str = f"{ERR_PREFIX}-0000"

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint

    def __str__(self) -> str:
        if self.hint:
            return f"[{self.code}] {self.message} — hint: {self.hint}"
        return f"[{self.code}] {self.message}"


# ---------------------------------------------------------------------------
# 0001-0099 : configuration / environment errors
# ---------------------------------------------------------------------------
class ConfigurationError(DataLakeError):
    code = f"{ERR_PREFIX}-0001"


class MissingDependencyError(DataLakeError):
    """Raised when an optional dep (pyarrow, duckdb, boto3) is required but absent."""

    code = f"{ERR_PREFIX}-0002"


class BucketNotFoundError(DataLakeError):
    code = f"{ERR_PREFIX}-0003"


# ---------------------------------------------------------------------------
# 0100-0199 : storage / IO errors
# ---------------------------------------------------------------------------
class StorageError(DataLakeError):
    code = f"{ERR_PREFIX}-0100"


class ParquetWriteError(StorageError):
    code = f"{ERR_PREFIX}-0101"


class ParquetReadError(StorageError):
    code = f"{ERR_PREFIX}-0102"


class ManifestCorruptError(StorageError):
    code = f"{ERR_PREFIX}-0103"


# ---------------------------------------------------------------------------
# 0200-0299 : catalog errors
# ---------------------------------------------------------------------------
class CatalogError(DataLakeError):
    code = f"{ERR_PREFIX}-0200"


class TableNotFoundError(CatalogError):
    code = f"{ERR_PREFIX}-0201"


class TableAlreadyExistsError(CatalogError):
    code = f"{ERR_PREFIX}-0202"


class SchemaMismatchError(CatalogError):
    code = f"{ERR_PREFIX}-0203"


# ---------------------------------------------------------------------------
# 0300-0399 : quality gate / validation
# ---------------------------------------------------------------------------
class QualityError(DataLakeError):
    code = f"{ERR_PREFIX}-0300"


class QualityRejectThresholdExceeded(QualityError):
    code = f"{ERR_PREFIX}-0301"


class ValidationError(QualityError):
    code = f"{ERR_PREFIX}-0302"


# ---------------------------------------------------------------------------
# 0400-0499 : query engine errors
# ---------------------------------------------------------------------------
class QueryError(DataLakeError):
    code = f"{ERR_PREFIX}-0400"


class QueryTimeoutError(QueryError):
    code = f"{ERR_PREFIX}-0401"


class QuerySyntaxError(QueryError):
    code = f"{ERR_PREFIX}-0402"


# ---------------------------------------------------------------------------
# 0500-0599 : orchestration / pipeline
# ---------------------------------------------------------------------------
class PipelineError(DataLakeError):
    code = f"{ERR_PREFIX}-0500"


class UpstreamUnavailableError(PipelineError):
    code = f"{ERR_PREFIX}-0501"


class IdempotencyViolationError(PipelineError):
    code = f"{ERR_PREFIX}-0502"


# Public surface
__all__: Final[list[str]] = [
    "BucketNotFoundError",
    "CatalogError",
    "ConfigurationError",
    "DataLakeError",
    "IdempotencyViolationError",
    "ManifestCorruptError",
    "MissingDependencyError",
    "ParquetReadError",
    "ParquetWriteError",
    "PipelineError",
    "QualityError",
    "QualityRejectThresholdExceeded",
    "QueryError",
    "QuerySyntaxError",
    "QueryTimeoutError",
    "SchemaMismatchError",
    "StorageError",
    "TableAlreadyExistsError",
    "TableNotFoundError",
    "UpstreamUnavailableError",
    "ValidationError",
]
