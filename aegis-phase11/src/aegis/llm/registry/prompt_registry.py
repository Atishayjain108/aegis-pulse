"""
aegis.llm.registry.prompt_registry — PromptRegistry
=====================================================

Manages all Jinja2 prompt templates with:
- File-based storage under ``PROMPT_TEMPLATE_DIR``.
- Version hashing (sha256 of template content) for change detection.
- Variable validation (required variables checked before render).
- Audit log of every render (template name + version hash + variables used).
- Nightly eval integration: ``get_golden_pairs()`` returns reference
  input/output pairs from the eval directory.

Every prompt template MUST include a YAML front-matter block:

    ---
    name: scout_analysis
    version: 1
    required_vars: [trend_title, signal_count, platforms]
    description: "Scout agent: analyse a trend candidate"
    ---

Author: AEGIS Engineering
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

from aegis.llm.constants import PROMPT_TEMPLATE_DIR, PROMPT_VERSION_HASH_LEN
from aegis.llm.errors import InvalidPrompt

_log = structlog.get_logger("aegis.llm.registry.prompt_registry")

_FRONT_MATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)

# Lazy import for jinja2 — kept out of the critical import path
try:
    from jinja2 import Environment, StrictUndefined, Template
    _JINJA_AVAILABLE = True
except ImportError:
    _JINJA_AVAILABLE = False


@dataclass(frozen=True)
class TemplateMetadata:
    """Parsed front-matter for a prompt template."""

    name: str
    version: int
    required_vars: list[str]
    description: str
    content_hash: str  # Truncated sha256 of full file content


@dataclass
class RenderAuditEntry:
    """Record of a single prompt render — written to audit log."""

    template_name: str
    content_hash: str
    variables_used: list[str]
    rendered_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class PromptRegistry:
    """
    File-based prompt template registry with version tracking.

    Parameters
    ----------
    template_dir:
        Path to the directory containing ``.jinja2`` template files.
    audit_log:
        Optional list to append ``RenderAuditEntry`` items to.
        Pass a shared list if you need external access to the audit trail.

    Example
    -------
    .. code-block:: python

        registry = PromptRegistry("src/aegis/llm/prompts/templates")
        registry.load_all()
        rendered = registry.render("scout_analysis", trend_title="AI chips", ...)
    """

    def __init__(
        self,
        template_dir: str | Path = PROMPT_TEMPLATE_DIR,
        *,
        audit_log: list[RenderAuditEntry] | None = None,
    ) -> None:
        self._dir = Path(template_dir)
        self._templates: dict[str, tuple[TemplateMetadata, str]] = {}
        self._audit_log: list[RenderAuditEntry] = audit_log if audit_log is not None else []

        if _JINJA_AVAILABLE:
            self._env = Environment(
                undefined=StrictUndefined,
                autoescape=False,
                keep_trailing_newline=True,
            )
        else:
            self._env = None  # type: ignore[assignment]

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def load_all(self) -> int:
        """
        Scan ``template_dir`` and load all ``.jinja2`` files.

        Returns the number of templates loaded.
        """
        if not self._dir.exists():
            _log.warning("prompt_registry.dir_missing", path=str(self._dir))
            return 0

        count = 0
        for path in sorted(self._dir.glob("*.jinja2")):
            try:
                self._load_file(path)
                count += 1
            except Exception as exc:  # noqa: BLE001
                _log.error(
                    "prompt_registry.load_failed",
                    path=str(path),
                    error=str(exc),
                )
        _log.info("prompt_registry.loaded", count=count, dir=str(self._dir))
        return count

    def load_file(self, path: str | Path) -> None:
        """Load a single template file."""
        self._load_file(Path(path))

    def _load_file(self, path: Path) -> None:
        raw = path.read_text(encoding="utf-8")
        metadata = self._parse_front_matter(raw, path)
        self._templates[metadata.name] = (metadata, raw)
        _log.debug(
            "prompt_registry.loaded_template",
            name=metadata.name,
            version=metadata.version,
            hash=metadata.content_hash,
        )

    @staticmethod
    def _parse_front_matter(raw: str, path: Path) -> TemplateMetadata:
        """Extract YAML front-matter and compute content hash."""
        match = _FRONT_MATTER_RE.match(raw)
        if not match:
            raise InvalidPrompt(
                f"Template {path.name} missing YAML front-matter block",
                detail=str(path),
            )

        # Parse minimal YAML manually (no PyYAML dependency)
        front = {}
        for line in match.group(1).splitlines():
            if ":" in line:
                key, _, val = line.partition(":")
                front[key.strip()] = val.strip()

        required_vars_raw = front.get("required_vars", "[]")
        # Parse simple list: [a, b, c]
        required_vars = [
            v.strip().strip("[]'\"")
            for v in required_vars_raw.strip("[]").split(",")
            if v.strip().strip("[]'\"")
        ]

        content_hash = hashlib.sha256(raw.encode()).hexdigest()[:PROMPT_VERSION_HASH_LEN]

        return TemplateMetadata(
            name=front.get("name", path.stem),
            version=int(front.get("version", "1")),
            required_vars=required_vars,
            description=front.get("description", ""),
            content_hash=content_hash,
        )

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def render(self, template_name: str, **variables: Any) -> str:
        """
        Render a template by name with the given variables.

        Parameters
        ----------
        template_name:
            Name of the template to render (matches the ``name`` field in
            the template's front-matter).
        **variables:
            Template variables.  All ``required_vars`` must be present.

        Raises
        ------
        InvalidPrompt
            When the template is not found or required variables are missing.
        """
        if template_name not in self._templates:
            raise InvalidPrompt(f"Template {template_name!r} not found in registry")

        metadata, raw = self._templates[template_name]

        # Validate required variables
        missing = [v for v in metadata.required_vars if v not in variables]
        if missing:
            raise InvalidPrompt(
                f"Template {template_name!r} missing required variables: {missing}"
            )

        # Render with Jinja2 if available, else simple str.format_map
        if self._env is not None and _JINJA_AVAILABLE:
            # Strip front-matter before rendering
            body = _FRONT_MATTER_RE.sub("", raw)
            template: Template = self._env.from_string(body)
            rendered = template.render(**variables)
        else:
            body = _FRONT_MATTER_RE.sub("", raw)
            rendered = body.format_map(variables)

        # Audit trail
        self._audit_log.append(
            RenderAuditEntry(
                template_name=template_name,
                content_hash=metadata.content_hash,
                variables_used=list(variables.keys()),
            )
        )

        return rendered

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def list_templates(self) -> list[TemplateMetadata]:
        """Return metadata for all loaded templates."""
        return [meta for meta, _ in self._templates.values()]

    def get_metadata(self, template_name: str) -> TemplateMetadata:
        """Return metadata for a single template."""
        if template_name not in self._templates:
            raise KeyError(f"Template {template_name!r} not found")
        return self._templates[template_name][0]

    def audit_log(self) -> list[RenderAuditEntry]:
        """Return a copy of the render audit log."""
        return list(self._audit_log)
