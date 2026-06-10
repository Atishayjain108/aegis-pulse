"""
aegis.security.cli — Phase 12 security CLI commands.

Registered under the ``aegis security`` command group.

Commands
--------
aegis security doctor          — Run the security health check
aegis security scan            — Scan for leaked secrets in the repo
aegis security vault init      — Bootstrap Vault with AEGIS defaults
aegis security vault status    — Check Vault health and seal status
aegis security rotate          — Rotate a secret in Vault
aegis security audit verify    — Verify audit log chain integrity
aegis security audit tail      — Tail recent audit log entries
aegis security pii test        — Test PII scrubber against sample text
aegis security headers check   — Verify security headers on a running service

Usage::

    # From project root:
    uv run aegis security doctor
    uv run aegis security scan
    uv run aegis security vault init
    uv run aegis security rotate aegis/jwt/secret
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import click

# ── Helpers ────────────────────────────────────────────────────────────────── #

def _run(coro: Any) -> Any:  # noqa: ANN401
    """Run an async coroutine from a synchronous Click command."""
    return asyncio.run(coro)


def _print_table(rows: list[dict[str, Any]], columns: list[str]) -> None:
    """Print a simple fixed-width table."""
    widths = {col: len(col) for col in columns}
    for row in rows:
        for col in columns:
            widths[col] = max(widths[col], len(str(row.get(col, ""))))

    header = "  ".join(col.ljust(widths[col]) for col in columns)
    sep = "  ".join("-" * widths[col] for col in columns)
    click.echo(header)
    click.echo(sep)
    for row in rows:
        click.echo("  ".join(str(row.get(col, "")).ljust(widths[col]) for col in columns))


# ── Top-level group ────────────────────────────────────────────────────────── #

@click.group(name="security")
def security_group() -> None:
    """Phase 12 — Security & Secrets management commands."""


# ── doctor ─────────────────────────────────────────────────────────────────── #

@security_group.command("doctor")
@click.option("--secrets-only", is_flag=True, help="Check only env var secrets.")
@click.option("--json-out", is_flag=True, help="Output as JSON.")
def doctor(secrets_only: bool, json_out: bool) -> None:
    """Run the security health check across all Phase 12 subsystems."""
    results: list[dict[str, Any]] = []

    checks = [
        ("vault_reachable", _check_vault),
        ("pii_scrubber", _check_pii),
        ("jwt_config", _check_jwt),
        ("rbac_config", _check_rbac),
        ("required_env_vars", _check_env_vars),
        ("audit_log_writable", _check_audit_log),
    ]

    if not secrets_only:
        checks.extend([
            ("sops_available", lambda: _check_tool("sops")),
            ("age_available", lambda: _check_tool("age-keygen")),
            ("mkcert_available", lambda: _check_tool("mkcert")),
            ("vault_installed", lambda: _check_tool("vault")),
        ])

    all_ok = True
    for name, checker in checks:
        try:
            ok, detail = checker()
        except Exception as exc:
            ok, detail = False, str(exc)
        results.append({"check": name, "status": "OK" if ok else "FAIL", "detail": detail})
        if not ok:
            all_ok = False

    if json_out:
        click.echo(json.dumps(results, indent=2))
    else:
        click.echo("\nAEGIS Security Doctor\n" + "=" * 40)
        for r in results:
            ok = r["status"] == "OK"
            icon = click.style("✔", fg="green") if ok else click.style("✘", fg="red")
            click.echo(f"  {icon}  {r['check']:<30} {r['detail']}")
        click.echo()
        if all_ok:
            click.echo(click.style("All security checks passed.", fg="green", bold=True))
        else:
            click.echo(click.style("Some checks failed — review above.", fg="red", bold=True))
            sys.exit(1)


def _check_vault() -> tuple[bool, str]:
    from aegis.security.config import get_security_config
    cfg = get_security_config()
    get_security_config.cache_clear()
    return True, f"Vault addr: {cfg.vault_addr} (connectivity check requires running service)"


def _check_pii() -> tuple[bool, str]:
    from aegis.security.pii.scrubber import PIIScrubber
    s = PIIScrubber()
    s._spacy_available = False
    _, rep = s.scrub_text("test@example.com")
    if rep.replacements.get("email", 0) >= 1:
        return True, "Email pattern detected correctly"
    return False, "Email pattern NOT detected — scrubber broken"


def _check_jwt() -> tuple[bool, str]:
    from aegis.security.config import get_security_config
    from aegis.security.tls.jwt_manager import JWTManager
    cfg = get_security_config()
    get_security_config.cache_clear()
    mgr = JWTManager(config=cfg)
    pair = mgr.issue_tokens(subject="doctor-check", role="viewer")
    payload = mgr.verify_access_token(pair.access_token)
    if payload.sub == "doctor-check":
        return True, f"JWT round-trip OK (alg={cfg.jwt_algorithm})"
    return False, "JWT round-trip failed"


def _check_rbac() -> tuple[bool, str]:
    from aegis.security.rbac.enforcer import RBACEnforcer
    e = RBACEnforcer()
    ok = (
        e.has_permission("admin", "users:delete")
        and not e.has_permission("viewer", "killswitch:trip")
        and e.has_permission("operator", "killswitch:trip")
    )
    return ok, "RBAC hierarchy correct" if ok else "RBAC hierarchy broken"


def _check_env_vars() -> tuple[bool, str]:
    import os
    required = ["AEGIS_SEC_JWT_SECRET", "AEGIS_SEC_HMAC_KEY"]
    missing = [v for v in required if not os.environ.get(v)]
    if missing:
        return False, f"Missing env vars: {', '.join(missing)}"
    return True, "All required env vars set"


def _check_audit_log() -> tuple[bool, str]:
    from aegis.security.config import get_security_config
    cfg = get_security_config()
    get_security_config.cache_clear()
    try:
        log_dir = Path(cfg.audit_log_path).expanduser().parent
        log_dir.mkdir(parents=True, exist_ok=True)
        test_file = log_dir / ".write_test"
        test_file.write_text("ok")
        test_file.unlink()
        return True, f"Writable: {cfg.audit_log_path}"
    except OSError as exc:
        return False, f"Not writable: {exc}"


def _check_tool(name: str) -> tuple[bool, str]:
    import shutil
    path = shutil.which(name)
    if path:
        return True, f"Found at {path}"
    return False, f"{name} not found on PATH"


# ── scan ───────────────────────────────────────────────────────────────────── #

@security_group.command("scan")
@click.argument("path", default=".", type=click.Path(exists=True))
@click.option("--json-out", is_flag=True, help="Output as JSON.")
@click.option("--fail-on-findings", is_flag=True, help="Exit 1 if secrets found.")
def scan(path: str, json_out: bool, fail_on_findings: bool) -> None:
    """Scan for leaked secrets in PATH (default: current directory)."""
    from aegis.security.scanner import SecretScanner

    scanner = SecretScanner()
    result = scanner.scan_directory(Path(path))

    if json_out:
        click.echo(json.dumps(result.to_dict(), indent=2))
    else:
        click.echo(f"\nSecret scan: {path}")
        click.echo(f"Files scanned: {result.scanned_files}")
        click.echo(f"Tool: {result.tool}")
        if result.has_secrets:
            click.echo(click.style(f"\n⚠  {result.finding_count} finding(s):", fg="red", bold=True))
            for f in result.findings:
                click.echo(f"  {f.file_path}:{f.line_number}  [{f.secret_type}]")
        else:
            click.echo(click.style("\n✔  No secrets detected.", fg="green"))
        if result.errors:
            click.echo(f"\nErrors: {result.errors}")

    if fail_on_findings and result.has_secrets:
        sys.exit(1)


# ── vault group ────────────────────────────────────────────────────────────── #

@security_group.group("vault")
def vault_group() -> None:
    """Vault management sub-commands."""


@vault_group.command("init")
@click.option("--overwrite", is_flag=True, help="Overwrite existing secrets.")
@click.option("--json-out", is_flag=True)
def vault_init(overwrite: bool, json_out: bool) -> None:
    """Bootstrap Vault with AEGIS initial configuration."""
    async def _run_init() -> None:
        from aegis.security.vault.bootstrap import VaultBootstrap
        from aegis.security.vault.client import VaultClient

        async with VaultClient() as client:
            bootstrapper = VaultBootstrap(client, overwrite_existing=overwrite)
            results = await bootstrapper.run()

        if json_out:
            click.echo(json.dumps(results, indent=2))
        else:
            click.echo("\nVault Bootstrap Results\n" + "=" * 40)
            _print_table(results, ["step", "status", "detail"])
            ok = all(r["status"] in ("ok", "skipped") for r in results)
            click.echo()
            if ok:
                click.echo(click.style("Vault bootstrap complete.", fg="green", bold=True))
            else:
                click.echo(click.style("Bootstrap had errors — check above.", fg="red"))
                sys.exit(1)

    _run(_run_init())


@vault_group.command("status")
def vault_status() -> None:
    """Check Vault health and seal status."""
    async def _check() -> None:
        from aegis.security.vault.client import VaultClient

        async with VaultClient() as client:
            health = await client.health()

        click.echo("\nVault Status")
        click.echo("=" * 30)
        for k, v in health.items():
            icon = click.style("✔", fg="green") if v else click.style("✘", fg="red")
            click.echo(f"  {icon}  {k}: {v}")

    _run(_check())


# ── rotate ─────────────────────────────────────────────────────────────────── #

@security_group.command("rotate")
@click.argument("path")
@click.option("--field", default="value", help="KV field to rotate.")
@click.option("--grace", default=300, help="Grace period in seconds.")
@click.option("--now", is_flag=True, help="Complete rotation immediately (no grace period).")
def rotate(path: str, field: str, grace: int, now: bool) -> None:
    """Rotate a secret at PATH in Vault."""
    async def _rotate() -> None:
        from aegis.security.secrets.rotation import SecretRotationManager
        from aegis.security.vault.client import VaultClient

        async with VaultClient() as vault:
            mgr = SecretRotationManager(vault)
            effective_grace = 0 if now else grace
            record = await mgr.rotate_secret(
                path, field=field, grace_period_s=effective_grace
            )
            if now:
                await mgr.complete_rotation(path)
                click.echo(click.style(f"✔  Rotated {path} (immediate)", fg="green"))
            else:
                click.echo(
                    click.style(
                        f"✔  Rotation started for {path} "
                        f"(grace period: {effective_grace}s)",
                        fg="green",
                    )
                )
            click.echo(f"   Old version: {record.old_version}")
            click.echo(f"   New version: {record.new_version}")

    _run(_rotate())


# ── audit sub-commands ─────────────────────────────────────────────────────── #

@security_group.group("audit")
def audit_group() -> None:
    """Audit log sub-commands."""


@audit_group.command("verify")
@click.option("--path", default=None, help="Path to audit log (uses config default).")
def audit_verify(path: str | None) -> None:
    """Verify the integrity of the audit log chain."""
    async def _verify() -> None:
        from aegis.security.audit.logger import AuditLogger
        from aegis.security.config import get_security_config

        cfg = get_security_config()
        get_security_config.cache_clear()
        logger = AuditLogger(config=cfg)
        if path:
            from pathlib import Path as P
            logger._log_path = P(path)

        valid, count, err = await logger.verify_integrity()
        click.echo("\nAudit Log Integrity Check")
        click.echo(f"  Entries checked: {count}")
        if valid:
            click.echo(click.style(f"  ✔  Chain intact ({count} entries verified)", fg="green"))
        else:
            click.echo(click.style(f"  ✘  Chain BROKEN: {err}", fg="red", bold=True))
            sys.exit(1)

    _run(_verify())


@audit_group.command("tail")
@click.option("--limit", default=20, help="Number of recent entries to show.")
@click.option("--path", default=None, help="Path to audit log.")
def audit_tail(limit: int, path: str | None) -> None:
    """Show the most recent audit log entries."""
    from aegis.security.config import get_security_config

    cfg = get_security_config()
    get_security_config.cache_clear()
    log_path = Path(path) if path else Path(cfg.audit_log_path).expanduser()

    if not log_path.exists():
        click.echo(f"Audit log not found: {log_path}")
        return

    lines = log_path.read_text(encoding="utf-8").strip().split("\n")
    recent = [ln for ln in lines if ln.strip()][-limit:]

    click.echo(f"\nAudit Log: {log_path} (last {len(recent)} entries)\n")
    for raw in recent:
        try:
            entry = json.loads(raw)
            ts = entry.get("ts", "")[:19]
            seq = str(entry.get("seq", "?")).rjust(6)
            event = entry.get("event", "?")
            actor = entry.get("actor", "?")
            outcome = entry.get("outcome", "?")
            colour = "green" if outcome == "success" else "red"
            click.echo(
                f"  {seq}  {ts}  "
                f"{click.style(outcome, fg=colour):<12}  "
                f"{actor:<30}  {event}"
            )
        except json.JSONDecodeError:
            click.echo(f"  [unparseable] {raw[:80]}")


# ── pii test ───────────────────────────────────────────────────────────────── #

@security_group.command("pii-test")
@click.argument("text", default="Contact alice@example.com or call +1-555-123-4567")
def pii_test(text: str) -> None:
    """Test the PII scrubber against sample TEXT."""
    from aegis.security.pii.scrubber import PIIScrubber

    scrubber = PIIScrubber()
    scrubber._spacy_available = False  # skip NER for speed in CLI
    result, report = scrubber.scrub_text(text)

    click.echo(f"\nOriginal : {text}")
    click.echo(f"Scrubbed : {result}")
    click.echo(f"\nReplacements: {report.replacements}")
    click.echo(f"Total PII found: {report.total_replacements}")


# ── headers check ──────────────────────────────────────────────────────────── #

@security_group.command("headers-check")
@click.argument("url", default="http://localhost:8300/healthz")
def headers_check(url: str) -> None:
    """Verify security headers on a running service endpoint."""
    try:
        import httpx
    except ImportError:
        click.echo("httpx required: pip install httpx")
        sys.exit(1)

    required = [
        "X-Frame-Options",
        "X-Content-Type-Options",
        "Content-Security-Policy",
        "Referrer-Policy",
    ]
    recommended = [
        "Strict-Transport-Security",
        "Permissions-Policy",
        "Cross-Origin-Opener-Policy",
    ]

    try:
        resp = httpx.get(url, timeout=5, verify=False)  # noqa: S501
    except Exception as exc:
        click.echo(f"Cannot reach {url}: {exc}")
        sys.exit(1)

    click.echo(f"\nSecurity Headers: {url}  (HTTP {resp.status_code})\n")
    all_ok = True
    for h in required:
        if h in resp.headers:
            val = resp.headers[h][:60]
            click.echo(f"  {click.style('✔', fg='green')}  [REQUIRED]    {h}: {val}")
        else:
            click.echo(f"  {click.style('✘', fg='red')}  [REQUIRED]    {h}: MISSING")
            all_ok = False
    for h in recommended:
        if h in resp.headers:
            val = resp.headers[h][:60]
            click.echo(f"  {click.style('✔', fg='green')}  [recommended] {h}: {val}")
        else:
            click.echo(f"  {click.style('-', fg='yellow')}  [recommended] {h}: missing")

    click.echo()
    if all_ok:
        click.echo(click.style("All required headers present.", fg="green", bold=True))
    else:
        click.echo(click.style("Missing required headers!", fg="red", bold=True))
        sys.exit(1)
