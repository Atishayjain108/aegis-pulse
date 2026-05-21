# Phase 4 — Docker

Two images plus a local development stack.

## Images

| Image | Dockerfile | What it runs | Default ports |
|---|---|---|---|
| `aegis/execute-api:0.4.0`   | `Dockerfile.api`   | Uvicorn → `aegis.execute.api.app:build_app` | 8200 |
| `aegis/execute-drain:0.4.0` | `Dockerfile.drain` | `aegis-execute drain --tenant <uuid>`        | —    |

Both images:

* are based on `python:3.12-slim-bookworm`,
* use `tini` as PID 1 so SIGTERM is handled cleanly,
* run as non-root (`uid 1000`, group `aegis`),
* share `entrypoint.sh` for dependency-wait + command dispatch,
* ship a `HEALTHCHECK` (HTTP `/healthz` for the API; `aegis-execute
  version` for the drainer).

The drainer image deliberately omits the `[serve]` extra — no FastAPI /
uvicorn, smaller footprint, fewer attack-surface bytes.

## Build

```bash
# From the repo root
docker build -f docker/Dockerfile.api   -t aegis/execute-api:0.4.0   .
docker build -f docker/Dockerfile.drain -t aegis/execute-drain:0.4.0 .
```

`.dockerignore` keeps tests, caches, and the distribution zip out of the
build context — image sizes stay ~120 MB (api) / ~110 MB (drain) on a
clean build.

## Local dev stack

```bash
docker compose -f docker/docker-compose.execute.yml up -d
```

This boots:

| Service | URL / port |
|---|---|
| API + dashboard | http://localhost:8200/dashboard/ |
| Prometheus | http://localhost:9190 |
| Grafana | http://localhost:3100 (admin / `aegis_dev_admin_pw`) |
| Postgres | `localhost:5544` (`aegis` / `aegis_dev_pw`) |
| Redis | `localhost:6480` |

The Grafana provisioning under `config/grafana/` auto-wires Prometheus as
a data source and loads the **AEGIS Pulse — Execute** dashboard.

The Postgres container runs `db/migrations/*.sql` on first start, so the
`alerts` / `alert_outbox` / etc. tables are ready immediately.

Phase 4 ports are offset by **+100** from the Phase 1 dev stack (which
uses 5433 / 6380 / 9091 / 3001) so both can run side-by-side on a single
laptop.

## Environment variables

The compose file sets safe dev defaults. In production the things you
**must** override are:

| Var | Default in compose | Production action |
|---|---|---|
| `AEGIS_EXECUTE_API_BEARER_TOKEN` | empty (open) | set to a 32+ byte secret |
| `AEGIS_EXECUTE_HMAC_KEY` | `dev-hmac-key-not-for-prod` | rotate to a real secret |
| `AEGIS_EXECUTE_TENANT_ID` | all-zeros UUID | per-tenant drainer replica |
| `POSTGRES_PASSWORD` | `aegis_dev_pw` | replace |
| `GF_SECURITY_ADMIN_PASSWORD` | `aegis_dev_admin_pw` | replace |

See `docs/phase4/OPERATIONS.md` for the full operator runbook.
