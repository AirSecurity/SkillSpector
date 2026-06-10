# Marketplace addition (air/space) — NOT part of upstream NVIDIA SkillSpector.
# Licensed under Apache-2.0, consistent with the vendored SkillSpector source.

"""MCP server references — the Mcp product and extract_mcps().

An MCP server a skill wires up is a high-value supply-chain edge: it runs an
external process (often via ``npx``/``uvx``, i.e. a fetched package) or talks to
a remote endpoint, frequently with secrets passed through env. This extractor
reads MCP server declarations from config (``.mcp.json`` / any JSON file with an
``mcpServers`` object) and records, per server:

  * name, command + args, transport (stdio / sse / http), remote url
  * env-var NAMES passed to the server (never values)
  * a command->package cross-link (an ``npx``/``uvx``/``docker`` launcher names
    a package the Package extractor would otherwise miss)

No tested toolkit logic existed for this — it is new for the marketplace fork.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import NamedTuple

from skillspector.refextract.context import Context, RefForm, context_for
from skillspector.refextract.occurrence import Occurrence
from skillspector.refextract.package import launcher_package
from skillspector.refextract.paths import suffix


class Transport(StrEnum):
    """How a skill talks to an MCP server."""

    STDIO = "stdio"  # local process launched via command + args
    SSE = "sse"  # remote server-sent-events endpoint
    HTTP = "http"  # remote (streamable) HTTP endpoint


@dataclass(frozen=True)
class Mcp:
    """An MCP server declaration, deduped by full identity (not name alone).

    Two declarations sharing a name but differing in command/args/url are
    *different products* — a name collision with a different payload is exactly
    the anomaly that must stay visible. Identical declarations found in several
    files merge into one Mcp whose ``occurrences`` lists every declaration site.
    """

    name: str
    command: str | None = None  # stdio launcher: npx / uvx / docker / node / python / ...
    args: tuple[str, ...] = ()
    transport: Transport = Transport.STDIO
    url: str | None = None  # remote (sse/http) servers
    env_vars: tuple[str, ...] = ()  # env NAMES only
    # Cross-link by value (not an object ref): matches Package.slug ("manager:name",
    # same normalization — see package.launcher_package) so a consumer can join an
    # MCP launcher to the package it runs. The matching Package product only exists
    # when the skill also references the package elsewhere; treat as a dangling FK.
    package: str | None = None
    occurrences: tuple[Occurrence, ...] = ()

    @property
    def context(self) -> Context:
        """First declaration site — keeps Mcp a UrlBearing for sources.url_sources."""
        return self.occurrences[0].context

    @property
    def identity(self) -> tuple[str, str | None, tuple[str, ...], Transport, str | None, tuple[str, ...]]:
        """Everything that makes two declarations the same server (all fields but
        the declaration sites)."""
        return (self.name, self.command, self.args, self.transport, self.url, self.env_vars)

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "command": self.command,
            "args": list(self.args),
            "transport": str(self.transport),
            "url": self.url,
            "env_vars": list(self.env_vars),
            "package": self.package,
            "occurrences": [occurrence.to_dict() for occurrence in self.occurrences],
        }


class _Endpoint(NamedTuple):
    """A server entry's transport and remote URL (url None for stdio)."""

    transport: Transport
    url: str | None


def _transport_of(entry: dict) -> _Endpoint:
    """Resolve a server entry's transport (and remote URL, when it has one)."""
    url = entry.get("url")
    if not url:
        return _Endpoint(Transport.STDIO, None)
    declared = (entry.get("type") or entry.get("transport") or "").lower()
    if declared == "sse":
        return _Endpoint(Transport.SSE, url)
    if "http" in declared:  # http / streamable-http
        return _Endpoint(Transport.HTTP, url)
    # Undeclared remote: infer from the URL shape.
    return _Endpoint(Transport.SSE if str(url).rstrip("/").endswith("sse") else Transport.HTTP, url)


def _find_line(lines: list[str], name: str) -> int | None:
    """1-based line of the server's key in the raw config, or None if not found."""
    needle = f'"{name}"'
    for index, line in enumerate(lines):
        if needle in line:
            return index + 1
    return None


def _iter_mcps(path: str, text: str) -> Iterator[Mcp]:
    """Yield Mcp for each server in a JSON config that has an ``mcpServers`` object."""
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        return
    if not isinstance(parsed, dict):
        return
    servers = parsed.get("mcpServers")
    if not isinstance(servers, dict):
        return

    lines = text.splitlines()
    for name, entry in servers.items():
        if not isinstance(entry, dict):
            continue
        command = entry.get("command")
        raw_args = entry.get("args") or []
        args = tuple(str(arg) for arg in raw_args) if isinstance(raw_args, list) else ()
        env = entry.get("env") or {}
        env_vars = tuple(env.keys()) if isinstance(env, dict) else ()
        transport, url = _transport_of(entry)
        context = context_for(path, _find_line(lines, str(name)), RefForm.MCP_CONFIG)
        yield Mcp(
            name=str(name),
            command=str(command) if command else None,
            args=args,
            transport=transport,
            url=str(url) if url else None,
            env_vars=env_vars,
            package=launcher_package(str(command) if command else None, args),
            occurrences=(Occurrence(context=context),),
        )


def extract_mcps(files: Mapping[str, str]) -> list[Mcp]:
    """Extract MCP server references from a {relative-path: content} map.

    Scans JSON files (or any file mentioning ``mcpServers``) for server objects.
    Deduped by full identity: identical declarations merge (occurrences keeps
    every site); same-name declarations with different payloads stay separate.
    """
    merged: dict[tuple, Mcp] = {}
    for path, content in files.items():
        if suffix(path) != ".json" and "mcpServers" not in content:
            continue
        for mcp in _iter_mcps(path, content):
            existing = merged.get(mcp.identity)
            if existing is None:
                merged[mcp.identity] = mcp
            else:
                merged[mcp.identity] = replace(
                    existing, occurrences=existing.occurrences + mcp.occurrences
                )
    return list(merged.values())
