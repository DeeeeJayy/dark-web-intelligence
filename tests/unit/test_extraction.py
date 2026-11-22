"""Tests for extraction parsers against frozen HTML fixtures."""
from __future__ import annotations

from pathlib import Path

import pytest

from src.extraction.parsers import (
    detect_format,
    get_parser,
    get_parser_for,
    parse_atom,
    parse_blog,
    parse_forum,
    parse_json,
    parse_paste,
    parse_rss,
)

FIXTURES = Path(__file__).parent.parent / "fixtures" / "html"
FEED_FIXTURES = Path(__file__).parent.parent / "fixtures" / "feeds"
API_FIXTURES = Path(__file__).parent.parent / "fixtures" / "api"


def _load(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _load_feed(name: str) -> bytes:
    return (FEED_FIXTURES / name).read_bytes()


def _load_api(name: str) -> bytes:
    return (API_FIXTURES / name).read_bytes()


class TestForumParser:
    def test_extracts_content(self):
        raw = _load("forum_post.html")
        result = parse_forum(raw, "http://localhost:9999/forum_a/thread/1")
        assert result.success
        assert len(result.content) > 20

    def test_extracts_title(self):
        raw = _load("forum_post.html")
        result = parse_forum(raw, "http://localhost:9999/forum_a/thread/1")
        assert result.title is not None
        assert "Security" in result.title or "Thread" in result.title

    def test_extracts_links(self):
        raw = _load("forum_post.html")
        result = parse_forum(raw, "http://localhost:9999/forum_a/thread/1")
        assert isinstance(result.links, list)

    def test_extracts_date(self):
        raw = _load("forum_post.html")
        result = parse_forum(raw, "http://localhost:9999/forum_a/thread/1")
        assert result.published_at is not None


class TestPasteParser:
    def test_extracts_content(self):
        raw = _load("paste_content.html")
        result = parse_paste(raw, "http://localhost:9999/paste_b/abc")
        assert result.success
        assert "testuser" in result.content or len(result.content) > 10

    def test_extracts_title(self):
        raw = _load("paste_content.html")
        result = parse_paste(raw, "http://localhost:9999/paste_b/abc")
        assert result.title is not None


class TestBlogParser:
    def test_extracts_content(self):
        raw = _load("blog_article.html")
        result = parse_blog(raw, "http://localhost:9999/blog_c/article/1")
        assert result.success
        assert len(result.content) > 20

    def test_extracts_title(self):
        raw = _load("blog_article.html")
        result = parse_blog(raw, "http://localhost:9999/blog_c/article/1")
        assert result.title is not None


class TestEmptyPage:
    def test_empty_page_not_success(self):
        raw = _load("empty_page.html")
        parser = get_parser("generic")
        result = parser(raw, "http://localhost:9999/empty")
        # Empty page should produce no meaningful content
        assert not result.success or len(result.content.strip()) == 0

    def test_empty_page_no_exception(self):
        raw = _load("empty_page.html")
        parser = get_parser("generic")
        # Must not raise
        result = parser(raw, "http://localhost:9999/empty")
        assert result is not None


class TestParserRegistry:
    def test_known_types_resolve(self):
        # Frozen source_type values plus the new *format* keys (rss/atom/json)
        # are registered; format keys are NOT source_type enum values.
        for t in ["forum", "paste", "blog", "generic", "news", "marketplace",
                  "rss", "atom", "json"]:
            fn = get_parser(t)
            assert callable(fn)

    def test_unknown_type_falls_back_to_generic(self):
        fn = get_parser("completely_unknown_type")
        assert fn == get_parser("generic")


class TestRssParser:
    def test_extracts_title(self):
        raw = _load_feed("sample_rss.xml")
        result = parse_rss(raw, "http://localhost:9999/feeds/rss")
        assert result.success
        assert result.title is not None
        assert "Security" in result.title

    def test_extracts_content(self):
        raw = _load_feed("sample_rss.xml")
        result = parse_rss(raw, "http://localhost:9999/feeds/rss")
        assert result.success
        assert len(result.content) > 20
        assert result.extra.get("format") == "rss"

    def test_extracts_links(self):
        raw = _load_feed("sample_rss.xml")
        result = parse_rss(raw, "http://localhost:9999/feeds/rss")
        assert isinstance(result.links, list)
        assert any("http://localhost:9999" in link for link in result.links)


class TestAtomParser:
    def test_extracts_title(self):
        raw = _load_feed("sample_atom.xml")
        result = parse_atom(raw, "http://localhost:9999/feeds/atom")
        assert result.success
        assert result.title is not None
        assert "Threat" in result.title or "Atom" in result.title

    def test_extracts_content(self):
        raw = _load_feed("sample_atom.xml")
        result = parse_atom(raw, "http://localhost:9999/feeds/atom")
        assert result.success
        assert len(result.content) > 20
        assert result.extra.get("format") == "atom"

    def test_extracts_links(self):
        raw = _load_feed("sample_atom.xml")
        result = parse_atom(raw, "http://localhost:9999/feeds/atom")
        assert isinstance(result.links, list)
        assert any("http://localhost:9999" in link for link in result.links)


class TestJsonParser:
    def test_extracts_title(self):
        raw = _load_api("sample.json")
        result = parse_json(raw, "http://localhost:9999/api/bulletins/1")
        assert result.success
        assert result.title is not None
        assert "JSON" in result.title

    def test_extracts_content(self):
        raw = _load_api("sample.json")
        result = parse_json(raw, "http://localhost:9999/api/bulletins/1")
        assert result.success
        assert len(result.content) > 20
        assert result.extra.get("format") == "json"

    def test_extracts_links(self):
        raw = _load_api("sample.json")
        result = parse_json(raw, "http://localhost:9999/api/bulletins/1")
        assert isinstance(result.links, list)
        assert any("http://localhost:9999" in link for link in result.links)


class TestDetectFormat:
    def test_rss_from_content_type(self):
        assert detect_format({"content-type": "application/rss+xml"}, None, None) == "rss"

    def test_atom_from_content_type(self):
        assert detect_format({"content-type": "application/atom+xml"}, None, None) == "atom"

    def test_json_from_content_type(self):
        assert detect_format({"content-type": "application/json"}, None, None) == "json"

    def test_html_returns_none(self):
        assert detect_format({"content-type": "text/html"}, None, None) is None

    def test_rss_from_url_suffix(self):
        assert detect_format(None, "http://localhost:9999/feed.rss", None) == "rss"

    def test_atom_from_url_suffix(self):
        assert detect_format(None, "http://localhost:9999/feed.atom", None) == "atom"

    def test_json_from_url_suffix(self):
        assert detect_format(None, "http://localhost:9999/data.json", None) == "json"

    def test_html_url_returns_none(self):
        assert detect_format(None, "http://localhost:9999/page.html", None) is None

    def test_rss_from_content_sniff(self):
        assert detect_format(None, None, b'<?xml version="1.0"?><rss version="2.0">') == "rss"

    def test_atom_from_content_sniff(self):
        assert detect_format(None, None, b'<?xml version="1.0"?>\n<feed xmlns="http://www.w3.org/2005/Atom">') == "atom"

    def test_json_from_content_sniff(self):
        assert detect_format(None, None, b'{"title": "x"}') == "json"


class TestGetParserFor:
    def test_prefers_rss_parser_from_content_type(self):
        assert get_parser_for("news", content_type="application/rss+xml") == parse_rss

    def test_prefers_atom_parser_from_content_type(self):
        assert get_parser_for("news", content_type="application/atom+xml") == parse_atom

    def test_prefers_json_parser_from_content_type(self):
        assert get_parser_for("other", content_type="application/json") == parse_json

    def test_prefers_rss_from_url_suffix(self):
        assert get_parser_for("news", url="http://localhost:9999/feed.rss") == parse_rss

    def test_prefers_json_from_content_sniff(self):
        assert get_parser_for("other", raw_bytes=b'{"title": "x"}') == parse_json

    def test_falls_back_to_source_type_parser(self):
        # No detectable format -> the source-type parser (parse_forum) is used.
        assert get_parser_for("forum", content_type="text/html") == parse_forum

    def test_falls_back_to_generic_for_unknown_type(self):
        assert get_parser_for("completely_unknown_type") == get_parser("generic")
