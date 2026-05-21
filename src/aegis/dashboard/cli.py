"""
aegis dashboard CLI — `aegis dashboard serve`
"""
from __future__ import annotations

import click


@click.group()
def dashboard() -> None:
    """Command Center dashboard (web UI on :8300)."""


@dashboard.command("serve")
@click.option("--host", default="127.0.0.1", show_default=True, help="Bind host.")
@click.option("--port", default=8300, show_default=True, help="Bind port.")
@click.option("--reload", is_flag=True, default=False, help="Enable uvicorn auto-reload (dev).")
def serve(host: str, port: int, reload: bool) -> None:
    """Start the AEGIS Command Center web dashboard.

    Opens at http://localhost:8300 by default.

    Examples:\n
        aegis dashboard serve\n
        aegis dashboard serve --port 9000\n
        aegis dashboard serve --reload
    """
    try:
        import uvicorn
    except ImportError as exc:
        raise click.ClickException("uvicorn is not installed. Run: uv sync --all-extras") from exc

    click.echo(f"AEGIS Command Center → http://{host}:{port}", err=True)
    click.echo("Press Ctrl+C to stop.\n", err=True)

    uvicorn.run(
        "aegis.dashboard.app:app",
        host=host,
        port=port,
        reload=reload,
        log_level="warning",
    )
