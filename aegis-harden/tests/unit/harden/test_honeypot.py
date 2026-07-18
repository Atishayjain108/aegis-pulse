"""Tests for `aegis.harden.honeypot`."""

from __future__ import annotations

from aegis.harden.honeypot import filter_safe, score_element, score_url


class TestScoreURL:
    def test_clean_url(self) -> None:
        v = score_url("https://example.com/articles/123")
        assert v.blocked is False
        assert v.score == 0.0

    def test_donotvisit_path(self) -> None:
        v = score_url("https://example.com/donotvisit")
        assert v.score > 0.4

    def test_honeypot_token(self) -> None:
        v = score_url("https://example.com/honeypot")
        assert v.blocked is True

    def test_trap_query_param(self) -> None:
        v = score_url("https://example.com/page?bot=trap")
        assert v.blocked is True

    def test_donotclick_query(self) -> None:
        v = score_url("https://example.com/foo?donotclick=1")
        assert v.blocked is True

    def test_hidden_dot_path(self) -> None:
        v = score_url("https://example.com/.hidden-trap")
        assert v.score > 0.3

    def test_empty_string(self) -> None:
        v = score_url("")
        assert v.score == 0.0
        assert v.blocked is False

    def test_score_clamped_to_unity(self) -> None:
        # Multiple tokens — score should clamp to 1.0
        v = score_url("https://x.com/honeypot/trap?bot=trap")
        assert v.score == 1.0


class TestScoreElement:
    def test_clean_anchor(self) -> None:
        el = {
            "href": "https://x.com/blog/article",
            "tag": "a",
            "text": "Read more",
            "classes": ["btn", "primary"],
            "attrs": {},
            "style": "",
            "rect": {"x": 100, "y": 200, "w": 80, "h": 24},
        }
        v = score_element(el)
        assert v.blocked is False

    def test_honeypot_class(self) -> None:
        el = {
            "href": "https://x.com/x",
            "classes": ["donotclick"],
            "text": "hi",
            "rect": {"x": 1, "y": 1, "w": 10, "h": 10},
        }
        v = score_element(el)
        assert v.blocked is True
        assert any("class:" in r for r in v.reasons)

    def test_data_attribute_trap(self) -> None:
        el = {
            "href": "https://x.com/x",
            "classes": [],
            "text": "hi",
            "attrs": {"data-honeypot": "1"},
            "rect": {"x": 1, "y": 1, "w": 10, "h": 10},
        }
        v = score_element(el)
        assert any("attr:" in r for r in v.reasons)

    def test_display_none_style(self) -> None:
        el = {
            "href": "https://x.com/x",
            "classes": [],
            "text": "hi",
            "style": "display:none;",
            "rect": {"x": 1, "y": 1, "w": 10, "h": 10},
        }
        v = score_element(el)
        assert v.score >= 0.4

    def test_offscreen_rect(self) -> None:
        el = {
            "href": "https://x.com/x",
            "classes": [],
            "text": "hi",
            "rect": {"x": -99999, "y": 0, "w": 10, "h": 10},
        }
        v = score_element(el)
        assert any("rect:offscreen" in r for r in v.reasons)

    def test_zero_area_rect(self) -> None:
        el = {
            "href": "https://x.com/x",
            "classes": [],
            "text": "hi",
            "rect": {"x": 100, "y": 100, "w": 0, "h": 0},
        }
        v = score_element(el)
        assert any("rect:zero-area" in r for r in v.reasons)

    def test_empty_text_with_href(self) -> None:
        el = {
            "href": "https://x.com/foo",
            "classes": [],
            "text": "",
            "rect": {"x": 100, "y": 100, "w": 50, "h": 24},
        }
        v = score_element(el)
        assert any("empty-text" in r for r in v.reasons)

    def test_combined_signals_block(self) -> None:
        el = {
            "href": "https://x.com/x",
            "classes": ["antibot"],
            "text": "",
            "style": "opacity:0;",
            "rect": {"x": 0, "y": 0, "w": 10, "h": 10},
        }
        v = score_element(el)
        assert v.blocked is True

    def test_filter_safe_drops_blocked(self) -> None:
        elements = [
            {
                "href": "https://x.com/ok",
                "classes": [],
                "text": "ok",
                "rect": {"x": 100, "y": 100, "w": 50, "h": 24},
            },
            {
                "href": "https://x.com/bad",
                "classes": ["donotclick"],
                "text": "x",
                "rect": {"x": 100, "y": 100, "w": 50, "h": 24},
            },
        ]
        kept = filter_safe(elements)  # type: ignore[arg-type]
        assert len(kept) == 1
        assert kept[0]["href"] == "https://x.com/ok"
