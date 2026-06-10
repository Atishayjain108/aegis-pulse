#!/usr/bin/env python
"""ENV-1: generate ``.env.example`` from the pydantic-settings models.

Introspects every Settings class in the codebase, derives the full
``<PREFIX><FIELD>`` env-var name plus its default, and emits a grouped
``.env.example``. Running this on every settings change keeps the example
from drifting out of sync (the previous hand-maintained file was missing
16+ keys).

Usage:
    uv run python scripts/gen_env_example.py            # write .env.example
    uv run python scripts/gen_env_example.py --check     # CI: fail if stale
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

# (heading, "import.path:ClassName")
_SETTINGS_TARGETS: tuple[tuple[str, str], ...] = (
    ("Core", "aegis.config:Settings"),
    ("MinIO object store", "aegis.config:_MinIOSettings"),
    ("Scrape", "aegis.config:_ScrapeSettings"),
    ("Reddit API (key-gated adapter)", "aegis.config:_RedditSettings"),
    ("YouTube API (key-gated adapter)", "aegis.config:_YouTubeSettings"),
    ("Alerts", "aegis.config:_AlertSettings"),
    ("LLM providers (Phase 11)", "aegis.llm.config:LLMSettings"),
    ("Compliance (Phase 8)", "aegis.compliance.config:ComplianceSettings"),
    ("Evolve (Phase 9)", "aegis.evolve.config:EvolveSettings"),
    ("Execute / Capital (Phase 4/6)", "aegis.execute.config:ExecuteSettings"),
)

_HEADER = (
    "# AEGIS Pulse — environment template (AUTO-GENERATED)\n"
    "# Regenerate with:  uv run python scripts/gen_env_example.py\n"
    "# Do NOT hand-edit; edit the pydantic-settings models instead.\n"
    "# Secrets are shown as empty — fill them in your real .env (never commit it).\n"
)

# Field name substrings that mark a value as secret → emit empty in the example.
_SECRET_HINTS = ("key", "secret", "token", "password", "dsn", "url", "webhook")


def _load(path: str) -> type[Any] | None:
    module_path, cls_name = path.split(":")
    try:
        module = __import__(module_path, fromlist=[cls_name])
        return getattr(module, cls_name)
    except Exception as exc:  # optional dep missing → skip gracefully
        print(f"# (skipped {path}: {exc})", file=sys.stderr)
        return None


def _render() -> str:
    lines: list[str] = [_HEADER]
    seen: set[str] = set()

    for heading, target in _SETTINGS_TARGETS:
        cls = _load(target)
        if cls is None:
            continue
        prefix = (cls.model_config.get("env_prefix") or "").upper()
        block: list[str] = []
        for name, field in cls.model_fields.items():
            env_name = f"{prefix}{name.upper()}"
            if env_name in seen:
                continue
            seen.add(env_name)
            is_secret = any(h in name.lower() for h in _SECRET_HINTS)
            default = field.default
            if is_secret or default is None or repr(default).startswith("PydanticUndefined"):
                value = ""
            elif isinstance(default, bool):
                value = "true" if default else "false"
            else:
                value = str(default)
            block.append(f"{env_name}={value}")
        if block:
            lines.append(f"\n# --- {heading} ---")
            lines.extend(sorted(block))

    return "\n".join(lines) + "\n"


def main() -> int:
    out_path = Path(__file__).resolve().parents[1] / ".env.example"
    rendered = _render()
    if "--check" in sys.argv:
        current = out_path.read_text() if out_path.exists() else ""
        if current != rendered:
            print("ERROR: .env.example is stale — run gen_env_example.py", file=sys.stderr)
            return 1
        print(".env.example is in sync.")
        return 0
    out_path.write_text(rendered)
    print(f"Wrote {out_path} ({rendered.count(chr(10))} lines).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
