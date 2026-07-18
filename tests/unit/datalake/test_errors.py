"""Error hierarchy + AEGIS-DATALAKE-NNNN code stability tests."""

from __future__ import annotations

import pytest

from aegis.datalake import errors as E


class TestErrorHierarchy:
    def test_all_errors_descend_from_base(self) -> None:
        for cls in [
            E.ConfigurationError,
            E.MissingDependencyError,
            E.BucketNotFoundError,
            E.StorageError,
            E.ParquetWriteError,
            E.ParquetReadError,
            E.ManifestCorruptError,
            E.CatalogError,
            E.TableNotFoundError,
            E.TableAlreadyExistsError,
            E.SchemaMismatchError,
            E.QualityError,
            E.QualityRejectThresholdExceeded,
            E.ValidationError,
            E.QueryError,
            E.QueryTimeoutError,
            E.QuerySyntaxError,
            E.PipelineError,
            E.UpstreamUnavailableError,
            E.IdempotencyViolationError,
        ]:
            assert issubclass(cls, E.DataLakeError)

    def test_base_is_exception(self) -> None:
        assert issubclass(E.DataLakeError, Exception)


class TestStableErrorCodes:
    """If an error code changes, downstream runbooks break — pin them."""

    @pytest.mark.parametrize(
        ("cls", "expected_prefix"),
        [
            (E.ConfigurationError, "AEGIS-DATALAKE-0001"),
            (E.MissingDependencyError, "AEGIS-DATALAKE-0002"),
            (E.StorageError, "AEGIS-DATALAKE-0100"),
            (E.ParquetWriteError, "AEGIS-DATALAKE-0101"),
            (E.CatalogError, "AEGIS-DATALAKE-0200"),
            (E.QualityError, "AEGIS-DATALAKE-0300"),
            (E.QueryError, "AEGIS-DATALAKE-0400"),
        ],
    )
    def test_error_codes_stable(self, cls: type[E.DataLakeError], expected_prefix: str) -> None:
        err = cls("dummy")
        msg = str(err)
        assert expected_prefix in msg, f"{cls.__name__} should include {expected_prefix}, got {msg!r}"


class TestErrorMessages:
    def test_error_includes_hint(self) -> None:
        err = E.ConfigurationError("bad value", hint="set AEGIS_DATALAKE_X")
        rendered = str(err)
        assert "bad value" in rendered
        assert "set AEGIS_DATALAKE_X" in rendered

    def test_table_not_found_carries_name(self) -> None:
        err = E.TableNotFoundError("unknown table signals in layer silver")
        rendered = str(err)
        assert "signals" in rendered
        assert "silver" in rendered

    def test_quality_threshold_exceeded_carries_numbers(self) -> None:
        err = E.QualityRejectThresholdExceeded(
            "12% rejected exceeds 5% threshold"
        )
        rendered = str(err)
        assert "12" in rendered
        assert "5" in rendered
