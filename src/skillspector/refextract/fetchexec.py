# Marketplace addition (air/space) — NOT part of upstream NVIDIA SkillSpector.
# Licensed under Apache-2.0, consistent with the vendored SkillSpector source.

"""Fetch-and-execute references — the FetchExecute product and extract_fetch_executes().

The highest-signal supply-chain pattern: a command that downloads remote content
and immediately runs it (``curl … | bash``, ``eval $(curl …)``, ``bash <(curl …)``,
PowerShell ``iex (iwr …)``, etc.). The bare URL is already captured by the URL
extractor; this records the *execution semantics* — that the fetched payload is
piped to a shell / eval'd / made executable — which is the dangerous part.

Ported from the audit toolkit's scan_fetch_and_execute.py. One occurrence per
matching line (not deduped); the first matching pattern on a line wins.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import NamedTuple
from urllib.parse import urlparse

from skillspector.refextract.context import Context, RefForm, context_for
from skillspector.refextract.markdown import fence_map, markdown_form
from skillspector.refextract.paths import is_markdown
from skillspector.refextract.url import first_url_on_line


class ExecPattern(StrEnum):
    """Which fetch-and-execute construct was matched."""

    CURL_PIPE_SHELL = "curl_pipe_shell"  # curl … | bash|sh|zsh|fish
    EVAL_CURL = "eval_curl"  # eval "$(curl …)"
    SHELL_PROCESS_SUB = "shell_process_substitution"  # bash <(curl …)
    PYTHON_C_CURL = "python_c_curl"  # python -c "$(curl …)"
    CURL_THEN_CHMOD = "curl_then_chmod"  # curl -O … ; chmod +x
    SOURCE_CURL = "source_curl"  # source <(curl …)
    POWERSHELL_IEX = "powershell_iex"  # iex (iwr …) / Invoke-Expression (Invoke-WebRequest …)


@dataclass(frozen=True)
class FetchExecute:
    """One fetch-and-execute command occurrence found in a skill."""

    pattern: ExecPattern
    context: Context
    url: str | None = None  # the fetched URL, when one is on the line
    host: str | None = None  # host of that URL

    def to_dict(self) -> dict[str, object]:
        return {
            "pattern": str(self.pattern),
            "context": self.context.to_dict(),
            "url": self.url,
            "host": self.host,
        }


# First matching pattern on a line wins (ordered most- to least-specific-ish).
_PATTERNS = [
    (ExecPattern.CURL_PIPE_SHELL,
     re.compile(r"(curl|wget)\s+[^\n|;`]{0,300}?\|\s*(?:sudo\s+(?:-\S+\s+)*)?(bash|sh|zsh|fish)\b", re.I)),
    (ExecPattern.EVAL_CURL,
     re.compile(r"\beval\s+[\"']?\$\(\s*(curl|wget)\b", re.I)),
    (ExecPattern.SHELL_PROCESS_SUB,
     re.compile(r"\b(bash|sh|zsh|fish)\s+<\(\s*(curl|wget)\b", re.I)),
    (ExecPattern.PYTHON_C_CURL,
     re.compile(r"\bpython3?\s+-c\s+[\"']?\$\(\s*(curl|wget)\b", re.I)),
    (ExecPattern.CURL_THEN_CHMOD,
     re.compile(r"\bcurl\s+(-O|-o\s+\S+)[^;&\n]{0,200}?(?:;|&&).{0,200}?chmod\s+\+x", re.I)),
    (ExecPattern.SOURCE_CURL,
     re.compile(r"\bsource\s+<\(\s*(curl|wget)\b", re.I)),
    (ExecPattern.POWERSHELL_IEX,
     re.compile(
         r"\biex\s+\(\s*i(?:wr|rm)\b"
         r"|\binvoke-expression\s+\(\s*invoke-(?:webrequest|restmethod)\b",
         re.I,
     )),
]

class _LineUrl(NamedTuple):
    """The fetched URL found on a matched line, with its host (either may be None)."""

    url: str | None
    host: str | None


def _line_url(line: str) -> _LineUrl:
    """The first URL on the line (via the shared url.py recognizer) and its host."""
    url = first_url_on_line(line)
    if url is None:
        return _LineUrl(None, None)
    try:
        host = (urlparse(url).hostname or "").lower() or None
    except ValueError:
        host = None
    return _LineUrl(url, host)


def extract_fetch_executes(files: Mapping[str, str]) -> list[FetchExecute]:
    """Extract fetch-and-execute command occurrences from a {path: content} map."""
    results: list[FetchExecute] = []
    for path, content in files.items():
        lines = content.splitlines()
        markdown = is_markdown(path)
        fence_flags = fence_map(lines) if markdown else []
        for line_index, line in enumerate(lines):
            for pattern, regex in _PATTERNS:
                if regex.search(line):
                    form = markdown_form(lines, fence_flags, line_index) if markdown else RefForm.SCRIPT_FILE
                    url, host = _line_url(line)
                    results.append(
                        FetchExecute(
                            pattern=pattern,
                            context=context_for(path, line_index + 1, form),
                            url=url,
                            host=host,
                        )
                    )
                    break  # first matching pattern per line wins
    return results
