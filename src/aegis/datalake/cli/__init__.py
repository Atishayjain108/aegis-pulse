"""Phase 10 command-line interface.

Mounted as the ``aegis datalake`` subcommand group when the main Phase 1+ CLI
is present. When run directly (``python -m aegis.datalake.cli``), it acts as a
standalone CLI for environments without the rest of AEGIS installed.
"""

from .main import cli

__all__ = ["cli"]
