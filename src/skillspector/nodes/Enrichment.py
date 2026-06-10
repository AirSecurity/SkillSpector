# Marketplace addition (air/space) — NOT part of upstream NVIDIA SkillSpector.
# Licensed under Apache-2.0, consistent with the vendored SkillSpector source.

"""Enrichment node: enrich extracted references before the analyzer superstep.

Second of two sequential pre-analysis nodes (build_context -> refExtract ->
Enrichment -> analyzers). Intended to take the references surfaced by refExtract
and enrich them with additional context the analyzers can use.

Stub: implementation is pending. Currently a no-op that returns no state update,
so the graph wiring can be validated end-to-end.
"""

from __future__ import annotations

from skillspector.logging_config import get_logger
from skillspector.state import SkillspectorState

logger = get_logger(__name__)


def Enrichment(state: SkillspectorState) -> dict[str, object]:
    """Enrich the references extracted by refExtract.

    TODO: implement. For now a pass-through no-op (returns no state update).
    """
    logger.info("Enrichment: stub (no-op) — pending implementation")
    return {}
