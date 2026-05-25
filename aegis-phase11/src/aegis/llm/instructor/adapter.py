"""
aegis.llm.instructor.adapter — InstructorAdapter
=================================================

Wraps an ``LLMGateway`` call to extract structured, pydantic-validated
output from the raw text response.

Strategy:
  1. Render the prompt with an appended "JSON schema instructions" block
     derived from the target pydantic model.
  2. Call the gateway.
  3. Parse the JSON from the response (handles markdown fences).
  4. Validate against the pydantic schema.
  5. On parse/validation failure, retry up to ``max_retries`` with an
     error-correction prompt.

This mirrors the ``instructor`` OSS library's approach but:
- Has zero additional dependencies.
- Integrates directly with our ``LLMGateway`` instead of patching clients.
- Produces fully typed responses.

Author: AEGIS Engineering
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any, TypeVar

import structlog
from pydantic import BaseModel, ValidationError

from aegis.llm.constants import ERR_INSTRUCTOR_PARSE
from aegis.llm.errors import InstructorParseError

if TYPE_CHECKING:
    from aegis.llm.gateway.gateway import LLMGateway
    from aegis.llm.gateway.response import LLMResponse

_log = structlog.get_logger("aegis.llm.instructor")

T = TypeVar("T", bound=BaseModel)

# Regex to strip markdown code fences around JSON
_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)\s*```", re.IGNORECASE)

# JSON object/array extractor (greedy — takes the largest JSON block)
_JSON_EXTRACT_RE = re.compile(r"(\{[\s\S]*\}|\[[\s\S]*\])")

_MAX_RETRIES: int = 2

_SCHEMA_INSTRUCTION_TEMPLATE = """
---
IMPORTANT: Respond ONLY with a valid JSON object matching this schema.
Do NOT include any explanation, markdown, or code fences.
Schema:
{schema}
---
"""

_ERROR_CORRECTION_TEMPLATE = """
Your previous response could not be parsed as valid JSON matching the schema.
Error: {error}

Previous response:
{previous}

Please respond ONLY with a corrected valid JSON object.
"""


class InstructorAdapter:
    """
    Extracts typed pydantic models from LLM text output.

    Parameters
    ----------
    gateway:
        The ``LLMGateway`` instance to call.
    max_retries:
        Number of error-correction retries on parse failure.

    Example
    -------
    .. code-block:: python

        from pydantic import BaseModel

        class Verdict(BaseModel):
            decision: str
            confidence: float
            rationale: str

        adapter = InstructorAdapter(gateway=gateway)
        verdict = await adapter.complete(
            messages=[{"role": "user", "content": "Analyse this trend..."}],
            schema=Verdict,
        )
        print(verdict.decision)  # "ENTER"
    """

    def __init__(
        self,
        gateway: "LLMGateway",
        *,
        max_retries: int = _MAX_RETRIES,
    ) -> None:
        self._gateway = gateway
        self._max_retries = max_retries

    async def complete(
        self,
        messages: list[dict[str, str]],
        schema: type[T],
        *,
        provider: str | None = None,
        temperature: float = 0.1,  # Lower temperature for structured output
        max_tokens: int = 2048,
        **kwargs: Any,
    ) -> T:
        """
        Call the LLM and parse output into a pydantic model.

        Parameters
        ----------
        messages:
            Conversation messages (will have schema instructions appended).
        schema:
            Pydantic model class to parse into.
        provider:
            Optional provider override.
        temperature:
            Sampling temperature (default 0.1 for deterministic structured output).

        Returns
        -------
        T
            A validated instance of ``schema``.

        Raises
        ------
        InstructorParseError
            When all retries are exhausted without a valid parse.
        """
        schema_json = json.dumps(schema.model_json_schema(), indent=2)
        instruction = _SCHEMA_INSTRUCTION_TEMPLATE.format(schema=schema_json)

        # Append schema instructions to the last user message
        augmented = list(messages)
        if augmented and augmented[-1]["role"] == "user":
            augmented[-1] = {
                "role": "user",
                "content": augmented[-1]["content"] + "\n" + instruction,
            }
        else:
            augmented.append({"role": "user", "content": instruction})

        last_error = ""
        last_content = ""

        for attempt in range(self._max_retries + 1):
            if attempt > 0:
                # Error-correction prompt
                correction = _ERROR_CORRECTION_TEMPLATE.format(
                    error=last_error,
                    previous=last_content[:500],
                )
                augmented = list(messages) + [
                    {"role": "user", "content": instruction + "\n" + correction}
                ]

            response: LLMResponse = await self._gateway.complete(
                augmented,
                provider=provider,
                temperature=temperature,
                max_tokens=max_tokens,
                **kwargs,
            )
            last_content = response.content

            try:
                parsed = self._parse(response.content, schema)
                _log.debug(
                    "instructor.parse.success",
                    schema=schema.__name__,
                    attempt=attempt,
                )
                return parsed
            except (json.JSONDecodeError, ValidationError, ValueError) as exc:
                last_error = str(exc)[:300]
                _log.warning(
                    "instructor.parse.failed",
                    schema=schema.__name__,
                    attempt=attempt,
                    error=last_error,
                )

        raise InstructorParseError(
            f"Failed to parse {schema.__name__} after {self._max_retries + 1} attempts: {last_error}",
            schema=schema.__name__,
        )

    # ------------------------------------------------------------------
    # Parsing helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse(text: str, schema: type[T]) -> T:
        """Extract JSON from text and validate against schema."""
        # 1. Try to strip markdown fences
        fence_match = _JSON_FENCE_RE.search(text)
        if fence_match:
            text = fence_match.group(1)

        # 2. Try to extract raw JSON object/array
        json_match = _JSON_EXTRACT_RE.search(text)
        if json_match:
            text = json_match.group(1)

        # 3. Parse JSON
        data = json.loads(text.strip())

        # 4. Validate with pydantic
        return schema.model_validate(data)
