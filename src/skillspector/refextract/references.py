# Marketplace addition (air/space) — NOT part of upstream NVIDIA SkillSpector.
# Licensed under Apache-2.0, consistent with the vendored SkillSpector source.

"""ExtractedReferences — the single feed grouping every reference product.

One typed list per product kind. This is what refExtract writes to graph state
and what the Enrichment node consumes.
"""

from __future__ import annotations

from dataclasses import dataclass

from skillspector.refextract.domain import Domain
from skillspector.refextract.fetchexec import FetchExecute
from skillspector.refextract.mcp import Mcp
from skillspector.refextract.package import Package
from skillspector.refextract.repo import Repo
from skillspector.refextract.url import Url


@dataclass(frozen=True)
class ExtractedReferences:
    """All external references a skill points at, grouped by product kind."""

    urls: tuple[Url, ...] = ()
    repos: tuple[Repo, ...] = ()
    packages: tuple[Package, ...] = ()
    domains: tuple[Domain, ...] = ()
    mcps: tuple[Mcp, ...] = ()
    fetch_executes: tuple[FetchExecute, ...] = ()

    def counts(self) -> dict[str, int]:
        """Unique products per kind — deduped kinds (repo/package/domain/mcp) count
        distinct resources, occurrence-level kinds (url/fetch_execute) count sites."""
        return {
            "url": len(self.urls),
            "repo": len(self.repos),
            "package": len(self.packages),
            "domain": len(self.domains),
            "mcp": len(self.mcps),
            "fetch_execute": len(self.fetch_executes),
        }

    def occurrence_counts(self) -> dict[str, int]:
        """Reference *sites* per kind — every occurrence of the deduped kinds;
        url/fetch_execute are already one product per site."""
        return {
            "url": len(self.urls),
            "repo": sum(len(repo.occurrences) for repo in self.repos),
            "package": sum(len(package.occurrences) for package in self.packages),
            "domain": sum(len(domain.occurrences) for domain in self.domains),
            "mcp": sum(len(mcp.occurrences) for mcp in self.mcps),
            "fetch_execute": len(self.fetch_executes),
        }

    def total_unique(self) -> int:
        """Distinct products across all kinds (a repo cited 50 times counts once)."""
        return sum(self.counts().values())

    def total_occurrences(self) -> int:
        """Reference sites across all kinds (a repo cited 50 times counts 50).

        Kinds overlap: repos/domains derive from urls, fetch_executes and MCP
        endpoints share lines with their urls — so one physical site can count
        several times here. Use external_site_count() for the deduped answer.
        """
        return sum(self.occurrence_counts().values())

    def external_site_count(self) -> int:
        """Number of distinct places — (file, line) — referencing something external.

        Every kind's occurrences collapse onto their site, so a github URL that is
        also a Repo and a Domain counts once. A whole-file manifest parse
        (line None) is one place.
        """
        sites: set[tuple[str, int | None]] = {
            (url.context.file, url.context.line) for url in self.urls
        }
        sites.update(
            (fetch_execute.context.file, fetch_execute.context.line)
            for fetch_execute in self.fetch_executes
        )
        for product in (*self.repos, *self.packages, *self.domains, *self.mcps):
            sites.update(
                (occurrence.context.file, occurrence.context.line)
                for occurrence in product.occurrences
            )
        return len(sites)

    def to_dict(self) -> dict[str, object]:
        return {
            "urls": [url.to_dict() for url in self.urls],
            "repos": [repo.to_dict() for repo in self.repos],
            "packages": [package.to_dict() for package in self.packages],
            "domains": [domain.to_dict() for domain in self.domains],
            "mcps": [mcp.to_dict() for mcp in self.mcps],
            "fetch_executes": [fe.to_dict() for fe in self.fetch_executes],
        }
