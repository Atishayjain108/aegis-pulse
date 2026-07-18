"""FastAPI router tests."""

from __future__ import annotations

import pytest

from aegis.datalake.facade import DataLake
from aegis.datalake.settings import DataLakeSettings

fastapi = pytest.importorskip("fastapi")
httpx = pytest.importorskip("httpx")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from aegis.datalake.api.router import build_router  # noqa: E402


@pytest.fixture()
def app_with_router(tmp_settings: DataLakeSettings) -> FastAPI:
    """Build a FastAPI app that owns a long-lived DataLake.

    Using ``lake=...`` (not ``settings=...``) so successive requests share a
    single catalog handle. The lake is exposed on ``app.state.lake`` so tests
    can seed data before issuing requests.
    """
    from collections.abc import AsyncIterator
    from contextlib import asynccontextmanager

    lake = DataLake.open(tmp_settings)

    @asynccontextmanager
    async def _lifespan(application: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            lake.close()

    app = FastAPI(lifespan=_lifespan)
    app.state.lake = lake
    app.include_router(build_router(lake=lake))
    return app


@pytest.fixture()
def client(app_with_router: FastAPI) -> TestClient:
    return TestClient(app_with_router)


class TestHealthEndpoint:
    def test_health_returns_ok(self, client: TestClient) -> None:
        r = client.get("/datalake/health")
        assert r.status_code == 200
        body = r.json()
        assert body["backend_ok"]
        assert body["catalog_ok"]


class TestTablesEndpoint:
    def test_empty(self, client: TestClient) -> None:
        r = client.get("/datalake/tables")
        assert r.status_code == 200
        assert r.json() == []

    def test_invalid_layer_400(self, client: TestClient) -> None:
        r = client.get("/datalake/tables?layer=platinum")
        assert r.status_code == 400

    def test_tables_with_data_serializes_created_at(
        self, client: TestClient, app_with_router: FastAPI, sample_signal_rows: list[dict]
    ) -> None:
        """Regression: catalog returns created_at as datetime; the response
        model must coerce it to an ISO string instead of 500-ing."""
        lake = app_with_router.state.lake
        lake.bronze.write(
            table="signals", rows=sample_signal_rows,
            source="fixture", partition_date="2026-05-20",
        )
        r = client.get("/datalake/tables")
        assert r.status_code == 200, r.text
        body = r.json()
        assert len(body) >= 1
        # created_at must be a string in the JSON response
        assert isinstance(body[0]["created_at"], str)
        assert body[0]["name"] == "signals"


class TestPartitionsEndpointWithData:
    def test_partitions_with_data(
        self, client: TestClient, app_with_router: FastAPI, sample_signal_rows: list[dict]
    ) -> None:
        """Regression: partitions endpoint must serialize written_at + return rows."""
        lake = app_with_router.state.lake
        lake.bronze.write(
            table="signals", rows=sample_signal_rows,
            source="fixture", partition_date="2026-05-20",
        )
        r = client.get("/datalake/tables/signals/partitions?layer=bronze")
        assert r.status_code == 200, r.text
        body = r.json()
        assert len(body) >= 1
        assert isinstance(body[0]["written_at"], str)
        assert body[0]["row_count"] == len(sample_signal_rows)


class TestPartitionsEndpoint:
    def test_layer_required(self, client: TestClient) -> None:
        r = client.get("/datalake/tables/signals/partitions")
        # FastAPI validation: missing required query param → 422
        assert r.status_code == 422


class TestQueryEndpoint:
    def test_simple_query(
        self, client: TestClient, app_with_router: FastAPI, sample_signal_rows: list[dict]
    ) -> None:
        # Find the lake from the app
        from aegis.datalake.api import router as router_mod  # noqa: F401
        # Write some bronze data through the lake the router shares.
        # We grab the lake reference by reconstructing from settings, since
        # the router's lake is shared via closure — but the simpler thing
        # is to query a literal.
        r = client.post(
            "/datalake/query",
            json={"sql": "SELECT 1 AS one", "limit": 10},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["rows"] == [{"one": 1}]
        assert "one" in body["columns"]

    def test_write_statement_400(self, client: TestClient) -> None:
        r = client.post(
            "/datalake/query",
            json={"sql": "CREATE TABLE x (a INT)", "limit": 10},
        )
        assert r.status_code == 400

    def test_oversized_sql_422(self, client: TestClient) -> None:
        long_sql = "SELECT 1 -- " + ("x" * 10_000)
        r = client.post(
            "/datalake/query",
            json={"sql": long_sql, "limit": 10},
        )
        # FastAPI/Pydantic max_length on sql → 422
        assert r.status_code == 422


class TestLineageEndpoint:
    def test_empty_lineage(self, client: TestClient) -> None:
        r = client.get("/datalake/lineage")
        assert r.status_code == 200
        assert r.json() == []
