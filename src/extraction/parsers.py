"""
Plugin-style parser registry.

Each parser handles a specific source type and returns an ExtractionResult.
Register new parsers by decorating with @register_parser("source_type").
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from bs4 import BeautifulSoup

from src.shared.logging import get_logger

log = get_logger(__name__)

# Parser registry: source_type -> callable
_PARSER_REGISTRY: dict[str, Callable] = {}


def register_parser(source_type: str):
    """Decorator to register a parser for a source type."""
    def decorator(fn: Callable) -> Callable:
        _PARSER_REGISTRY[source_type] = fn
        return fn
    return decorator


def get_parser(source_type: str) -> Callable:
    """Return the parser for a source type, falling back to generic."""
    return _PARSER_REGISTRY.get(source_type, _PARSER_REGISTRY["generic"])


def detect_format(
    headers: dict | None,
    url: str | None,
    raw_bytes: bytes | None,
) -> str | None:
    """Detect RSS/Atom/JSON from content-type, URL suffix, or content sniff.

    Returns 'rss' | 'atom' | 'json', or None when undetectable (e.g. plain HTML).
    These are *format* keys, NOT source_type enum values — the frozen v1.0
    SourceType set (forum|paste|blog|marketplace|news|other) is unchanged.
    """
    # 1. Content-Type header (case-insensitive)
    if headers:
        ct = (
            headers.get("content-type")
            or headers.get("Content-Type")
            or headers.get("content_type")
        )
        if ct:
            ct_l = ct.lower()
            if "application/rss+xml" in ct_l or "text/rss" in ct_l:
                return "rss"
            if "application/atom+xml" in ct_l or "text/atom" in ct_l:
                return "atom"
            if "application/json" in ct_l or "+json" in ct_l:
                return "json"

    # 2. URL suffix
    if url:
        u = url.lower().split("?", 1)[0].split("#", 1)[0]
        if u.endswith(".rss"):
            return "rss"
        if u.endswith(".atom"):
            return "atom"
        if u.endswith(".json"):
            return "json"

    # 3. Content sniff
    if raw_bytes:
        head = raw_bytes[:512].lstrip()
        if head.startswith(b"{"):
            return "json"
        try:
            head_text = head.decode("utf-8", errors="ignore").lower()
        except Exception:
            head_text = ""
        if "<?xml" in head_text and "<rss" in head_text:
            return "rss"
        if "<feed" in head_text:
            return "atom"

    return None


def get_parser_for(
    source_type: str,
    content_type: str | None = None,
    url: str | None = None,
    raw_bytes: bytes | None = None,
) -> Callable:
    """Prefer a parser detected from the content format (content-type/URL/sniff);
    fall back to the existing source-type parser (and its generic fallback)."""
    headers = {"content-type": content_type} if content_type else None
    fmt = detect_format(headers, url, raw_bytes)
    if fmt and fmt in _PARSER_REGISTRY:
        return _PARSER_REGISTRY[fmt]
    return get_parser(source_type)


@dataclass
class ExtractionResult:
    title: str | None = None
    content: str = ""
    author: str | None = None
    published_at: datetime | None = None
    language: str | None = None
    links: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    @property
    def success(self) -> bool:
        return bool(self.content.strip()) and self.error is None


# -----------------------------------------------------------------------
# Generic / fallback parser (uses trafilatura for main-content extraction)
# -----------------------------------------------------------------------

@register_parser("generic")
def parse_generic(raw_bytes: bytes, url: str, source_type: str = "other") -> ExtractionResult:
    import chardet
    import trafilatura

    try:
        detected = chardet.detect(raw_bytes)
        encoding = detected.get("encoding") or "utf-8"
        try:
            html = raw_bytes.decode(encoding, errors="replace")
        except (LookupError, UnicodeDecodeError):
            html = raw_bytes.decode("utf-8", errors="replace")

        # trafilatura for main content
        content = trafilatura.extract(html, include_links=False, include_comments=False) or ""

        # BeautifulSoup for metadata
        soup = BeautifulSoup(html, "lxml")
        title = _extract_title(soup)
        links = _extract_links(soup, url)
        published_at = _extract_date(soup)
        author = _extract_author(soup)

        return ExtractionResult(
            title=title,
            content=content,
            author=author,
            published_at=published_at,
            links=links,
        )
    except Exception as exc:
        log.warning("parse_generic_error", url=url, error=str(exc))
        return ExtractionResult(error=str(exc))


@register_parser("forum")
def parse_forum(raw_bytes: bytes, url: str, source_type: str = "forum") -> ExtractionResult:
    """Forum parser: extracts thread posts."""
    import chardet

    try:
        detected = chardet.detect(raw_bytes)
        encoding = detected.get("encoding") or "utf-8"
        html = raw_bytes.decode(encoding, errors="replace")
        soup = BeautifulSoup(html, "lxml")

        title = _extract_title(soup)
        links = _extract_links(soup, url)
        published_at = _extract_date(soup)
        author = _extract_author(soup)

        # Try to find post content blocks
        post_texts: list[str] = []
        for sel in [
            ".post-content", ".message-body", ".postbody",
            "[class*='post']", "[class*='message']", "article",
        ]:
            blocks = soup.select(sel)
            for b in blocks:
                t = b.get_text(separator=" ", strip=True)
                if len(t) > 50:
                    post_texts.append(t)
            if post_texts:
                break

        if not post_texts:
            # Fallback: trafilatura
            import trafilatura
            content = trafilatura.extract(html, include_links=False) or ""
        else:
            content = "\n\n".join(post_texts)

        return ExtractionResult(
            title=title,
            content=content,
            author=author,
            published_at=published_at,
            links=links,
            extra={"post_count": len(post_texts)},
        )
    except Exception as exc:
        log.warning("parse_forum_error", url=url, error=str(exc))
        return ExtractionResult(error=str(exc))


@register_parser("paste")
def parse_paste(raw_bytes: bytes, url: str, source_type: str = "paste") -> ExtractionResult:
    """Paste-site parser: raw text content."""
    import chardet

    try:
        detected = chardet.detect(raw_bytes)
        encoding = detected.get("encoding") or "utf-8"
        html = raw_bytes.decode(encoding, errors="replace")
        soup = BeautifulSoup(html, "lxml")

        title = _extract_title(soup)

        # Try paste-specific selectors
        content = ""
        for sel in [".paste-content", "#paste_code", ".code", "pre", "textarea"]:
            el = soup.select_one(sel)
            if el:
                content = el.get_text(separator="\n", strip=True)
                break

        if not content:
            import trafilatura
            content = trafilatura.extract(html) or ""

        return ExtractionResult(
            title=title,
            content=content,
            links=_extract_links(soup, url),
        )
    except Exception as exc:
        log.warning("parse_paste_error", url=url, error=str(exc))
        return ExtractionResult(error=str(exc))


@register_parser("blog")
def parse_blog(raw_bytes: bytes, url: str, source_type: str = "blog") -> ExtractionResult:
    """Blog parser: article content."""
    return parse_generic(raw_bytes, url, source_type)


@register_parser("news")
def parse_news(raw_bytes: bytes, url: str, source_type: str = "news") -> ExtractionResult:
    return parse_generic(raw_bytes, url, source_type)


@register_parser("marketplace")
def parse_marketplace(raw_bytes: bytes, url: str, source_type: str = "marketplace") -> ExtractionResult:
    """Marketplace parser: listing content only (public views)."""
    import chardet

    try:
        detected = chardet.detect(raw_bytes)
        encoding = detected.get("encoding") or "utf-8"
        html = raw_bytes.decode(encoding, errors="replace")
        soup = BeautifulSoup(html, "lxml")

        title = _extract_title(soup)

        # Listing selectors
        content = ""
        for sel in [".listing-desc", ".product-description", ".item-description", "article", "main"]:
            el = soup.select_one(sel)
            if el:
                content = el.get_text(separator=" ", strip=True)
                break

        if not content:
            import trafilatura
            content = trafilatura.extract(html) or ""

        price_text = ""
        price_el = soup.select_one("[class*='price']")
        if price_el:
            price_text = price_el.get_text(strip=True)

        return ExtractionResult(
            title=title,
            content=content,
            links=_extract_links(soup, url),
            extra={"price": price_text},
        )
    except Exception as exc:
        log.warning("parse_marketplace_error", url=url, error=str(exc))
        return ExtractionResult(error=str(exc))


# -----------------------------------------------------------------------
# Feed (RSS/Atom) and JSON parsers — registered under *format* keys, NOT
# source_type enum values. They map onto existing frozen source types
# (news/other); the normalizer still sets source_type from the source record.
# -----------------------------------------------------------------------

@register_parser("rss")
def parse_rss(raw_bytes: bytes, url: str, source_type: str = "news") -> ExtractionResult:
    """RSS feed parser using stdlib xml.etree (no feedparser dependency)."""
    import xml.etree.ElementTree as ET

    try:
        text = _decode_bytes(raw_bytes)
        root = ET.fromstring(text)
        # RSS 2.0 root is <rss><channel>; be lenient about the wrapper.
        channel = root.find("channel")
        if channel is None:
            channel = root
        title_el = _find(channel, "title")
        title = title_el.text.strip() if title_el is not None and title_el.text else None

        items = channel.findall("item") or _findall(root, "item")

        contents: list[str] = []
        links: list[str] = []
        published_at: datetime | None = None

        for item in items:
            item_title = _findtext(item, "title") or ""
            item_desc = _findtext(item, "description") or ""
            item_link = _findtext(item, "link") or ""
            if item_link:
                links.append(item_link.strip())
            parts = [p.strip() for p in (item_title, item_desc) if p and p.strip()]
            if parts:
                contents.append(": ".join(parts) if len(parts) > 1 else parts[0])
            if published_at is None:
                pub = _findtext(item, "pubDate") or _findtext(item, "date")
                if pub:
                    published_at = _parse_feed_date(pub)

        content = "\n\n".join(contents).strip()
        return ExtractionResult(
            title=title,
            content=content,
            links=links,
            published_at=published_at,
            extra={"format": "rss"},
        )
    except Exception as exc:
        log.warning("parse_rss_error", url=url, error=str(exc))
        return ExtractionResult(error=str(exc))


@register_parser("atom")
def parse_atom(raw_bytes: bytes, url: str, source_type: str = "news") -> ExtractionResult:
    """Atom feed parser using stdlib xml.etree (namespace-agnostic)."""
    import xml.etree.ElementTree as ET

    try:
        text = _decode_bytes(raw_bytes)
        root = ET.fromstring(text)
        title_el = _find(root, "title")
        title = title_el.text.strip() if title_el is not None and title_el.text else None

        entries = _findall(root, "entry")

        contents: list[str] = []
        links: list[str] = []
        published_at: datetime | None = None

        for entry in entries:
            e_title = _findtext(entry, "title") or ""
            e_content = _findtext(entry, "content") or _findtext(entry, "summary") or ""
            e_link = None
            for link in _findall(entry, "link"):
                href = link.get("href")
                if href:
                    e_link = href
                    if (link.get("rel") or "alternate") == "alternate":
                        break
            if e_link:
                links.append(e_link.strip())
            parts = [p.strip() for p in (e_title, e_content) if p and p.strip()]
            if parts:
                contents.append(": ".join(parts) if len(parts) > 1 else parts[0])
            if published_at is None:
                pub = _findtext(entry, "published") or _findtext(entry, "updated")
                if pub:
                    published_at = _parse_feed_date(pub)

        content = "\n\n".join(contents).strip()
        return ExtractionResult(
            title=title,
            content=content,
            links=links,
            published_at=published_at,
            extra={"format": "atom"},
        )
    except Exception as exc:
        log.warning("parse_atom_error", url=url, error=str(exc))
        return ExtractionResult(error=str(exc))


@register_parser("json")
def parse_json(raw_bytes: bytes, url: str, source_type: str = "other") -> ExtractionResult:
    """JSON document parser using stdlib json. Extracts common title/content/link keys."""
    import json

    try:
        text = _decode_bytes(raw_bytes)
        data = json.loads(text)
        records = data if isinstance(data, list) else [data]

        titles: list[str] = []
        contents: list[str] = []
        links: list[str] = []
        author: str | None = None
        published_at: datetime | None = None

        for rec in records:
            if not isinstance(rec, dict):
                continue
            t = _first_key(rec, ("title", "name", "subject"))
            c = _first_key(rec, ("content", "body", "text", "description", "summary", "message"))
            link = _first_key(rec, ("link", "url", "href"))
            if t:
                titles.append(str(t))
            if c:
                contents.append(str(c))
            if link:
                links.append(str(link))
            if author is None:
                a = _first_key(rec, ("author", "author_name", "byline"))
                if a:
                    author = str(a)
            if published_at is None:
                pub = _first_key(rec, ("published_at", "published", "pubDate", "date", "updated"))
                if pub:
                    published_at = _parse_feed_date(str(pub))

        title = titles[0] if titles else None
        content = "\n\n".join(contents).strip()
        if not content:
            # Fall back to the raw text so the record is not empty
            content = text.strip()

        return ExtractionResult(
            title=title,
            content=content,
            author=author,
            links=links,
            published_at=published_at,
            extra={"format": "json"},
        )
    except Exception as exc:
        log.warning("parse_json_error", url=url, error=str(exc))
        return ExtractionResult(error=str(exc))


# -----------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------

def _extract_title(soup: BeautifulSoup) -> str | None:
    og = soup.find("meta", property="og:title")
    if og and og.get("content"):
        return str(og["content"]).strip()
    h1 = soup.find("h1")
    if h1:
        return h1.get_text(strip=True) or None
    title_tag = soup.find("title")
    if title_tag:
        return title_tag.get_text(strip=True) or None
    return None


def _extract_links(soup: BeautifulSoup, base_url: str) -> list[str]:
    links: list[str] = []
    for a in soup.find_all("a", href=True):
        href = str(a["href"]).strip()
        if href.startswith("http"):
            links.append(href)
        elif href.startswith("/"):
            # Resolve relative
            from urllib.parse import urljoin
            links.append(urljoin(base_url, href))
    return list(dict.fromkeys(links))[:50]  # deduplicate, cap at 50


def _extract_date(soup: BeautifulSoup) -> datetime | None:
    import dateparser

    # Try meta tags
    for sel, attr in [
        ("meta[property='article:published_time']", "content"),
        ("meta[name='date']", "content"),
        ("meta[name='pubdate']", "content"),
        ("time[datetime]", "datetime"),
    ]:
        el = soup.select_one(sel)
        if el:
            val = el.get(attr, "")
            if val:
                try:
                    return dateparser.parse(str(val))
                except Exception:
                    pass

    # Try visible time elements
    time_el = soup.find("time")
    if time_el:
        dt_attr = time_el.get("datetime", "")
        if dt_attr:
            try:
                return dateparser.parse(str(dt_attr))
            except Exception:
                pass

    return None


def _extract_author(soup: BeautifulSoup) -> str | None:
    for sel, attr in [
        ("meta[name='author']", "content"),
        ("meta[property='article:author']", "content"),
    ]:
        el = soup.select_one(sel)
        if el and el.get(attr):
            return str(el[attr]).strip()

    for sel in [".author", "[rel='author']", "[class*='author']"]:
        el = soup.select_one(sel)
        if el:
            text = el.get_text(strip=True)
            if text:
                return text

    return None


# -----------------------------------------------------------------------
# Feed / JSON helpers (stdlib only; namespace-agnostic for XML)
# -----------------------------------------------------------------------

def _decode_bytes(raw_bytes: bytes) -> str:
    """Decode bytes to text, honoring the detected encoding."""
    import chardet
    detected = chardet.detect(raw_bytes)
    encoding = detected.get("encoding") or "utf-8"
    try:
        return raw_bytes.decode(encoding, errors="replace")
    except (LookupError, UnicodeDecodeError):
        return raw_bytes.decode("utf-8", errors="replace")


def _find(el, tag: str):
    """Find the first child element by local name, ignoring XML namespaces."""
    for child in el:
        if child.tag.split("}")[-1] == tag:
            return child
    return None


def _findall(el, tag: str) -> list:
    """Find all child elements by local name, ignoring XML namespaces."""
    return [c for c in el if c.tag.split("}")[-1] == tag]


def _findtext(el, tag: str) -> str | None:
    """Return trimmed text of the first matching child, or None."""
    child = _find(el, tag)
    if child is not None and child.text:
        return child.text.strip()
    return None


def _first_key(d: dict, keys: tuple[str, ...]):
    """Return the first present, non-empty value among keys."""
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return None


def _parse_feed_date(value: str) -> datetime | None:
    """Parse a feed date string (RFC822 or ISO-8601) into a datetime."""
    if not value:
        return None
    import dateparser
    try:
        return dateparser.parse(value.strip())
    except Exception:
        return None
