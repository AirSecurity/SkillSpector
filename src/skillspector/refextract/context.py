# Marketplace addition (air/space) — NOT part of upstream NVIDIA SkillSpector.
# Licensed under Apache-2.0, consistent with the vendored SkillSpector source.

"""Where an extracted reference came from — the Context object every product embeds.

A Context bundles provenance (file + line), the *form* the reference appeared in
(RefForm — prose vs code block vs manifest, the blast-radius signal), and the
*file type* it lives in (FileType — markdown/python/shell/...), which is what
dataset-wide statistics group on.
"""

from __future__ import annotations

import contextvars
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum

from skillspector.refextract.paths import basename, suffix

# The skill currently being extracted. Set once at the root (extract_references) and
# read by context_for, so every Context is stamped without threading `skill` through
# each extractor. A ContextVar (not a plain global) keeps this correct under
# concurrency / reentrancy.
_current_skill: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "refextract_current_skill", default=None
)


@contextmanager
def current_skill(skill: str | None) -> Iterator[None]:
    """Set the skill stamped into every Context built within the block."""
    token = _current_skill.set(skill)
    try:
        yield
    finally:
        _current_skill.reset(token)


class RefForm(StrEnum):
    """The surrounding form of a reference occurrence (blast-radius signal)."""

    MD_PROSE = "md_prose"  # bare in markdown prose, no fetch verb nearby
    MD_FETCH_INSTRUCTION = "md_fetch_instruction"  # markdown prose with a fetch verb nearby
    MD_LINK = "md_link"  # inside markdown link syntax [text](url)
    MD_CODE_BLOCK = "md_code_block"  # inside a fenced/indented code block in markdown
    MD_JSON_VALUE = "md_json_value"  # a JSON-ish quoted value in markdown
    SCRIPT_FILE = "script_file"  # in a non-markdown script/text file
    MANIFEST = "manifest"  # in a dependency manifest (requirements.txt, package.json, ...)
    MCP_CONFIG = "mcp_config"  # in an MCP server config (.mcp.json / mcpServers block)


class FileType(StrEnum):
    """Canonical labels for common file types (used to normalize aliased extensions).

    These are the *known* normalizations only — there is no catch-all member.
    file_type_for() returns one of these for a recognized extension, and the raw
    extension (or filename) verbatim otherwise: unknown/anomalous types are
    preserved, never collapsed, so they remain visible to later analysis.
    """

    MARKDOWN = "markdown"
    PYTHON = "python"
    SHELL = "shell"
    JAVASCRIPT = "javascript"
    TYPESCRIPT = "typescript"
    JSON = "json"
    YAML = "yaml"
    TOML = "toml"
    TEXT = "text"
    RUBY = "ruby"
    GO = "go"
    RUST = "rust"
    HTML = "html"
    CSS = "css"
    SQL = "sql"


# Extension -> canonical FileType. Only genuine aliases are normalized (.yml/.yaml,
# .pyw/.py, ...); everything else keeps its own extension.
_EXT_TO_FILE_TYPE: dict[str, FileType] = {
    ".md": FileType.MARKDOWN, ".markdown": FileType.MARKDOWN,
    ".py": FileType.PYTHON, ".pyw": FileType.PYTHON,
    ".sh": FileType.SHELL, ".bash": FileType.SHELL, ".zsh": FileType.SHELL,
    ".js": FileType.JAVASCRIPT, ".mjs": FileType.JAVASCRIPT, ".cjs": FileType.JAVASCRIPT,
    ".jsx": FileType.JAVASCRIPT,
    ".ts": FileType.TYPESCRIPT, ".tsx": FileType.TYPESCRIPT,
    ".json": FileType.JSON,
    ".yaml": FileType.YAML, ".yml": FileType.YAML,
    ".toml": FileType.TOML,
    ".txt": FileType.TEXT,
    ".rb": FileType.RUBY,
    ".go": FileType.GO,
    ".rs": FileType.RUST,
    ".html": FileType.HTML, ".htm": FileType.HTML,
    ".css": FileType.CSS, ".scss": FileType.CSS,
    ".sql": FileType.SQL,
}


def file_type_for(path: str) -> str:
    """The file's type label: a canonical FileType for known extensions, else the
    raw extension (e.g. ``"lua"``, ``"ipynb"``) — or the filename for extensionless
    files (e.g. ``"dockerfile"``). Unknown types are preserved, never collapsed."""
    extension = suffix(path)
    known = _EXT_TO_FILE_TYPE.get(extension)
    if known is not None:
        return str(known)
    if extension:
        return extension.lstrip(".")  # preserve the real, unrecognized extension
    return basename(path).lower()  # extensionless: keep the filename (Dockerfile, LICENSE, ...)


@dataclass(frozen=True)
class Context:
    """Provenance of a single reference occurrence: which skill, file, line, form, type.

    ``skill`` lets a reference be traced back to its origin skill when many skills'
    products are pooled into one store. It is supplied by the caller (the scan knows
    its skill); the extractor itself is skill-agnostic, so it is None when unset.
    """

    file: str  # source file, relative to skill root
    line: int | None  # 1-based line; None when not line-bound (whole-file manifest parse)
    form: RefForm
    file_type: str  # canonical FileType label for known extensions, else the raw ext/filename
    skill: str | None = None  # origin skill id, for reverse-inspection in a pooled store

    def to_dict(self) -> dict[str, object]:
        return {
            "skill": self.skill,
            "file": self.file,
            "line": self.line,
            "form": str(self.form),
            "file_type": str(self.file_type),
        }


def context_for(path: str, line: int | None, form: RefForm) -> Context:
    """Build a Context for an occurrence, stamping the current skill (see current_skill)."""
    return Context(
        file=path, line=line, form=form, file_type=file_type_for(path), skill=_current_skill.get()
    )
