# Marketplace addition (air/space) — NOT part of upstream NVIDIA SkillSpector.
# Licensed under Apache-2.0, consistent with the vendored SkillSpector source.

"""CLI: ``python -m skillspector.refextract <dir>`` -> extracted products as JSON.

If <dir> contains any file directly (SKILL.md, README, ...), it is one skill —
real skills routinely have scripts/ or references/ subdirs, which must not be
mis-split into fake per-subdir skills (and the root files silently dropped).
Only a directory of *only* subdirectories is treated as a multi-skill root: each
top-level subdir is extracted as its own skill (skill id = subdir name) and the
output is a ``{skill: products}`` map — one independent feed per skill, not
merged. Stdlib-only, so the package stays reusable.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from skillspector.refextract import extract_references


def _files_under(directory: Path) -> dict[str, str]:
    """{path-relative-to-directory: content} for every file under directory."""
    return {
        str(path.relative_to(directory)): path.read_text(errors="replace")
        for path in directory.rglob("*")
        if path.is_file()
    }


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: python -m skillspector.refextract <dir>", file=sys.stderr)
        return 2
    root = Path(args[0])
    if not root.is_dir():
        print(f"not a directory: {root}", file=sys.stderr)
        return 2

    children = list(root.iterdir())
    skill_dirs = sorted(child for child in children if child.is_dir())
    has_direct_files = any(child.is_file() for child in children)
    if skill_dirs and not has_direct_files:
        # A root of only subdirs: one skill per subdir, files relative to the
        # subdir; output a per-skill map.
        result: object = {
            skill_dir.name: extract_references(_files_under(skill_dir), skill=skill_dir.name).to_dict()
            for skill_dir in skill_dirs
        }
    else:
        # Any direct file ⇒ a single skill (its subdirs are part of the skill).
        result = extract_references(_files_under(root), skill=root.name).to_dict()

    json.dump(result, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
