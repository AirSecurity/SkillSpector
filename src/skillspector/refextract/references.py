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

    def total(self) -> int:
        """Total number of references across all product kinds."""
        return (
            len(self.urls) + len(self.repos) + len(self.packages)
            + len(self.domains) + len(self.mcps) + len(self.fetch_executes)
        )

    def counts(self) -> dict[str, int]:
        """Per-kind reference counts."""
        return {
            "url": len(self.urls),
            "repo": len(self.repos),
            "package": len(self.packages),
            "domain": len(self.domains),
            "mcp": len(self.mcps),
            "fetch_execute": len(self.fetch_executes),
        }

    def to_dict(self) -> dict[str, object]:
        return {
            "urls": [url.to_dict() for url in self.urls],
            "repos": [repo.to_dict() for repo in self.repos],
            "packages": [package.to_dict() for package in self.packages],
            "domains": [domain.to_dict() for domain in self.domains],
            "mcps": [mcp.to_dict() for mcp in self.mcps],
            "fetch_executes": [fe.to_dict() for fe in self.fetch_executes],
        }
