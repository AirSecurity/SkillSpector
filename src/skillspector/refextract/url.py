# Marketplace addition (air/space) — NOT part of upstream NVIDIA SkillSpector.
# Licensed under Apache-2.0, consistent with the vendored SkillSpector source.

"""URL references — the Url product and extract_urls().

Ported from the skills.sh audit toolkit (classify_url_context.py +
extract_script_urls.py). Every file in the input map is scanned: markdown files
get full form classification; any other file's URLs get RefForm.SCRIPT_FILE.
"Which kind of file did this come from" is recorded in the Context (file_type),
not used to gate extraction — so no URL is dropped for living in an unusual file.

A Url carries its Context (provenance + form + file type) and the host signals
(host, takeover_platform, apex_domain) the audit scripts derive, as convenience
fields for consumers. (Derivation modules like domain.py still re-parse the raw
URL themselves — url_sources yields bare (url, context) so Mcp endpoints get
identical treatment; the duplicate parse is accepted, perf is not a constraint.)
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
    """A URL-shaped reference occurrence found in a skill — any URI scheme
    (https, s3, ftp, git+ssh, …), protocol-relative (//cdn…), or bare www form;
    the scheme is readable off the kept string."""

    url: str
    context: Context
    host: str | None = None
    scheme: str | None = None  # as written, lowercased (https, s3, git+ssh); None when
    #                            nothing was written (protocol-relative //, bare www.)
    port: int | None = None  # explicit port only — default ports are not inferred
    service_family: ServiceFamily = ServiceFamily.NONE
    takeover_platform: TakeoverPlatform = TakeoverPlatform.NONE
    apex_domain: str | None = None
    is_template: bool = False  # contains a placeholder (<account>, {host}, ...) — likely noise

    def to_dict(self) -> dict[str, object]:
        return {
            "url": self.url,
            "context": self.context.to_dict(),
            "host": self.host,
            "scheme": self.scheme,
            "port": self.port,
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

# One recognizer for every file kind, with three entry points:
#   * any URI scheme (https, s3, gs, ftp, ws, git+ssh, ...) — capture, don't gate
#     on scheme; "what scheme is it" is readable off the kept string,
#   * protocol-relative //cdn.example.com (lookarounds keep out floor division
#     `a//b`, paths `x.com//y`, and `// comment` styles — the next chars must be
#     domain-shaped),
#   * bare www.example.com.
# Stop at whitespace, quotes/backtick, and the closing delimiters `>`/`]`
# (markdown autolinks <url>, links [text](url), and bracket wrappers). Crucially
# we DO allow `<`, `(`, `)`, `[` inside: that keeps templated refs
# (https://<account>.foo, https://[your-domain]) and parenthesized paths
# (…/Foo_(bar)) instead of truncating them to nothing. _trim_url removes any
# unbalanced trailing wrapper bracket the regex pulled in.
_SCHEME = r"[a-z][a-z0-9+.\-]*://"
# The tail is `*`, not `+`: a bare scheme mention ("upload to s3:// paths") has no
# target but IS a capability signal — Url(scheme="s3", host=None) — don't gate it.
# The optional bracket group right after the scheme keeps IPv6 literals
# (https://[::1]:8080/x) whole even though `]` ends the tail elsewhere.
_URL_RE = re.compile(
    rf"(?:{_SCHEME}(?:\[[0-9A-Fa-f:.]+\])?|(?<![\w:./])//(?=[\w-]+\.)|(?<![\w.@/-])www\.(?=[\w-]))"
    r"[^\s>\]`\"']*",
    re.IGNORECASE,
)
_MD_LINK_RE = re.compile(rf"\]\(\s*((?:{_SCHEME}|//|www\.)[^)\s]+)", re.IGNORECASE)
_JSON_VAL_RE = re.compile(rf"\"\s*:\s*\"\s*((?:{_SCHEME}|//|www\.)[^\"]+)", re.IGNORECASE)
_QUOTED_VAL_RE = re.compile(rf"\"\s*((?:{_SCHEME}|//|www\.)[^\"]+)\s*\"", re.IGNORECASE)


_BRACKET_OPENER_FOR_CLOSER = {")": "(", "]": "[", "}": "{"}
_BARE_SCHEME_RE = re.compile(rf"(?:{_SCHEME}|//|www\.)", re.IGNORECASE)


def _trim_url(raw: str) -> str:
    """Strip trailing sentence punctuation and post-wrapper junk.

    Keeps balanced brackets (``.../Foo_(bar)``, ``/{tenant}/``) but cuts at the
    first *unbalanced* closer: in markdown, a closer with no matching opener is
    the wrapper's own bracket (``](url)``, ``{{url}}``), and anything after it —
    bold markers ``)**``, table pipes ``)|``, CJK punctuation ``)。`` — is prose
    the greedy regex swallowed, not URL.
    """
    url = raw
    while True:
        trimmed = _cut_at_unbalanced_closer(url.rstrip(".,;:!?"))
        if trimmed == url:
            break
        url = trimmed
    if _BARE_SCHEME_RE.fullmatch(url):
        # Trimming consumed the whole rest. A placeholder ellipsis (https://...)
        # keeps its marker so is_templated sees it; plain sentence punctuation
        # after a bare scheme mention ("with az://.") is not content — drop it.
        bare = _cut_at_unbalanced_closer(raw)
        return bare if bare.endswith(("...", "…")) else bare.rstrip(".,;:!?")
    return url


def _cut_at_unbalanced_closer(url: str) -> str:
    """Truncate ``url`` at the first closing bracket that has no matching opener."""
    open_depth = dict.fromkeys(_BRACKET_OPENER_FOR_CLOSER.values(), 0)
    for position, character in enumerate(url):
        if character in open_depth:
            open_depth[character] += 1
        elif character in _BRACKET_OPENER_FOR_CLOSER:
            opener = _BRACKET_OPENER_FOR_CLOSER[character]
            if open_depth[opener] == 0:
                return url[:position]
            open_depth[opener] -= 1
    return url


def first_url_on_line(line: str) -> str | None:
    """The first http(s) URL on a line, trimmed — the one recognizer for every
    extractor (fetchexec uses this; a second regex copy would drift)."""
    match = _URL_RE.search(line)
    return _trim_url(match.group(0)) if match else None


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
    scheme: str | None = None
    port: int | None = None
    try:
        parsed = urlparse(hit.url)
        host = (parsed.hostname or "").lower()
        scheme = parsed.scheme.lower() or None
        try:
            port = parsed.port  # explicit port only; raises on a malformed one
        except ValueError:
            port = None
    except ValueError:
        host = ""
    if not host and hit.url.lower().startswith("www."):
        # urlparse sees a scheme-less www.example.com[:port]/path as all-path (or
        # worse, "www.example.com" as the scheme); the host signals are too
        # valuable to lose on the bare-www form.
        bare = re.match(r"([^/?#:]+)(?::(\d+))?", hit.url)
        host = bare.group(1).lower() if bare else ""
        port = int(bare.group(2)) if bare and bare.group(2) else None
        if port is not None and port > 65535:
            port = None  # malformed; the raw text stays on .url
        scheme = None  # nothing was written
    apex = "" if is_internal_host(host) else apex_domain(host)
    return Url(
        url=hit.url,
        context=context_for(path, hit.line, hit.form),
        host=host or None,
        scheme=scheme,
        port=port,
        service_family=service_family(host) if host else ServiceFamily.NONE,
        takeover_platform=takeover_platform(host) if host else TakeoverPlatform.NONE,
        apex_domain=apex or None,
        is_template=is_templated(hit.url),
    )


def extract_urls(files: Mapping[str, str]) -> list[Url]:
    """Extract every URL-shaped occurrence (any scheme / protocol-relative / bare
    www) from a {relative-path: content} map.

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
