# Marketplace addition (air/space) — NOT part of upstream NVIDIA SkillSpector.
# Licensed under Apache-2.0, consistent with the vendored SkillSpector source.

"""Tiny path helpers shared by the extractors (relative-path strings, no I/O)."""

from __future__ import annotations


def basename(path: str) -> str:
    """Final path component of a relative path string."""
    return path.rsplit("/", 1)[-1]


def suffix(path: str) -> str:
    """Lowercased file extension (with dot), or "" when there is none."""
    name = basename(path)
    dot = name.rfind(".")
    return name[dot:].lower() if dot > 0 else ""


def is_markdown(path: str) -> bool:
    return suffix(path) in {".md", ".markdown"}
