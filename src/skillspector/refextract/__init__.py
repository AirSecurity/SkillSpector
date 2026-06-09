# Marketplace addition (air/space) — NOT part of upstream NVIDIA SkillSpector.
# Licensed under Apache-2.0, consistent with the vendored SkillSpector source.

"""refextract — extract external references from a skill's source files.

Self-contained (stdlib only) so other repositories can reuse it: feed it a
``{relative-path: file-content}`` mapping and get back typed reference products.

    from skillspector.refextract import extract_references
    refs = extract_references(file_cache)
    refs.urls, refs.repos, refs.packages, refs.domains

Each product kind has its own module and dataclass (url.Url, repo.Repo,
package.Package, domain.Domain, mcp.Mcp). URLs, packages, and MCP servers are
read from files (extract_*); repos and domains are derived from URL-bearing
products (derive_*). Every product carries a Context (file, line, form,
file_type) describing where it was found.
"""

from __future__ import annotations

from collections.abc import Mapping

from skillspector.refextract.context import Context, FileType, RefForm, current_skill
from skillspector.refextract.domain import Domain, derive_domains
from skillspector.refextract.fetchexec import ExecPattern, FetchExecute, extract_fetch_executes
from skillspector.refextract.host import ServiceFamily, TakeoverPlatform
from skillspector.refextract.mcp import Mcp, Transport, extract_mcps
from skillspector.refextract.occurrence import Occurrence
from skillspector.refextract.package import Package, extract_packages
from skillspector.refextract.references import ExtractedReferences
from skillspector.refextract.repo import Repo, derive_repos
from skillspector.refextract.url import Url, extract_urls

__all__ = [
    "Context",
    "current_skill",
    "Occurrence",
    "RefForm",
    "FileType",
    "ServiceFamily",
    "TakeoverPlatform",
    "Transport",
    "ExecPattern",
    "Url",
    "Repo",
    "Package",
    "Domain",
    "Mcp",
    "FetchExecute",
    "ExtractedReferences",
    "extract_urls",
    "extract_packages",
    "extract_mcps",
    "extract_fetch_executes",
    "derive_repos",
    "derive_domains",
    "extract_references",
]


def extract_references(files: Mapping[str, str], skill: str | None = None) -> ExtractedReferences:
    """Extract every reference product from one skill's {relative-path: content} map.

    URLs and MCP servers are extracted first; repos and domains are derived from
    every URL-bearing product (URLs + MCP remote endpoints), so coverage doesn't
    depend on which extractor happened to see a file. Packages are independent.

    ``skill`` is the origin skill id for this whole call; it is stamped into every
    Context (so a pooled multi-skill store stays reverse-inspectable). Pool many
    skills by calling once per skill and combining the feeds however you store them.
    None (the default) keeps the extractor fully skill-agnostic.
    """
    with current_skill(skill):
        urls = extract_urls(files)
        mcps = extract_mcps(files)
        return ExtractedReferences(
            urls=tuple(urls),
            repos=tuple(derive_repos(urls, mcps)),
            packages=tuple(extract_packages(files)),
            domains=tuple(derive_domains(urls, mcps)),
            mcps=tuple(mcps),
            fetch_executes=tuple(extract_fetch_executes(files)),
        )
