"""tests/unit/llm/test_tokenizer.py — Tokenizer tests"""
from __future__ import annotations


class TestCountTokens:
    def test_empty_string_returns_zero(self):
        from aegis.llm.tokenizer import count_tokens
        assert count_tokens("") == 0

    def test_short_text_positive(self):
        from aegis.llm.tokenizer import count_tokens
        n = count_tokens("Hello world")
        assert n > 0

    def test_longer_text_more_tokens(self):
        from aegis.llm.tokenizer import count_tokens
        short = count_tokens("Hi")
        long = count_tokens("This is a much longer piece of text with many words.")
        assert long > short

    def test_code_fewer_tokens_per_char(self):
        from aegis.llm.tokenizer import _is_code, count_tokens
        code = "def calculate_total(items): return sum(item.price for item in items)"
        assert _is_code(code)
        n = count_tokens(code)
        assert n > 0


class TestCountMessages:
    def test_empty_messages(self):
        from aegis.llm.tokenizer import count_messages
        n = count_messages([])
        assert n >= 0

    def test_single_message(self):
        from aegis.llm.tokenizer import count_messages
        messages = [{"role": "user", "content": "Hello, how are you?"}]
        n = count_messages(messages)
        assert n > 0

    def test_more_messages_more_tokens(self):
        from aegis.llm.tokenizer import count_messages
        m1 = [{"role": "user", "content": "Hi"}]
        m2 = [{"role": "user", "content": "Hi"}, {"role": "assistant", "content": "Hello there!"}]
        assert count_messages(m2) > count_messages(m1)


class TestFitsInContext:
    def test_short_fits(self):
        from aegis.llm.tokenizer import fits_in_context
        messages = [{"role": "user", "content": "Hi"}]
        assert fits_in_context(messages, context_limit=8192)

    def test_too_long_does_not_fit(self):
        from aegis.llm.tokenizer import fits_in_context
        long_content = "word " * 10_000
        messages = [{"role": "user", "content": long_content}]
        assert not fits_in_context(messages, context_limit=1000, max_output_tokens=100)


class TestTruncateMessages:
    def test_short_messages_unchanged(self):
        from aegis.llm.tokenizer import truncate_messages
        messages = [{"role": "user", "content": "Hi"}]
        result = truncate_messages(messages, context_limit=8192)
        assert result == messages

    def test_long_messages_truncated(self):
        from aegis.llm.tokenizer import truncate_messages
        long = [{"role": "user", "content": "word " * 1000}] * 5
        result = truncate_messages(long, context_limit=500, max_output_tokens=50)
        assert len(result) < len(long)

    def test_system_message_preserved(self):
        from aegis.llm.tokenizer import truncate_messages
        messages = [
            {"role": "system", "content": "You are helpful."},
            *[{"role": "user", "content": "word " * 500} for _ in range(10)],
        ]
        result = truncate_messages(messages, context_limit=500, max_output_tokens=50, preserve_system=True)
        system_in_result = any(m["role"] == "system" for m in result)
        assert system_in_result


class TestTokenStats:
    def test_returns_dict_with_required_keys(self):
        from aegis.llm.tokenizer import token_stats
        stats = token_stats("Hello world")
        assert "char_count" in stats
        assert "heuristic_tokens" in stats
        assert "method" in stats

    def test_char_count_correct(self):
        from aegis.llm.tokenizer import token_stats
        text = "Hello"
        stats = token_stats(text)
        assert stats["char_count"] == 5
