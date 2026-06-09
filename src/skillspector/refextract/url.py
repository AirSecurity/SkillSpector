# Marketplace addition (air/space) — NOT part of upstream NVIDIA SkillSpector.
# Licensed under Apache-2.0, consistent with the vendored SkillSpector source.

"""URL references — the Url product and extract_urls().

Ported from the skills.sh audit toolkit (classify_url_context.py +
extract_script_urls.py). Every file in the input map is scanned: markdown files
get full form classification; any other file's URLs get RefForm.SCRIPT_FILE.
"Which kind of file did this come from" is recorded in the Context (file_type),
not used to gate extraction — so no URL is dropped for living in an unusual file.

A Url carries its Context (provenance + form + file type) and the host signals
(host, takeover_platform, apex_domain) the audit scripts derive — so downstream
consumers never re-parse the raw URL.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import NamedTuple
from urllib.parse import urlparse

from skillspector.refextract.context import Context, RefForm, context_for
from skillspector.refextract.host import (
    ServiceFamily,
    TakeoverPlatform,
    apex_domain,
    is_internal_host,
    is_templated,
    service_family,
    takeover_platform,
)
from skillspector.refextract.markdown import fence_map, markdown_form
from skillspector.refextract.paths import is_markdown


@dataclass(frozen=True)
class Url:
    """An http(s) URL occurrence found in a skill."""

    url: str
    context: Context
    host: str | None = None
    service_family: ServiceFamily = ServiceFamily.NONE
    takeover_platform: TakeoverPlatform = TakeoverPlatform.NONE
    apex_domain: str | None = None
    is_template: bool = False  # contains a placeholder (<account>, {host}, ...) — likely noise

    def to_dict(self) -> dict[str, object]:
        return {
            "url": self.url,
            "context": self.context.to_dict(),
            "host": self.host,
            "service_family": str(self.service_family),
            "takeover_platform": str(self.takeover_platform),
            "apex_domain": self.apex_domain,
            "is_template": self.is_template,
        }


class _UrlHit(NamedTuple):
    """One URL occurrence found on a line, before host enrichment."""

    line: int
    column: int
    url: str
    form: RefForm


# ── URL recognizers (classify_url_context.py) ───────────────────────────────

# One recognizer for every file kind. Stop at whitespace, quotes/backtick, and the
# closing delimiters `>`/`]` (markdown autolinks <url>, links [text](url), and
# bracket wrappers). Crucially we DO allow `<`, `(`, `)`, `[` inside: that keeps
# templated refs (https://<account>.foo, https://[your-domain]) and parenthesized
# paths (…/Foo_(bar)) instead of truncating them to nothing. _trim_url removes any
# unbalanced trailing wrapper bracket the regex pulled in.
_URL_RE = re.compile(r"https?://[^\s>\]`\"']+", re.IGNORECASE)
_MD_LINK_RE = re.compile(r"\]\(\s*(https?://[^)\s]+)")
_JSON_VAL_RE = re.compile(r"\"\s*:\s*\"\s*(https?://[^\"]+)")
_QUOTED_VAL_RE = re.compile(r"\"\s*(https?://[^\"]+)\s*\"")


def _trim_url(raw: str) -> str:
    """Strip trailing sentence punctuation and unbalanced wrapper brackets.

    Keeps balanced parens (``.../Foo_(bar)``) but drops the dangling bracket the
    greedy regex pulls in from wrappers like markdown ``](url)``.
    """
    url = raw.rstrip(".,;:!?")
    for close, open_ in ((")", "("), ("]", "["), ("}", "{")):
        while url.endswith(close) and url.count(close) > url.count(open_):
            url = url[:-1]
    return url


def _is_md_link(line: str, url: str) -> bool:
    """True when ``url`` appears inside markdown link syntax [text](url) on this line."""
    return bool(_MD_LINK_RE.search(line)) and url in {
        link.group(1).rstrip(".,;:") for link in _MD_LINK_RE.finditer(line)
    }


def _is_json_value(line: str, url: str, match: re.Match[str]) -> bool:
    """True when ``url`` looks like a JSON/quoted value rather than prose."""
    if _JSON_VAL_RE.search(line) and url in {
        val.group(1).rstrip(".,;:") for val in _JSON_VAL_RE.finditer(line)
    }:
        return True
    quoted = {val.group(1).rstrip(".,;:") for val in _QUOTED_VAL_RE.finditer(line)}
    if url in quoted:
        # Treat as data only when the line is mostly punctuation around the URL.
        non_url = line.replace(match.group(0), "")
        return sum(character.isalpha() for character in non_url) <= 10
    return False


def _classify_markdown_urls(lines: list[str]) -> Iterator[_UrlHit]:
    """Yield a _UrlHit for every URL in a markdown file.

    Uses the shared markdown_form for the structural form, then layers the
    URL-shape-specific MD_LINK / MD_JSON_VALUE on top (these win over prose/fetch
    but never override a code block).
    """
    fence_flags = fence_map(lines)
    for index, line in enumerate(lines):
        for match in _URL_RE.finditer(line):
            url = _trim_url(match.group(0))
            form = markdown_form(lines, fence_flags, index)
            if form is not RefForm.MD_CODE_BLOCK:
                if _is_md_link(line, url):
                    form = RefForm.MD_LINK
                elif _is_json_value(line, url, match):
                    form = RefForm.MD_JSON_VALUE
            yield _UrlHit(index + 1, match.start(), url, form)


def _extract_script_urls(lines: list[str]) -> Iterator[_UrlHit]:
    """Yield a _UrlHit (always SCRIPT_FILE form) for every URL in a non-markdown file."""
    for index, line in enumerate(lines):
        for match in _URL_RE.finditer(line):
            yield _UrlHit(index + 1, match.start(), _trim_url(match.group(0)), RefForm.SCRIPT_FILE)


def _build_url(hit: _UrlHit, path: str) -> Url:
    try:
        host = (urlparse(hit.url).hostname or "").lower()
    except ValueError:
        host = ""
    apex = "" if is_internal_host(host) else apex_domain(host)
    return Url(
        url=hit.url,
        context=context_for(path, hit.line, hit.form),
        host=host or None,
        service_family=service_family(host) if host else ServiceFamily.NONE,
        takeover_platform=takeover_platform(host) if host else TakeoverPlatform.NONE,
        apex_domain=apex or None,
        is_template=is_templated(hit.url),
    )


def extract_urls(files: Mapping[str, str]) -> list[Url]:
    """Extract every http(s) URL occurrence from a {relative-path: content} map.

    Every file is scanned. Markdown files get full form classification; all other
    files yield SCRIPT_FILE-form URLs. The file's nature is recorded in each URL's
    Context (file_type), never used to skip a file. One Url per distinct occurrence
    (file, line, column, url) — repeats on the same line are kept.
    """
    urls: list[Url] = []
    seen: set[tuple[str, int, int, str]] = set()
    for path, content in files.items():
        lines = content.splitlines()
        hits = _classify_markdown_urls(lines) if is_markdown(path) else _extract_script_urls(lines)
        for hit in hits:
            key = (path, hit.line, hit.column, hit.url)
            if key in seen:
                continue
            seen.add(key)
            urls.append(_build_url(hit, path))
    return urls
