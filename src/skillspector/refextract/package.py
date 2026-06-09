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
from collections.abc import Iterator, Mapping
from dataclasses import dataclass

from skillspector.refextract.context import Context, RefForm, context_for
from skillspector.refextract.markdown import fence_map, markdown_form
from skillspector.refextract.occurrence import Occurrence
from skillspector.refextract.paths import basename, is_markdown


@dataclass(frozen=True)
class Package:
    """A registry package a skill installs or depends on, with every reference site."""

    manager: str  # pip / npm / cargo / brew / go / ...
    name: str
    occurrences: tuple[Occurrence, ...]  # each occurrence's source_url is None (not URL-derived)

    @property
    def slug(self) -> str:
        """The canonical "manager:name" identifier."""
        return f"{self.manager}:{self.name}"

    def to_dict(self) -> dict[str, object]:
        return {
            "manager": self.manager,
            "name": self.name,
            "slug": self.slug,
            "occurrences": [occurrence.to_dict() for occurrence in self.occurrences],
        }


# ── inline shell install commands; capture group 1 = package name(s) ─────────
_INLINE_PATTERNS = [
    ("pip", re.compile(r"\b(?:pip3?|pipx|uv\s+pip)\s+install\s+(?:--[\w-]+(?:=\S+)?\s+)*([^|&;<>`]+)", re.I)),
    ("pip", re.compile(r"\b(?:poetry|pdm)\s+add\s+(?:--[\w-]+(?:=\S+)?\s+)*([^|&;<>`]+)", re.I)),
    ("conda", re.compile(r"\bconda\s+install\s+(?:-c\s+\S+\s+)?(?:--[\w-]+(?:=\S+)?\s+)*([^|&;<>`]+)", re.I)),
    ("npm", re.compile(r"\b(?:npm|yarn|pnpm|bun)\s+(?:install|add|i)\s+(?:--[\w-]+(?:=\S+)?\s+|-[\w]+\s+)*([^|&;<>`]+)", re.I)),
    ("npx", re.compile(r"\bnpx\s+(?:-{1,2}[\w-]+(?:=\S+)?\s+)*([^\s|&;<>`]+)", re.I)),
    ("brew", re.compile(r"\bbrew\s+(?:install|tap|cask\s+install)\s+(?:--[\w-]+(?:=\S+)?\s+)*([^|&;<>`]+)", re.I)),
    ("apt", re.compile(r"\bapt(?:-get)?\s+install\s+(?:-y\s+)?(?:--[\w-]+(?:=\S+)?\s+)*([^|&;<>`]+)", re.I)),
    ("yum/dnf", re.compile(r"\b(?:yum|dnf)\s+install\s+(?:-y\s+)?([^|&;<>`]+)", re.I)),
    ("pacman", re.compile(r"\bpacman\s+-S(?:y+u?)?\s+(?:--[\w-]+\s+)*([^|&;<>`]+)", re.I)),
    ("cargo", re.compile(r"\bcargo\s+(?:install|add)\s+(?:--[\w-]+(?:=\S+)?\s+)*([^|&;<>`]+)", re.I)),
    ("gem", re.compile(r"\bgem\s+install\s+(?:--[\w-]+(?:=\S+)?\s+)*([^|&;<>`]+)", re.I)),
    ("go", re.compile(r"\bgo\s+(?:install|get)\s+(?:-{1,2}[\w-]+(?:=\S+)?\s+)*([^\s|&;<>`]+)", re.I)),
    ("docker", re.compile(r"\bdocker\s+(?:pull|run(?:\s+-[\w-]+(?:\s+\S+)?)*)\s+([^\s|&;<>`]+)", re.I)),
]

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
        token = token.strip(" \t\"'`,;()[]{}").rstrip(".")
        if not token or _NOT_A_PACKAGE.match(token) or token.startswith("-"):
            continue
        if token.lower() in _STOPWORDS or token.endswith(".txt") or token.endswith(".lock"):
            continue
        cleaned.append(token)
    return cleaned


def _package_name(token: str) -> str | None:
    """Normalize an install token to a package name, or None if it isn't one.

    Strips version specifiers and extras (``requests==2.31.0`` -> ``requests``,
    ``foo[extra]`` -> ``foo``, ``@scope/pkg@1.2`` -> ``@scope/pkg``). VCS refs
    (``git+https://...``, ``git@...``) are kept whole — they are real
    supply-chain edges. Template placeholders and local paths are not packages;
    a bare http(s) URL download is captured by the URL extractor instead.
    """
    if token.startswith(("$", "${", "{", "<")):
        return None
    if token.startswith(("./", "../", "/")):
        return None
    if token.startswith(("http://", "https://")):
        return None
    if token.startswith(("git+", "git@")) or "://" in token:
        return token
    token = token.split("[", 1)[0]  # extras
    token = re.split(r"[<>=!~ ]", token, maxsplit=1)[0]  # PEP 508 / range specifiers
    if "@" in token[1:]:  # name@1.2.3 or @scope/name@1.2.3 — drop trailing @version
        token = token[0] + token[1:].rsplit("@", 1)[0]
    return token or None


# ── manifest parsers ─────────────────────────────────────────────────────────
def _parse_requirements_txt(text: str) -> list[str]:
    packages = []
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
            packages.append(egg.group(1))
            continue
        if line.startswith(("git+", "hg+", "svn+", "bzr+")) or "://" in line:
            packages.append(line.split()[0])
            continue
        line = re.sub(r"\s+#.*", "", line)  # strip trailing inline comment only
        match = re.match(r"^([A-Za-z0-9_][A-Za-z0-9_.\-]*)", line)  # name must start alnum/_
        if match:
            packages.append(match.group(1))
    return packages


def _parse_package_json(text: str) -> list[str]:
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        return []
    if not isinstance(parsed, dict):
        return []
    packages = []
    for key in (
        "dependencies", "devDependencies", "peerDependencies", "optionalDependencies",
        "bundledDependencies", "bundleDependencies",
    ):
        section = parsed.get(key) or {}
        if isinstance(section, dict):
            packages.extend(section.keys())
        elif isinstance(section, list):  # bundled* may be a list of names
            packages.extend(str(name) for name in section)
    return packages


def _parse_pyproject(text: str) -> list[str]:
    packages = []
    project = re.search(r"(?ms)^\[project\][^\[]*?^dependencies\s*=\s*\[(.*?)\]", text)
    if project:
        for token in re.findall(r"[\"']([^\"']+)[\"']", project.group(1)):
            match = re.match(r"([A-Za-z0-9_.\-]+)", token)
            if match:
                packages.append(match.group(1))
    # PEP 621 optional-dependencies (extras) + PEP 735 dependency-groups: tables of
    # group -> list[str]. Grab every quoted requirement in those sections.
    for section_name in ("project.optional-dependencies", "dependency-groups"):
        section = re.search(rf"(?ms)^\[{re.escape(section_name)}\](.*?)(?=^\[|\Z)", text)
        if section:
            for token in re.findall(r"[\"']([^\"']+)[\"']", section.group(1)):
                match = re.match(r"([A-Za-z0-9_.\-]+)", token)
                if match:
                    packages.append(match.group(1))
    poetry = re.search(r"(?ms)^\[tool\.poetry\.dependencies\](.*?)(?=^\[|\Z)", text)
    if poetry:
        for line in poetry.group(1).splitlines():
            match = re.match(r"^([A-Za-z0-9_.\-]+)\s*=", line.strip())
            if match and match.group(1).lower() != "python":
                packages.append(match.group(1))
    return packages


def _parse_cargo_toml(text: str) -> list[str]:
    packages = []
    for header in ("dependencies", "dev-dependencies", "build-dependencies"):
        section = re.search(rf"(?ms)^\[{header}\](.*?)(?=^\[|\Z)", text)
        if section:
            for line in section.group(1).splitlines():
                match = re.match(r"^([A-Za-z0-9_.\-]+)\s*=", line.strip())
                if match:
                    packages.append(match.group(1))
    return packages


def _parse_gemfile(text: str) -> list[str]:
    packages = []
    for line in text.splitlines():
        match = re.match(r"\s*gem\s+['\"]([^'\"]+)['\"]", line)
        if match:
            packages.append(match.group(1))
    return packages


def _parse_go_mod(text: str) -> list[str]:
    packages = []
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
            match = re.match(r"require\s+(\S+)\s+", stripped)
            if match:
                packages.append(match.group(1))
        elif in_require_block:
            match = re.match(r"(\S+)\s+v", stripped)
            if match:
                packages.append(match.group(1))
    return packages


def _parse_pipfile(text: str) -> list[str]:
    packages = []
    for header in ("packages", "dev-packages"):
        section = re.search(rf"(?ms)^\[{header}\](.*?)(?=^\[|\Z)", text)
        if section:
            for line in section.group(1).splitlines():
                match = re.match(r"^([A-Za-z0-9_.\-]+)\s*=", line.strip())
                if match:
                    packages.append(match.group(1))
    return packages


def _parse_brewfile(text: str) -> list[str]:
    packages = []
    for line in text.splitlines():
        match = re.match(r"\s*(?:brew|tap|cask|mas)\s+[\"']([^\"']+)[\"']", line)
        if match:
            packages.append(match.group(1))
    return packages


def _parse_dockerfile(text: str) -> list[tuple[str, str]]:
    """Surface FROM base images as ("docker-base", image) pairs."""
    images = []
    for line in text.splitlines():
        match = re.match(r"^\s*FROM\s+(\S+)", line, re.I)
        if match:
            images.append(("docker-base", match.group(1)))
    return images


# ── lockfiles: the fully-resolved (transitive) dependency trees ──────────────
def _parse_package_lock_json(text: str) -> list[str]:
    """npm package-lock.json — v2/v3 'packages' (node_modules paths) and v1 nested 'dependencies'."""
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return []
    if not isinstance(data, dict):
        return []
    names: list[str] = []
    packages = data.get("packages")
    if isinstance(packages, dict):
        for node_path in packages:
            if node_path:  # "" is the project root
                names.append(node_path.split("node_modules/")[-1])

    def walk(deps: object) -> None:
        if isinstance(deps, dict):
            for name, value in deps.items():
                names.append(name)
                if isinstance(value, dict):
                    walk(value.get("dependencies"))

    walk(data.get("dependencies"))
    return names


def _parse_pipfile_lock(text: str) -> list[str]:
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return []
    if not isinstance(data, dict):
        return []
    names: list[str] = []
    for section in ("default", "develop"):
        block = data.get(section) or {}
        if isinstance(block, dict):
            names.extend(block.keys())
    return names


def _parse_toml_lock(text: str) -> list[str]:
    """poetry.lock / Cargo.lock — the 'name = \"...\"' of each [[package]] block."""
    return re.findall(r'(?ms)^\[\[package\]\].*?^name\s*=\s*"([^"]+)"', text)


def _parse_go_sum(text: str) -> list[str]:
    """go.sum — first whitespace token (module path) of each line."""
    return [parts[0] for line in text.splitlines() if (parts := line.split())]


def _parse_yarn_lock(text: str) -> list[str]:
    """yarn.lock — the package name of each top-level entry key (strips @version)."""
    names: list[str] = []
    for line in text.splitlines():
        if not line or line[0] in " #" or not line.rstrip().endswith(":"):
            continue
        for key in line.rstrip(":").split(","):
            key = key.strip().strip('"')
            if not key or key.startswith("__"):  # yarn-berry metadata (e.g. __metadata:)
                continue
            at = key.rfind("@")
            name = key[:at] if at > 0 else key  # keep a leading @scope
            if name:
                names.append(name)
    return names


def _parse_pnpm_lock(text: str) -> list[str]:
    """pnpm-lock.yaml — package names from indented keys (/name@ver, name@ver, /name/ver).

    The separator after the name may be '@' (any version, incl. non-numeric like
    @beta) or '/' (legacy v5 /name/version); we don't require a digit.
    """
    return re.findall(r"(?m)^\s+'?/?((?:@[\w.\-]+/)?[\w.\-]+)[@/]", text)


def _parse_gemfile_lock(text: str) -> list[str]:
    """Gemfile.lock — gem names under the GEM specs: section (indented 'name (ver)')."""
    return re.findall(r"(?m)^\s{4}([A-Za-z0-9_.\-]+) \(", text)


# Filename -> (default manager, parser). Resolved by _manifest_handler so variants
# (requirements-dev.txt, Dockerfile.web) and lockfiles are matched, not just exact
# names. Dockerfile yields its own (manager, package) pairs.
_EXACT_MANIFESTS = {
    "package.json": ("npm", _parse_package_json),
    "package-lock.json": ("npm", _parse_package_lock_json),
    "yarn.lock": ("npm", _parse_yarn_lock),
    "pnpm-lock.yaml": ("npm", _parse_pnpm_lock),
    "pyproject.toml": ("pip", _parse_pyproject),
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


def _manifest_handler(filename: str):
    """Return (manager, parser) for a manifest/lockfile filename, or None."""
    lower = filename.lower()
    if lower in _EXACT_MANIFESTS:
        return _EXACT_MANIFESTS[lower]
    if lower.endswith(".txt") and "requirements" in lower:  # requirements*.txt, *-requirements.txt
        return ("pip", _parse_requirements_txt)
    if lower == "dockerfile" or lower.startswith("dockerfile"):  # Dockerfile, Dockerfile.web
        return (None, _parse_dockerfile)
    return None


def _iter_packages(path: str, text: str) -> Iterator[tuple[str, str, Context]]:
    """Yield (manager, name, context) for each package reference in a file."""
    handler = _manifest_handler(basename(path))

    # Manifest pass — whole-file parse, no specific line, MANIFEST context.
    if handler is not None:
        manager, parser = handler
        parsed = parser(text)
        manifest_context = context_for(path, None, RefForm.MANIFEST)
        if parsed and isinstance(parsed[0], tuple):
            for parsed_manager, package_name in parsed:
                yield (parsed_manager, package_name, manifest_context)
        else:
            for package_name in parsed:
                yield (manager, package_name, manifest_context)

    # Inline pass — single-line shell snippets, so per-line keeps line numbers.
    lines = text.splitlines()
    markdown = is_markdown(path)
    fence_flags = fence_map(lines) if markdown else []
    for line_index, line in enumerate(lines):
        form = markdown_form(lines, fence_flags, line_index) if markdown else RefForm.SCRIPT_FILE
        for manager, pattern in _INLINE_PATTERNS:
            for match in pattern.finditer(line):
                for token in _clean_inline(match.group(1)):  # every package, not just the first
                    package_name = _package_name(token)
                    if package_name:
                        yield (manager, package_name, context_for(path, line_index + 1, form))


def extract_packages(files: Mapping[str, str]) -> list[Package]:
    """Extract package references from a {relative-path: content} map.

    One Package per distinct (manager, name); every reference site is kept as an
    Occurrence (manifest declaration and inline install of the same package both count).
    """
    entries: dict[str, tuple[str, str, list[Occurrence]]] = {}
    for path, content in files.items():
        for manager, package_name, context in _iter_packages(path, content):
            occurrences = entries.setdefault(f"{manager}:{package_name}", (manager, package_name, []))[2]
            occurrences.append(Occurrence(context=context))
    return [
        Package(manager=manager, name=package_name, occurrences=tuple(occurrences))
        for manager, package_name, occurrences in entries.values()
    ]
