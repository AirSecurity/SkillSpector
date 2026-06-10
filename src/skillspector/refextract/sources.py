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
    """Yield (url, context) for every product in the given groups that has a URL.

    Later groups are *fallback coverage*: an Mcp's remote endpoint usually also
    appears as a Url from the same config file (extract_urls scans every file),
    and both describe one physical declaration — so a later group's (file, url)
    already yielded by an earlier group is skipped rather than double-counted.
    Repeats *within* a group are distinct reference sites and are all kept.
    """
    yielded_by_earlier_groups: set[tuple[str, str]] = set()
    for group in groups:
        yielded_by_this_group: set[tuple[str, str]] = set()
        for item in group:
            if not item.url:
                continue
            file_url = (item.context.file, item.url)
            if file_url in yielded_by_earlier_groups:
                continue
            yielded_by_this_group.add(file_url)
            yield (item.url, item.context)
        yielded_by_earlier_groups |= yielded_by_this_group
