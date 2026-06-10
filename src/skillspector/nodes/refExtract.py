# Marketplace addition (air/space) — NOT part of upstream NVIDIA SkillSpector.
# Licensed under Apache-2.0, consistent with the vendored SkillSpector source.

"""refExtract node: extract external references from the skill before analysis.

First of two sequential pre-analysis nodes (build_context -> refExtract ->
Enrichment -> analyzers). A thin orchestrator: it hands build_context's
file_cache to the standalone skillex library (https://github.com/AirSecurity/skillex)
and stores the resulting feed (state["references"]) for the Enrichment node to consume.

All extraction logic lives in skillex.refextract (one module + product object
per reference kind, stdlib-only) — extracted from this tree at 7e648b3; its
review guide, regression suite, and feature backlog live in that repo.

Not yet covered: instruction-file references (links a skill makes to other local
instruction/skill files).
"""

from __future__ import annotations

from skillspector.logging_config import get_logger
from skillex.refextract import extract_references
from skillspector.state import SkillspectorState

logger = get_logger(__name__)


def refExtract(state: SkillspectorState) -> dict[str, object]:
    """Extract the typed reference feed from the built scan context.

    Reads file_cache (path -> content) from build_context and writes
    state["references"] — the single feed the Enrichment node consumes.
    """
    file_cache: dict[str, str] = state.get("file_cache") or {}
    references = extract_references(file_cache)
    logger.info(
        "refExtract: %d unique products / %d reference sites across %d files (%s)",
        references.total_unique(),
        references.total_occurrences(),
        len(file_cache),
        ", ".join(f"{kind}={count}" for kind, count in references.counts().items()),
    )
    return {"references": references}
