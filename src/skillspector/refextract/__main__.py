# Marketplace addition (air/space) — NOT part of upstream NVIDIA SkillSpector.
# Licensed under Apache-2.0, consistent with the vendored SkillSpector source.

"""CLI: ``python -m skillspector.refextract <dir>`` -> extracted products as JSON.

If <dir> holds skill subdirectories, each top-level subdir is extracted as its own
skill (skill id = subdir name) and the output is a ``{skill: products}`` map — one
independent feed per skill, not merged. If <dir> has no subdirs, it is treated as a
single skill and the bare products feed is emitted. Stdlib-only, so the package
stays reusable.
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

    skill_dirs = sorted(child for child in root.iterdir() if child.is_dir())
    if skill_dirs:
        # One skill per subdir, files relative to the subdir; output a per-skill map.
        result: object = {
            d.name: extract_references(_files_under(d), skill=d.name).to_dict()
            for d in skill_dirs
        }
    else:
        result = extract_references(_files_under(root), skill=root.name).to_dict()

    json.dump(result, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
