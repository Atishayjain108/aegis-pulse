"""Tests for the LLM-output guardrails."""

from __future__ import annotations

from pydantic import BaseModel

from aegis.agents.llm.guardrails import extract_json, parse_into, parse_json


class TestExtractJson:
    def test_strips_markdown_fence(self) -> None:
        text = '```json\n{"a": 1}\n```'
        assert extract_json(text) == '{"a": 1}'

    def test_strips_uppercase_fence(self) -> None:
        text = '```JSON\n{"a": 1}\n```'
        assert extract_json(text) == '{"a": 1}'

    def test_finds_object_in_prose(self) -> None:
        text = 'I think the answer is {"score": 0.7, "reason": "lots"} probably'
        assert extract_json(text) == '{"score": 0.7, "reason": "lots"}'

    def test_finds_array(self) -> None:
        text = "Here you go: [1, 2, 3] and that's all"
        assert extract_json(text) == "[1, 2, 3]"

    def test_balanced_with_strings(self) -> None:
        text = '{"a": "this } is in a string"}'
        assert extract_json(text) == '{"a": "this } is in a string"}'

    def test_balanced_with_escaped_quote(self) -> None:
        text = '{"a": "he said \\"yes\\""}'
        assert extract_json(text) == '{"a": "he said \\"yes\\""}'

    def test_picks_larger_candidate(self) -> None:
        text = '{"x": [1,2]} other'
        assert extract_json(text) == '{"x": [1,2]}'

    def test_returns_none_when_no_json(self) -> None:
        assert extract_json("just words") is None

    def test_returns_none_on_empty(self) -> None:
        assert extract_json("") is None


class TestParseJson:
    def test_strict_object(self) -> None:
        result = parse_json('{"a": 1, "b": [1,2]}')
        assert result == {"a": 1, "b": [1, 2]}

    def test_repairs_trailing_comma_in_object(self) -> None:
        result = parse_json('{"a": 1, "b": 2,}')
        assert result == {"a": 1, "b": 2}

    def test_repairs_trailing_comma_in_array(self) -> None:
        result = parse_json("[1, 2, 3,]")
        assert result == [1, 2, 3]

    def test_returns_none_on_garbage(self) -> None:
        assert parse_json("complete garbage with {{") is None

    def test_handles_markdown_fenced(self) -> None:
        text = '```json\n{"reasoning": "x", "confidence_factor": 0.8}\n```'
        result = parse_json(text)
        assert result == {"reasoning": "x", "confidence_factor": 0.8}


class _Sample(BaseModel):
    name: str
    score: float


class TestParseInto:
    def test_valid_object(self) -> None:
        out = parse_into('{"name": "x", "score": 0.5}', _Sample)
        assert out is not None
        assert out.name == "x"

    def test_invalid_returns_none(self) -> None:
        assert parse_into('{"name": "x"}', _Sample) is None

    def test_unparseable_returns_none(self) -> None:
        assert parse_into("not json", _Sample) is None
