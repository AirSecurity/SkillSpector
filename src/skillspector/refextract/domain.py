# Marketplace addition (air/space) — NOT part of upstream NVIDIA SkillSpector.
# Licensed under Apache-2.0, consistent with the vendored SkillSpector source.

"""Apex-domain references — the Domain product and derive_domains().

Derived from every URL-bearing product (a Url, or an Mcp's remote endpoint), one
Domain per distinct apex, so a Domain inherits the context of the first URL that
referenced it and carries the takeover signal for that apex. Ported from
extract_apex_domains.py.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from urllib.parse import urlparse

from skillspector.refextract.host import (
    ServiceFamily,
    TakeoverPlatform,
    apex_domain,
    is_internal_host,
    is_templated,
    service_family,
    takeover_platform,
)
from skillspector.refextract.mcp import Mcp
from skillspector.refextract.occurrence import Occurrence
from skillspector.refextract.sources import url_sources
from skillspector.refextract.url import Url


@dataclass(frozen=True)
class Domain:
    """An apex domain (eTLD+1) a skill points at, with every site that referenced it."""

    apex: str
    service_family: ServiceFamily
    takeover_platform: TakeoverPlatform
    occurrences: tuple[Occurrence, ...]
    is_template: bool = False  # apex itself is a placeholder (e.g. from https://api.<region>...)

    def to_dict(self) -> dict[str, object]:
        return {
            "apex": self.apex,
            "service_family": str(self.service_family),
            "takeover_platform": str(self.takeover_platform),
            "is_template": self.is_template,
            "occurrences": [occurrence.to_dict() for occurrence in self.occurrences],
        }


def derive_domains(urls: Iterable[Url], mcps: Iterable[Mcp] = ()) -> list[Domain]:
    """Derive apex-domain references from every URL-bearing product.

    One Domain per distinct apex; every reference site is kept as an Occurrence.
    """
    occurrences_by_apex: dict[str, list[Occurrence]] = {}
    for source_url, context in url_sources(urls, mcps):
        try:
            host = (urlparse(source_url).hostname or "").lower()
        except ValueError:
            continue
        if is_internal_host(host):
            continue
        apex = apex_domain(host)
        if not apex or "." not in apex:
            continue
        occurrences_by_apex.setdefault(apex, []).append(
            Occurrence(context=context, source_url=source_url)
        )
    return [
        Domain(
            apex=apex,
            service_family=service_family(apex),
            takeover_platform=takeover_platform(apex),
            occurrences=tuple(occurrences),
            is_template=is_templated(apex),
        )
        for apex, occurrences in occurrences_by_apex.items()
    ]
