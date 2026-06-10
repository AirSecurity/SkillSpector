# Marketplace addition (air/space) — NOT part of upstream NVIDIA SkillSpector.
# Licensed under Apache-2.0, consistent with the vendored SkillSpector source.

"""Occurrence — one place a deduped resource was referenced.

A Url is itself an occurrence (one per file+line), but a Domain / Repo / Package
is a deduped *resource* that a skill can reference from many places. Each such
product keeps the full list of occurrences so the resource->where-it-came-from
join (how many files/lines/skills point at evil.io) is never discarded.
"""

from __future__ import annotations

from dataclasses import dataclass

from skillspector.refextract.context import Context


@dataclass(frozen=True)
class Occurrence:
    """One reference site of a deduped resource."""

    context: Context
    source_url: str | None = None  # URL that yielded a derived ref (domain/repo); None for packages
    # Version/constraint exactly as written at this site (packages: ==2.31.0, ^18.2,
    # 1.25-alpine, sha256:…). Verbatim on purpose — a constraint is information; the
    # *canonical* name lives on the product, the *pin* lives where it was written.
    version: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "context": self.context.to_dict(),
            "source_url": self.source_url,
            "version": self.version,
        }
