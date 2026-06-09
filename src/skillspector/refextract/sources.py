# Marketplace addition (air/space) — NOT part of upstream NVIDIA SkillSpector.
# Licensed under Apache-2.0, consistent with the vendored SkillSpector source.

"""Iterate (url, context) over every URL-bearing product.

Repos and domains are *derived* from the URLs a skill references — and those
URLs live on more than one product kind (a Url, but also an Mcp's remote
endpoint). This is the single place that enumerates URL-bearing products, so
derivation coverage never depends on which extractor happened to also see a file.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Protocol

from skillspector.refextract.context import Context


class UrlBearing(Protocol):
    """Structural type for any product that carries a URL + its context."""

    url: str | None
    context: Context


def url_sources(*groups: Iterable[UrlBearing]) -> Iterator[tuple[str, Context]]:
    """Yield (url, context) for every product in the given groups that has a URL."""
    for group in groups:
        for item in group:
            if item.url:
                yield (item.url, item.context)
