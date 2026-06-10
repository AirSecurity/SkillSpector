# Marketplace addition (air/space) — NOT part of upstream NVIDIA SkillSpector.
# Licensed under Apache-2.0, consistent with the vendored SkillSpector source.

"""Package references — the Package product and extract_packages().

Ported from extract_packages.py: inline install commands (pip/npm/cargo/brew/...)
across any text file, plus dependency-manifest parsers (requirements.txt,
package.json, pyproject.toml, Cargo.toml, Gemfile, go.mod, Pipfile, Brewfile,
Dockerfile). Context records where the reference lives: MANIFEST for manifests,
and for inline installs the markdown form (code block vs prose) or SCRIPT_FILE.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from typing import NamedTuple

from skillspector.refextract.context import Context, RefForm, context_for
from skillspector.refextract.markdown import fence_map, markdown_form
from skillspector.refextract.occurrence import Occurrence
from skillspector.refextract.paths import basename, is_markdown


@dataclass(frozen=True)
class Package:
    """A registry package a skill installs or depends on, with every reference site."""

    manager: str  # pip / npm / cargo / brew / go / ...
    name: str  # canonical, version-free (the version lives on each Occurrence)
    occurrences: tuple[Occurrence, ...]  # each occurrence's source_url is None (not URL-derived)

    @property
    def slug(self) -> str:
        """The canonical "manager:name" identifier."""
        return f"{self.manager}:{self.name}"

    @property
    def versions(self) -> tuple[str, ...]:
        """Distinct versions/constraints across occurrences, in first-seen order."""
        return tuple(dict.fromkeys(
            occurrence.version for occurrence in self.occurrences if occurrence.version
        ))

    def to_dict(self) -> dict[str, object]:
        return {
            "manager": self.manager,
            "name": self.name,
            "slug": self.slug,
            "versions": list(self.versions),
            "occurrences": [occurrence.to_dict() for occurrence in self.occurrences],
        }


# ── inline shell install commands; capture group 1 = package name(s) ─────────
_INLINE_PATTERNS = [
    ("pip", re.compile(r"\b(?:pip3?|pipx|uv\s+pip)\s+install\s+(?:--[\w-]+(?:=\S+)?\s+)*((?:[^|&;<>`]|[<>]=)+)", re.I)),
    ("pip", re.compile(r"\b(?:poetry|pdm)\s+add\s+(?:--[\w-]+(?:=\S+)?\s+)*((?:[^|&;<>`]|[<>]=)+)", re.I)),
    ("conda", re.compile(r"\bconda\s+install\s+(?:-c\s+\S+\s+)?(?:--[\w-]+(?:=\S+)?\s+)*((?:[^|&;<>`]|[<>]=)+)", re.I)),
    ("npm", re.compile(r"\b(?:npm|yarn|pnpm|bun)\s+(?:install|add|i)\s+(?:--[\w-]+(?:=\S+)?\s+|-[\w]+\s+)*((?:[^|&;<>`]|[<>]=)+)", re.I)),
    ("npx", re.compile(r"\bnpx\s+(?:-{1,2}[\w-]+(?:=\S+)?\s+)*([^\s|&;<>`]+)", re.I)),
    ("brew", re.compile(r"\bbrew\s+(?:install|tap|cask\s+install)\s+(?:--[\w-]+(?:=\S+)?\s+)*((?:[^|&;<>`]|[<>]=)+)", re.I)),
    ("apt", re.compile(r"\bapt(?:-get)?\s+install\s+(?:-y\s+)?(?:--[\w-]+(?:=\S+)?\s+)*((?:[^|&;<>`]|[<>]=)+)", re.I)),
    ("yum/dnf", re.compile(r"\b(?:yum|dnf)\s+install\s+(?:-y\s+)?((?:[^|&;<>`]|[<>]=)+)", re.I)),
    ("pacman", re.compile(r"\bpacman\s+-S(?:y+u?)?\s+(?:--[\w-]+\s+)*((?:[^|&;<>`]|[<>]=)+)", re.I)),
    ("cargo", re.compile(r"\bcargo\s+(?:install|add)\s+(?:--[\w-]+(?:=\S+)?\s+)*((?:[^|&;<>`]|[<>]=)+)", re.I)),
    ("gem", re.compile(r"\bgem\s+install\s+(?:--[\w-]+(?:=\S+)?\s+)*((?:[^|&;<>`]|[<>]=)+)", re.I)),
    ("go", re.compile(r"\bgo\s+(?:install|get)\s+(?:-{1,2}[\w-]+(?:=\S+)?\s+)*([^\s|&;<>`]+)", re.I)),
]

# docker is handled apart from _INLINE_PATTERNS: flag/value grammar (`-e KEY img`)
# can't be expressed safely in a single capture, so the segment after the verb is
# tokenized and walked by _docker_image — the same walker launcher_package uses.
_DOCKER_INLINE_RE = re.compile(r"\bdocker\s+(?:pull|run)\s+([^|&;<>`]+)", re.I)

_NOT_A_PACKAGE = re.compile(r"^(?:-|\.|`|\"|'|>|\||\\|--|\$|&|;|\?|\*|<|{|\[|\(|\)|\]|})", re.I)
# Common English words that a greedy capture pulls out of prose like "install X from
# the official site". Filtering them removes high-volume noise; the rare cost is a
# package literally named one of these in an install command.
_STOPWORDS = {
    "install", "add", "-y", "-g", "--save", "--save-dev", "--global",
    "from", "into", "with", "this", "the", "that", "a", "an", "via",
    "using", "use", "for", "to", "at", "it", "our",
}


def _clean_inline(token_blob: str) -> list[str]:
    """Split a captured install-command token blob into candidate tokens, dropping
    structural shell noise (flags, separators, requirement-file args) and common-word
    prose stopwords."""
    cleaned = []
    for token in re.split(r"[\s,]+", token_blob.strip()):
        # Strip wrapper punctuation to a fixpoint: trailing prose like `'pkg'.`
        # interleaves quote and period, so one strip pass isn't enough. Braces and
        # brackets are NOT in the set — they're placeholder markers ({package},
        # [name]) that the guards below must still see, else `pip install {package}`
        # fabricates a package literally named "package".
        while True:
            stripped = token.strip(" \t\"'`,;()").rstrip(".")
            if stripped == token:
                break
            token = stripped
        if not token or _NOT_A_PACKAGE.match(token) or token.startswith("-"):
            continue
        if token.lower() in _STOPWORDS or token.endswith(".txt") or token.endswith(".lock"):
            continue
        cleaned.append(token)
    return cleaned


def split_package_token(token: str) -> tuple[str | None, str | None]:
    """Split an install token into (canonical name, verbatim version), either None.

    The name is version-free (``requests==2.31.0`` -> ``requests``, ``foo[extra]``
    -> ``foo``, ``@scope/pkg@1.2`` -> ``@scope/pkg``) so identical packages dedup
    and join regardless of pin; the version is kept exactly as written
    (``==2.31.0``, ``>=2.0,<3``, ``1.2``) — the pin is real information. VCS refs
    (``git+https://...``, ``git@...``) are kept whole with no version — any pin is
    part of the ref. Template placeholders and local paths are not packages; a
    bare http(s) URL download is captured by the URL extractor instead.
    """
    if token.startswith(("$", "${", "{", "<")):
        return None, None
    if token.startswith(("./", "../", "/")):
        return None, None
    if token.startswith(("http://", "https://")):
        return None, None
    if token.startswith(("git+", "git@")) or "://" in token:
        return token, None
    # Extras are neither name nor version; `]?` tolerates an extras group truncated
    # by comma-splitting (`pkg[a,b]` arrives as `pkg[a`); a `]` with no `[` before
    # it is the other half of that split (`b]==1`).
    token = re.sub(r"\[[^\]]*\]?", "", token, count=1)
    if "]" in token and "[" not in token.split("]", 1)[0]:
        token = token.replace("]", "", 1)
    operator = re.search(r"[<>=!~]", token)  # PEP 508 / range specifiers
    if operator:
        name, version = token[:operator.start()], token[operator.start():]
    elif "@" in token[1:]:  # name@1.2.3 or @scope/name@1.2.3 — trailing @version
        name = token[0] + token[1:].rsplit("@", 1)[0]
        version = token[1:].rsplit("@", 1)[1]
    else:
        name, version = token, None
    if not name or any(marker in name for marker in "{}<>"):
        return None, None  # leftover placeholder marker — never a real name
    return name, version or None


def normalize_package_name(token: str) -> str | None:
    """The canonical, version-free package name of an install token (see
    split_package_token), or None if the token isn't a package."""
    return split_package_token(token)[0]


def _split_docker_image(image: str) -> tuple[str | None, str | None]:
    """Split a docker image ref into (name, pin) — pin = tag and/or digest, verbatim.

    ``nginx:1.25`` -> (nginx, 1.25); ``img@sha256:x`` -> (img, sha256:x);
    ``img:tag@sha256:x`` -> (img, tag@sha256:x). A colon before the last ``/`` is
    a registry port (``localhost:5000/img``), not a tag.
    """
    base, _, digest = image.partition("@")
    colon = base.rfind(":")
    if colon > base.rfind("/"):
        name, tag = base[:colon], base[colon + 1:]
    else:
        name, tag = base, ""
    if tag and digest:
        return name or None, f"{tag}@{digest}"
    return name or None, digest or tag or None


# ── launcher commands (npx/uvx/docker/... running a package) ─────────────────
# Owned here so "which package does this command reference" has one home; the MCP
# extractor calls launcher_package for its command/args cross-link.

# Launcher command -> package manager whose package it runs.
_RUNNER_TO_MANAGER = {
    "npx": "npm", "bunx": "npm", "pnpm": "npm", "yarn": "npm", "dlx": "npm",
    "uvx": "pip", "uv": "pip", "pipx": "pip", "pip": "pip", "pip3": "pip",
}
# Sub-tokens to skip when finding the package argument after a launcher.
_RUNNER_SKIP_ARGS = {
    "-y", "--yes", "-q", "--quiet", "run", "tool", "exec", "dlx",
    "install", "add", "-p", "--package",
}
# docker run flags that consume the FOLLOWING token as their value — so that
# value isn't mistaken for the image (e.g. `-e API_KEY img`, `-v /a:/b img`).
_DOCKER_VALUE_FLAGS = {
    "-e", "--env", "-v", "--volume", "--mount", "-p", "--publish", "-w", "--workdir",
    "--name", "--network", "--net", "-u", "--user", "--entrypoint", "--platform",
    "-l", "--label", "--add-host", "--device", "--cap-add", "--cap-drop",
    "--gpus", "-m", "--memory", "--memory-swap", "--cpus", "--shm-size", "--restart",
    "-h", "--hostname", "--ip", "--dns", "--expose", "--env-file", "--label-file",
    "--link", "--volumes-from", "--pid", "--ipc", "--runtime", "--pull",
    "--security-opt", "--tmpfs", "--ulimit", "--log-driver", "--stop-signal", "--health-cmd",
}


def _command_basename(command: str) -> str:
    """Bare executable name (strip any directory + .exe)."""
    return command.rsplit("/", 1)[-1].rsplit("\\", 1)[-1].removesuffix(".exe").lower()


def _docker_image(tokens: Iterable[str]) -> str | None:
    """First token of a docker run/pull argument list that is the image (not a
    flag, not a flag's value, not the run/pull verb itself)."""
    skip_value = False
    for token in tokens:
        if skip_value:  # this token is a flag's value, not the image
            skip_value = False
            continue
        if token in ("run", "pull"):
            continue
        if token.startswith("-"):
            skip_value = token in _DOCKER_VALUE_FLAGS
            continue
        return token
    return None


def launcher_package(command: str | None, args: tuple[str, ...]) -> str | None:
    """Derive a "manager:name" package slug from a launcher command + its args.

    The name half goes through normalize_package_name — the same normalization
    extract_packages applies — so the slug joins against Package.slug by value
    (``npx -y pkg@1.2`` -> ``npm:pkg``, exactly what the Package side records).
    """
    if not command:
        return None
    executable = _command_basename(command)
    if executable == "docker":
        image = _docker_image(args)
        if image is None:
            return None
        image_name, _version = _split_docker_image(image)
        return f"docker:{image_name}" if image_name else None
    manager = _RUNNER_TO_MANAGER.get(executable)
    if not manager:
        return None  # node/python/bash/... launch a local script, not a package
    for arg in args:
        if arg.lower() in _RUNNER_SKIP_ARGS or arg.startswith("-"):
            continue
        if manager == "pip" and arg.lower().endswith((".py", ".sh")):
            continue  # `uv run script.py` runs a local file, not a registry package
        package_name = normalize_package_name(arg)
        if package_name:
            return f"{manager}:{package_name}"
    return None


# ── manifest parsers ─────────────────────────────────────────────────────────
class _ManifestEntry(NamedTuple):
    """One package declared in a manifest/lockfile — the single parser contract.

    Every parser returns list[_ManifestEntry]; no parser has its own shape.
    ``version`` is the version/constraint exactly as written, when present.
    ``manager`` overrides the manifest's default manager (unused today; the
    Dockerfile parser's images get their manager from _manifest_handler).
    """

    name: str
    version: str | None = None
    manager: str | None = None


_ManifestParser = Callable[[str], list[_ManifestEntry]]


def _toml_dependency_entries(
    section_text: str, exclude: frozenset[str] = frozenset()
) -> list[_ManifestEntry]:
    """name = "spec" / name = { version = "spec", ... } lines of a TOML dep table
    (pyproject poetry, Cargo.toml, Pipfile)."""
    entries = []
    for line in section_text.splitlines():
        match = re.match(r"^([A-Za-z0-9_.\-]+)\s*=\s*(.*)$", line.strip())
        if not match or match.group(1).lower() in exclude:
            continue
        rest = match.group(2)
        if rest.startswith("{"):
            version_match = re.search(r"version\s*=\s*[\"']([^\"']+)[\"']", rest)
        else:
            version_match = re.match(r"[\"']([^\"']+)[\"']", rest)
        entries.append(_ManifestEntry(match.group(1), version_match.group(1) if version_match else None))
    return entries


def _requirement_string_entries(blob: str) -> list[_ManifestEntry]:
    """Quoted PEP 508 requirement strings anywhere in a blob (pyproject dep lists,
    setup.py kwargs) -> entries; env markers after ';' aren't version."""
    entries = []
    for token in re.findall(r"[\"']([^\"']+)[\"']", blob):
        name, version = split_package_token(token.split(";")[0].strip())
        if name:
            entries.append(_ManifestEntry(name, version))
    return entries


def _parse_requirements_txt(text: str) -> list[_ManifestEntry]:
    entries = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith(("-r", "-c", "--requirement", "--constraint")):
            continue  # includes another requirements/constraints file, not a package
        if line.startswith(("-e ", "--editable ")):
            line = line.split(None, 1)[1].strip() if " " in line else ""
        # VCS / URL requirement: prefer the explicit #egg=NAME, else keep the ref whole
        # (so an editable git install yields "foo" / the repo, never a bogus "git").
        egg = re.search(r"[#&]egg=([A-Za-z0-9_.\-]+)", line)
        if egg:
            entries.append(_ManifestEntry(egg.group(1)))
            continue
        if line.startswith(("git+", "hg+", "svn+", "bzr+")) or "://" in line:
            entries.append(_ManifestEntry(line.split()[0]))
            continue
        line = re.sub(r"\s+#.*", "", line)  # strip trailing inline comment only
        match = re.match(r"^([A-Za-z0-9_][A-Za-z0-9_.\-]*)", line)  # name must start alnum/_
        if match:
            # the spec after the name (extras and env markers aren't version)
            spec = re.sub(r"^\[[^\]]*\]", "", line[match.end():]).split(";")[0].strip()
            entries.append(_ManifestEntry(match.group(1), spec or None))
    return entries


def _parse_package_json(text: str) -> list[_ManifestEntry]:
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        return []
    if not isinstance(parsed, dict):
        return []
    entries = []
    for key in (
        "dependencies", "devDependencies", "peerDependencies", "optionalDependencies",
        "bundledDependencies", "bundleDependencies",
    ):
        section = parsed.get(key) or {}
        if isinstance(section, dict):  # values are the version ranges — keep them
            entries.extend(
                _ManifestEntry(name, spec if isinstance(spec, str) else None)
                for name, spec in section.items()
            )
        elif isinstance(section, list):  # bundled* may be a list of names
            entries.extend(_ManifestEntry(str(name)) for name in section)
    return entries


def _parse_pyproject(text: str) -> list[_ManifestEntry]:
    entries = []
    project = re.search(r"(?ms)^\[project\][^\[]*?^dependencies\s*=\s*\[(.*?)\]", text)
    if project:
        entries.extend(_requirement_string_entries(project.group(1)))
    # PEP 621 optional-dependencies (extras) + PEP 735 dependency-groups: tables of
    # group -> list[str]. Grab every quoted requirement in those sections.
    for section_name in ("project.optional-dependencies", "dependency-groups"):
        section = re.search(rf"(?ms)^\[{re.escape(section_name)}\](.*?)(?=^\[|\Z)", text)
        if section:
            entries.extend(_requirement_string_entries(section.group(1)))
    # [tool.poetry.dependencies] AND group tables ([tool.poetry.group.dev.dependencies])
    for poetry in re.finditer(
        r"(?ms)^\[tool\.poetry(?:\.group\.[A-Za-z0-9_.\-]+)?\.dependencies\](.*?)(?=^\[|\Z)", text
    ):
        entries.extend(_toml_dependency_entries(poetry.group(1), exclude=frozenset({"python"})))
    return entries


def _parse_cargo_toml(text: str) -> list[_ManifestEntry]:
    entries = []
    for header in ("dependencies", "dev-dependencies", "build-dependencies"):
        section = re.search(rf"(?ms)^\[{header}\](.*?)(?=^\[|\Z)", text)
        if section:
            entries.extend(_toml_dependency_entries(section.group(1)))
    return entries


def _parse_gemfile(text: str) -> list[_ManifestEntry]:
    entries = []
    for line in text.splitlines():
        match = re.match(r"\s*gem\s+['\"]([^'\"]+)['\"](?:\s*,\s*['\"]([^'\"]+)['\"])?", line)
        if match:
            entries.append(_ManifestEntry(match.group(1), match.group(2)))
    return entries


def _parse_go_mod(text: str) -> list[_ManifestEntry]:
    entries = []
    in_require_block = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("require ("):
            in_require_block = True
            continue
        if in_require_block and stripped == ")":
            in_require_block = False
            continue
        if stripped.startswith("require ") and not stripped.endswith("("):
            match = re.match(r"require\s+(\S+)\s+(\S+)", stripped)
            if match:
                entries.append(_ManifestEntry(match.group(1), match.group(2)))
        elif in_require_block:
            match = re.match(r"(\S+)\s+(v\S+)", stripped)
            if match:
                entries.append(_ManifestEntry(match.group(1), match.group(2)))
    return entries


def _parse_pipfile(text: str) -> list[_ManifestEntry]:
    entries = []
    for header in ("packages", "dev-packages"):
        section = re.search(rf"(?ms)^\[{header}\](.*?)(?=^\[|\Z)", text)
        if section:
            entries.extend(_toml_dependency_entries(section.group(1)))
    return entries


def _parse_brewfile(text: str) -> list[_ManifestEntry]:
    entries = []
    for line in text.splitlines():
        match = re.match(r"\s*(?:brew|tap|cask|mas)\s+[\"']([^\"']+)[\"']", line)
        if match:
            entries.append(_ManifestEntry(match.group(1)))
    return entries


def _parse_setup_py(text: str) -> list[_ManifestEntry]:
    """setup.py — quoted requirement strings in install_requires / setup_requires /
    tests_require lists and inside extras_require's value lists."""
    entries = []
    for block in re.finditer(  # list OR tuple literal
        r"(?:install_requires|setup_requires|tests_require)\s*=\s*[\[(](.*?)[\])]", text, re.S
    ):
        entries.extend(_requirement_string_entries(block.group(1)))
    extras = re.search(r"extras_require\s*=\s*\{(.*?)\}", text, re.S)
    if extras:
        # only the value LISTS — the dict keys are extra names, not packages
        for value_list in re.finditer(r"\[(.*?)\]", extras.group(1), re.S):
            entries.extend(_requirement_string_entries(value_list.group(1)))
    return entries


def _parse_setup_cfg(text: str) -> list[_ManifestEntry]:
    """setup.cfg — [options] install_requires (indented requirement lines, which
    are requirements.txt-shaped) + [options.extras_require] values."""
    entries = []
    for block in re.finditer(
        r"(?m)^(?:install_requires|tests_require)\s*=\s*\n((?:[ \t]+.+\n?)+)", text
    ):
        requirement_lines = "\n".join(line.strip() for line in block.group(1).splitlines())
        entries.extend(_parse_requirements_txt(requirement_lines))
    for inline in re.finditer(  # single-line form: install_requires = pkg1; pkg2
        r"(?m)^(?:install_requires|tests_require)\s*=[ \t]*(\S.*)$", text
    ):
        for token in inline.group(1).split(";"):
            name, version = split_package_token(token.strip())
            if name:
                entries.append(_ManifestEntry(name, version))
    extras = re.search(r"(?ms)^\[options\.extras_require\](.*?)(?=^\[|\Z)", text)
    if extras:
        for line in extras.group(1).splitlines():
            if line[:1] in (" ", "\t"):  # indented requirement line under `extra =`
                entries.extend(_parse_requirements_txt(line.strip()))
            elif "=" in line:  # inline form: `extra = dep1;dep2`
                for token in line.split("=", 1)[1].split(";"):
                    name, version = split_package_token(token.strip())
                    if name:
                        entries.append(_ManifestEntry(name, version))
    return entries


def _parse_environment_yml(text: str) -> list[_ManifestEntry]:
    """conda environment.yml — `- name=spec` items under dependencies:, plus the
    nested `- pip:` block whose items are pip requirements."""
    entries: list[_ManifestEntry] = []
    in_dependencies = False
    pip_indent: int | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not line[0].isspace() and not stripped.startswith("-"):
            in_dependencies = stripped.startswith("dependencies:")
            pip_indent = None
            continue
        if not in_dependencies:
            continue
        item = re.match(r"-\s*(.+?)\s*$", stripped)
        if item is None:
            continue
        indent = len(line) - len(line.lstrip())
        value = item.group(1).strip("\"'")
        if value == "pip:":
            pip_indent = indent
            continue
        if pip_indent is not None and indent <= pip_indent:
            pip_indent = None  # left the pip block
        if pip_indent is not None:
            name, version = split_package_token(value)
            if name:
                entries.append(_ManifestEntry(name, version, manager="pip"))
            continue
        if "::" in value:  # channel prefix (conda-forge::numpy)
            value = value.split("::", 1)[1]
        match = re.match(r"([A-Za-z0-9_.\-]+)\s*(.*)$", value)
        if match and match.group(1).lower() != "python":
            entries.append(_ManifestEntry(match.group(1), match.group(2) or None))
    return entries


def _parse_composer_json(text: str) -> list[_ManifestEntry]:
    """composer.json — require / require-dev maps (values are version constraints)."""
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        return []
    if not isinstance(parsed, dict):
        return []
    entries = []
    for key in ("require", "require-dev"):
        section = parsed.get(key) or {}
        if isinstance(section, dict):
            entries.extend(
                _ManifestEntry(name, spec if isinstance(spec, str) else None)
                for name, spec in section.items()
                if name.lower() != "php"  # the language itself, like poetry's `python`
            )
    return entries


def _parse_dockerfile(text: str) -> list[_ManifestEntry]:
    """FROM base images; the tag/digest pin is the version."""
    entries = []
    for line in text.splitlines():
        match = re.match(r"^\s*FROM\s+(?:--\S+\s+)*(\S+)", line, re.I)  # skip --platform=…
        if match:
            name, version = _split_docker_image(match.group(1))
            if name:
                entries.append(_ManifestEntry(name, version))
    return entries


# ── lockfiles: the fully-resolved (transitive) dependency trees ──────────────
def _parse_package_lock_json(text: str) -> list[_ManifestEntry]:
    """npm package-lock.json — v2/v3 'packages' (node_modules paths) and v1 nested 'dependencies'."""
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return []
    if not isinstance(data, dict):
        return []
    entries: list[_ManifestEntry] = []
    packages = data.get("packages")
    if isinstance(packages, dict):
        for node_path, meta in packages.items():
            if node_path:  # "" is the project root
                version = meta.get("version") if isinstance(meta, dict) else None
                entries.append(_ManifestEntry(
                    node_path.split("node_modules/")[-1],
                    version if isinstance(version, str) else None,
                ))

    def walk(deps: object) -> None:
        if isinstance(deps, dict):
            for name, value in deps.items():
                version = value.get("version") if isinstance(value, dict) else None
                entries.append(_ManifestEntry(name, version if isinstance(version, str) else None))
                if isinstance(value, dict):
                    walk(value.get("dependencies"))

    walk(data.get("dependencies"))
    return entries


def _parse_pipfile_lock(text: str) -> list[_ManifestEntry]:
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return []
    if not isinstance(data, dict):
        return []
    entries: list[_ManifestEntry] = []
    for section in ("default", "develop"):
        block = data.get(section) or {}
        if isinstance(block, dict):
            for name, meta in block.items():
                version = meta.get("version") if isinstance(meta, dict) else None
                entries.append(_ManifestEntry(name, version if isinstance(version, str) else None))
    return entries


def _parse_toml_lock(text: str) -> list[_ManifestEntry]:
    """poetry.lock / Cargo.lock — name + version of each [[package]] block."""
    entries = []
    for block in text.split("[[package]]")[1:]:
        name = re.search(r'(?m)^name\s*=\s*"([^"]+)"', block)
        version = re.search(r'(?m)^version\s*=\s*"([^"]+)"', block)
        if name:
            entries.append(_ManifestEntry(name.group(1), version.group(1) if version else None))
    return entries


def _parse_go_sum(text: str) -> list[_ManifestEntry]:
    """go.sum — 'module version hash' lines (version's /go.mod suffix dropped)."""
    entries = []
    for line in text.splitlines():
        parts = line.split()
        if parts:
            version = parts[1].removesuffix("/go.mod") if len(parts) > 1 else None
            entries.append(_ManifestEntry(parts[0], version))
    return entries


def _parse_yarn_lock(text: str) -> list[_ManifestEntry]:
    """yarn.lock — name + range of each top-level entry key ("name@range, ...")."""
    entries: list[_ManifestEntry] = []
    for line in text.splitlines():
        if not line or line[0] in " #" or not line.rstrip().endswith(":"):
            continue
        for key in line.rstrip(":").split(","):
            key = key.strip().strip('"')
            if not key or key.startswith("__"):  # yarn-berry metadata (e.g. __metadata:)
                continue
            at = key.rfind("@")
            if at > 0:  # keep a leading @scope
                entries.append(_ManifestEntry(key[:at], key[at + 1:] or None))
            else:
                entries.append(_ManifestEntry(key))
    return entries


def _parse_pnpm_lock(text: str) -> list[_ManifestEntry]:
    """pnpm-lock.yaml — names + versions from indented keys (/name@ver, name@ver, /name/ver).

    The separator after the name may be '@' (any version, incl. non-numeric like
    @beta) or '/' (legacy v5 /name/version); we don't require a digit.
    """
    return [
        _ManifestEntry(name, version or None)
        for name, version in re.findall(
            r"(?m)^\s+'?/?((?:@[\w.\-]+/)?[\w.\-]+)[@/]([\w.\-]*)", text
        )
    ]


def _parse_gemfile_lock(text: str) -> list[_ManifestEntry]:
    """Gemfile.lock — gem names under the GEM specs: section (indented 'name (ver)')."""
    return [
        _ManifestEntry(name, version or None)
        for name, version in re.findall(r"(?m)^\s{4}([A-Za-z0-9_.\-]+) \(([^)]*)\)", text)
    ]


# Filename -> (default manager, parser). Resolved by _manifest_handler so variants
# (requirements-dev.txt, Dockerfile.web) and lockfiles are matched, not just exact
# names. Every parser returns list[_ManifestEntry] — one contract, no shape-sniffing.
_EXACT_MANIFESTS = {
    "package.json": ("npm", _parse_package_json),
    "package-lock.json": ("npm", _parse_package_lock_json),
    "yarn.lock": ("npm", _parse_yarn_lock),
    "pnpm-lock.yaml": ("npm", _parse_pnpm_lock),
    "pyproject.toml": ("pip", _parse_pyproject),
    "uv.lock": ("pip", _parse_toml_lock),  # same [[package]] name/version blocks
    "setup.py": ("pip", _parse_setup_py),
    "setup.cfg": ("pip", _parse_setup_cfg),
    "environment.yml": ("conda", _parse_environment_yml),
    "environment.yaml": ("conda", _parse_environment_yml),
    "composer.json": ("composer", _parse_composer_json),
    "pipfile": ("pip", _parse_pipfile),
    "pipfile.lock": ("pip", _parse_pipfile_lock),
    "poetry.lock": ("pip", _parse_toml_lock),
    "cargo.toml": ("cargo", _parse_cargo_toml),
    "cargo.lock": ("cargo", _parse_toml_lock),
    "gemfile": ("gem", _parse_gemfile),
    "gemfile.lock": ("gem", _parse_gemfile_lock),
    "go.mod": ("go", _parse_go_mod),
    "go.sum": ("go", _parse_go_sum),
    "brewfile": ("brew", _parse_brewfile),
}


def _manifest_handler(filename: str) -> tuple[str, _ManifestParser] | None:
    """Return (default manager, parser) for a manifest/lockfile filename, or None."""
    lower = filename.lower()
    if lower in _EXACT_MANIFESTS:
        return _EXACT_MANIFESTS[lower]
    if lower.endswith((".txt", ".in")) and "requirements" in lower:
        return ("pip", _parse_requirements_txt)  # requirements*.txt/.in, *-requirements.txt
    if lower == "dockerfile" or lower.startswith("dockerfile"):  # Dockerfile, Dockerfile.web
        return ("docker-base", _parse_dockerfile)
    return None


class _PackageHit(NamedTuple):
    """One package reference occurrence, before dedup into Package products."""

    manager: str
    name: str
    version: str | None
    context: Context


def _iter_packages(path: str, text: str) -> Iterator[_PackageHit]:
    """Yield a _PackageHit for each package reference in a file."""
    handler = _manifest_handler(basename(path))

    # Manifest pass — whole-file parse, no specific line, MANIFEST context.
    if handler is not None:
        default_manager, parser = handler
        manifest_context = context_for(path, None, RefForm.MANIFEST)
        for entry in parser(text):
            yield _PackageHit(entry.manager or default_manager, entry.name, entry.version, manifest_context)

    # Inline pass — single-line shell snippets, so per-line keeps line numbers.
    lines = text.splitlines()
    markdown = is_markdown(path)
    fence_flags = fence_map(lines) if markdown else []
    for line_index, line in enumerate(lines):
        form = markdown_form(lines, fence_flags, line_index) if markdown else RefForm.SCRIPT_FILE
        for manager, pattern in _INLINE_PATTERNS:
            for match in pattern.finditer(line):
                for token in _clean_inline(match.group(1)):  # every package, not just the first
                    package_name, version = split_package_token(token)
                    if package_name:
                        yield _PackageHit(manager, package_name, version, context_for(path, line_index + 1, form))
        for match in _DOCKER_INLINE_RE.finditer(line):
            # Walk RAW tokens (flags included) — _docker_image needs the flags to
            # know which following token is a value; only then clean the image.
            raw_tokens = [token.strip(" \t\"'`,;") for token in match.group(1).split()]
            image = _docker_image(raw_tokens)
            for token in _clean_inline(image) if image else ():
                image_name, version = _split_docker_image(token)
                if image_name:
                    yield _PackageHit("docker", image_name, version, context_for(path, line_index + 1, form))


def extract_packages(files: Mapping[str, str]) -> list[Package]:
    """Extract package references from a {relative-path: content} map.

    One Package per distinct (manager, name) — the name is canonical/version-free;
    every reference site is kept as an Occurrence carrying the version written
    there (manifest declaration and inline install of the same package both count).
    """
    occurrences_by_package: dict[tuple[str, str], list[Occurrence]] = {}
    for path, content in files.items():
        for hit in _iter_packages(path, content):
            occurrences_by_package.setdefault((hit.manager, hit.name), []).append(
                Occurrence(context=hit.context, version=hit.version)
            )
    return [
        Package(manager=manager, name=package_name, occurrences=tuple(occurrences))
        for (manager, package_name), occurrences in occurrences_by_package.items()
    ]
