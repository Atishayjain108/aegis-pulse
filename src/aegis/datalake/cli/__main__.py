"""Allow ``python -m aegis.datalake.cli`` invocation."""

from .main import cli

if __name__ == "__main__":  # pragma: no cover
    cli()
