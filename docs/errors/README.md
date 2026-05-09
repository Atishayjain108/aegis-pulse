# AEGIS Pulse — Error Code Reference

Every error message AEGIS Pulse prints starts with a machine code in
the form **`AEGIS-<DOMAIN>-<NNNN>`**. The domain identifies which
subsystem owns the error; the four-digit number is unique within that
domain. Each code has a dedicated file in this directory documenting:

* What it means
* Why it usually happens
* Step-by-step remediation
* What information to gather if remediation fails

## Domains

| Domain | Owner | Range used |
| --- | --- | --- |
| `BOOT` | Bootstrap / setup scripts (`bootstrap/`) | 0001–0099 |
| `CLI` | The `aegis` command-line tool (`src/aegis/cli/`) | 0001–0099 |
| `DB` | Persistence layer (`src/aegis/db/`) | 0001–0099 |
| `CACHE` | Cache + priority queue (`src/aegis/cache/`) | 0001–0099 |
| `SCRAPE` | Source adapters (`src/aegis/scrape/`) | 0001–0099 |
| `AGENT` | Multi-agent layer *(Phase 2+)* | 0001–0099 |
| `MODEL` | Predictive engine *(Phase 3+)* | 0001–0099 |
| `EXEC` | Execution engine *(Phase 6+)* | 0001–0099 |

## Currently documented codes

### CLI
- [`AEGIS-CLI-0001.md`](AEGIS-CLI-0001.md) — wrapped subprocess returned non-zero
- [`AEGIS-CLI-0002.md`](AEGIS-CLI-0002.md) — Docker compose plugin not found
- [`AEGIS-CLI-0003.md`](AEGIS-CLI-0003.md) — `docker compose ps` failed
- [`AEGIS-CLI-0004.md`](AEGIS-CLI-0004.md) — `bootstrap/aegis-doctor` not found
- [`AEGIS-CLI-0005.md`](AEGIS-CLI-0005.md) — required secrets missing

### Boot
- [`AEGIS-BOOT-0001.md`](AEGIS-BOOT-0001.md) — WSL2 not enabled
- [`AEGIS-BOOT-0002.md`](AEGIS-BOOT-0002.md) — Docker daemon unreachable from WSL
- [`AEGIS-BOOT-0003.md`](AEGIS-BOOT-0003.md) — Python 3.12 not the active interpreter
- [`AEGIS-BOOT-0004.md`](AEGIS-BOOT-0004.md) — required port already in use
- [`AEGIS-BOOT-0005.md`](AEGIS-BOOT-0005.md) — clock drift detected

### Scrape
- [`AEGIS-SCRAPE-0001.md`](AEGIS-SCRAPE-0001.md) — adapter timed out
- [`AEGIS-SCRAPE-0002.md`](AEGIS-SCRAPE-0002.md) — Cloudflare challenge could not be solved
- [`AEGIS-SCRAPE-0003.md`](AEGIS-SCRAPE-0003.md) — proxy pool exhausted
- [`AEGIS-SCRAPE-0004.md`](AEGIS-SCRAPE-0004.md) — RED-ToS adapter requested without opt-in
- [`AEGIS-SCRAPE-0005.md`](AEGIS-SCRAPE-0005.md) — YouTube API quota exceeded

### DB
- [`AEGIS-DB-0001.md`](AEGIS-DB-0001.md) — pool exhausted
- [`AEGIS-DB-0002.md`](AEGIS-DB-0002.md) — migration failed

## Convention for new codes

When adding a new error code:

1. Pick the next free integer in the appropriate domain.
2. Create `docs/errors/AEGIS-<DOMAIN>-<NNNN>.md` with the structure of
   any existing file.
3. Reference the code from the raising site with a string of the form
   `f"AEGIS-{DOMAIN}-{NNNN:04d}: <human-readable message>"`.
4. Add the code to the table above.

The four-digit width (`{:04d}`) is mandatory. It keeps codes
visually aligned in logs and grep-friendly in incident postmortems.
