# Marketplace addition (air/space) — NOT part of upstream NVIDIA SkillSpector.
# Licensed under Apache-2.0, consistent with the vendored SkillSpector source.

"""Markdown structural classification shared by the URL and package extractors.

One fence scanner and one line-form classifier, so ``form`` means the same thing
across every product kind instead of being re-derived (and diverging) per
extractor. URL extraction layers the URL-shape-specific forms (MD_LINK,
MD_JSON_VALUE) on top of what markdown_form returns; those don't apply to other
kinds. Ported from classify_url_context.py.
"""

from __future__ import annotations

import re

from skillspector.refextract.context import RefForm

# Fetch-instruction verbs/phrases looked for in the ±3 lines around an occurrence.
# Narrow on purpose — broad words like "see" alone are lossy. A match means a
# fetch verb is *in the neighborhood*, not that the reference is definitely fetched.
_FETCH_PATTERNS = [
    r"\bread\b",
    r"\bfetch(?:ed|es|ing)?\b",
    r"\bconsult\b",
    r"\bload(?:ed|s|ing)?\b",
    r"\bdownload(?:ed|s|ing)?\b",
    r"\bretriev(?:e|ed|es|ing)\b",
    r"\bvisit\b",
    r"\bopen\b",
    r"\bbrowse\b",
    r"\bnavigate\b",
    r"\binstruction(?:s)?\b",
    r"\binstruct(?:ed|ing)?\b",
    r"\binstall(?:ed|ing|ation|er|ers)?\b",
    r"\bdocumentation\b",
    r"\bdocs\b",
    r"\bset\s?up\b",
    r"\bconfigur(?:e|ed|es|ing|ation)\b",
    r"\bguide(?:s|line|lines|d|ance)?\b",
    r"\btutorial(?:s)?\b",
    r"\bmanual\b",
    r"\breadme\b",
    r"\bspec(?:ification)?\b",
    r"\bquick\s?start\b",
    r"\bget(?:ting)? started\b",
    r"\busage\b",
    r"\bexample(?:s)?\b",
    r"\bhow[\s-]to\b",
    r"\brefer(?:ence|ring|red)? (?:to|the)\b",
    r"\breference(?:s)?\b",
    r"\blearn more\b",
    r"\bfor more (?:info|information|details|context)\b",
    r"\breview the (?:docs|documentation|guide|reference|spec|specification)\b",
    r"\bsee (?:the )?(?:docs|documentation|guide|reference|spec|specification|details|example|examples|notes|notes? (?:at|on|in)|below|above)\b",
    r"\bfollow (?:the )?(?:instructions?|guide|steps?|tutorial|link)\b",
    r"\b(?:full|complete|further|more) (?:docs|documentation|details|reference|info|information) (?:at|on|here)\b",
    r"\b(?:from|at) (?:the )?(?:url|link|endpoint)\b",
    r"\bget(?:s|ting)? from\b",
    r"\bcheck (?:the )?(?:docs|documentation|reference)\b",
    r"\bsource:\s*$",
    r"\bdocumentation:\s*$",
    r"\bdocs?:\s*$",
    r"\breference:\s*$",
    r"\binstall(?:ation)?:\s*$",
    r"\binstructions?:\s*$",
    r"\bsetup:\s*$",
    r"\bstep \d+[\.:]?\s*$",
]
_FETCH_RX = [re.compile(pattern, re.IGNORECASE | re.MULTILINE) for pattern in _FETCH_PATTERNS]


def fence_map(lines: list[str]) -> list[bool]:
    """Per-line "inside a fenced code block" flags (``` or ~~~ toggles)."""
    in_fence = False
    flags: list[bool] = []
    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
        flags.append(in_fence)
    return flags


def is_code_line(line: str, in_fence: bool) -> bool:
    """True when a markdown line is code: fenced, or indented (CommonMark)."""
    return in_fence or line.startswith("    ") or line.startswith("\t")


def has_fetch_context(lines: list[str], line_index: int) -> bool:
    """True when a fetch-instruction verb appears within ±3 lines of line_index."""
    window = "\n".join(lines[max(0, line_index - 3):min(len(lines), line_index + 4)])
    return any(rx.search(window) for rx in _FETCH_RX)


def markdown_form(lines: list[str], fence_flags: list[bool], line_index: int) -> RefForm:
    """Structural form of a markdown line: code block, fetch-instruction, or prose.

    The single source of truth for markdown form. Code-block dominates; otherwise
    a nearby fetch verb makes it MD_FETCH_INSTRUCTION, else MD_PROSE.
    """
    if is_code_line(lines[line_index], fence_flags[line_index]):
        return RefForm.MD_CODE_BLOCK
    if has_fetch_context(lines, line_index):
        return RefForm.MD_FETCH_INSTRUCTION
    return RefForm.MD_PROSE
