# Marketplace addition (air/space) — NOT part of upstream NVIDIA SkillSpector.
# Licensed under Apache-2.0, consistent with the vendored SkillSpector source.

"""Source-forge repo references — the Repo product and derive_repos().

Derived from every URL-bearing product (a Url, or an Mcp's remote endpoint), so a
Repo inherits the context of the URL it came from. Covers the common hosted
forges (GitHub, GitLab, Bitbucket, Codeberg) plus gists — not GitHub alone, so
non-GitHub repo references aren't silently downgraded to a bare Url/Domain.
Ported from find_external_refs.py (github.com category).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from urllib.parse import urlparse

from skillspector.refextract.mcp import Mcp
from skillspector.refextract.occurrence import Occurrence
from skillspector.refextract.sources import url_sources
from skillspector.refextract.url import Url

# Reserved first-path segments that are never repo owners (GitHub's set; harmless
# to apply to the other forges).
_NON_OWNER = frozenset(
    {"", "orgs", "sponsors", "marketplace", "features", "topics", "collections",
     "settings", "notifications", "explore", "search", "login", "join", "about",
     "pricing", "apps", "users"}
)
# Forge hostname -> canonical host. raw/www github variants fold to github.com so
# the same repo isn't double-counted; gists stay distinct (a gist isn't a repo).
_FORGE_HOSTS = {
    "github.com": "github.com",
    "www.github.com": "github.com",
    "raw.githubusercontent.com": "github.com",
    "gist.github.com": "gist.github.com",
    "gitlab.com": "gitlab.com",
    "www.gitlab.com": "gitlab.com",
    "bitbucket.org": "bitbucket.org",
    "codeberg.org": "codeberg.org",
}


@dataclass(frozen=True)
class Repo:
    """A hosted-forge "owner/repo" referenced by a skill, with every reference site."""

    host: str  # canonical forge host (github.com / gitlab.com / bitbucket.org / ...)
    owner: str
    repo: str
    occurrences: tuple[Occurrence, ...]

    @property
    def slug(self) -> str:
        """The canonical "owner/repo" identifier."""
        return f"{self.owner}/{self.repo}"

    def to_dict(self) -> dict[str, object]:
        return {
            "host": self.host,
            "owner": self.owner,
            "repo": self.repo,
            "slug": self.slug,
            "occurrences": [occurrence.to_dict() for occurrence in self.occurrences],
        }


def _forge_repo(url: str) -> tuple[str, str, str] | None:
    """Return (canonical_host, owner, repo) for a forge URL, else None."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    host = _FORGE_HOSTS.get((parsed.hostname or "").lower())
    if host is None:
        return None
    segments = [segment for segment in parsed.path.split("/") if segment]
    if len(segments) < 2 or segments[0].lower() in _NON_OWNER:
        return None
    if host == "gitlab.com":
        # GitLab supports nested groups (owner/sub.../project); "/-/" ends the repo
        # path (e.g. .../-/tree/main). Keep the full namespace as the repo.
        if "-" in segments:
            segments = segments[: segments.index("-")]
        if len(segments) < 2:
            return None
        return host, segments[0], "/".join(segments[1:]).removesuffix(".git")
    return host, segments[0], segments[1].removesuffix(".git")


def derive_repos(urls: Iterable[Url], mcps: Iterable[Mcp] = ()) -> list[Repo]:
    """Derive forge-repo references from every URL-bearing product.

    One Repo per distinct (host, owner, repo); every reference site is an Occurrence.
    """
    entries: dict[tuple[str, str, str], list[Occurrence]] = {}
    for source_url, context in url_sources(urls, mcps):
        forge_repo = _forge_repo(source_url)
        if forge_repo is None:
            continue
        entries.setdefault(forge_repo, []).append(
            Occurrence(context=context, source_url=source_url)
        )
    return [
        Repo(host=host, owner=owner, repo=repo, occurrences=tuple(occurrences))
        for (host, owner, repo), occurrences in entries.items()
    ]
