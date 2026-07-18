"""
aegis dashboard CLI — `aegis dashboard serve`
"""
from __future__ import annotations

import socket

import click


def _port_in_use(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
            return False
        except OSError:
            return True


def _kill_port(port: int) -> bool:
    """Kill whatever process is listening on *port*. Returns True if killed."""
    import subprocess
    try:
        result = subprocess.run(
            ["fuser", "-k", "-n", "tcp", str(port)],
            capture_output=True, timeout=5, check=False,
        )
        return result.returncode == 0
    except FileNotFoundError:
        pass
    # fuser not available — try ss + kill
    try:
        out = subprocess.check_output(
            ["ss", "-tlnp", f"sport = :{port}"], text=True, timeout=5
        )
        import re
        for pid in re.findall(r'pid=(\d+)', out):
            if not pid.isdigit():
                continue
            subprocess.run(["kill", pid], capture_output=True, check=False)
        return True
    except Exception:
        return False


@click.group()
def dashboard() -> None:
    """Command Center dashboard (web UI on :8300)."""


@dashboard.command("serve")
@click.option("--host", default="127.0.0.1", show_default=True, help="Bind host.")
@click.option("--port", default=8300, show_default=True, help="Bind port.")
@click.option("--reload", is_flag=True, default=False, help="Enable uvicorn auto-reload (dev).")
@click.option("--force", is_flag=True, default=False, help="Kill any process already on --port then start.")
def serve(host: str, port: int, reload: bool, force: bool) -> None:
    """Start the AEGIS Command Center web dashboard.

    Opens at http://localhost:8300 by default.
    If the port is already in use, pass --force to kill the old process.

    Examples:\n
        aegis dashboard serve\n
        aegis dashboard serve --port 9000\n
        aegis dashboard serve --force\n
        aegis dashboard serve --reload
    """
    try:
        import uvicorn
    except ImportError as exc:
        raise click.ClickException("uvicorn is not installed. Run: uv sync --all-extras") from exc

    if _port_in_use(host, port):
        if force:
            click.echo(f"Port {port} in use — killing existing process…", err=True)
            if _kill_port(port):
                import time
                time.sleep(1)
                if _port_in_use(host, port):
                    raise click.ClickException(
                        f"Port {port} still in use after kill attempt. "
                        "Try: kill $(fuser -n tcp {port})"
                    )
                click.echo(f"Port {port} freed.", err=True)
            else:
                raise click.ClickException(
                    f"Could not free port {port}. Run: fuser -k -n tcp {port}"
                )
        else:
            raise click.ClickException(
                f"Port {port} is already in use.\n"
                f"  • Run with --force to kill the existing process automatically.\n"
                f"  • Or free it manually: fuser -k -n tcp {port}\n"
                f"  • Or pick another port: aegis dashboard serve --port 8301"
            )

    click.echo(f"AEGIS Command Center → http://{host}:{port}", err=True)
    click.echo("Press Ctrl+C to stop.\n", err=True)

    uvicorn.run(
        "aegis.dashboard.app:app",
        host=host,
        port=port,
        reload=reload,
        log_level="warning",
    )
