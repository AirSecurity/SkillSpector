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
from dataclasses import dataclass
from enum import StrEnum

from skillspector.refextract.context import Context, RefForm, context_for
from skillspector.refextract.paths import suffix


class Transport(StrEnum):
    """How a skill talks to an MCP server."""

    STDIO = "stdio"  # local process launched via command + args
    SSE = "sse"  # remote server-sent-events endpoint
    HTTP = "http"  # remote (streamable) HTTP endpoint

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
}


@dataclass(frozen=True)
class Mcp:
    """An MCP server a skill declares."""

    name: str
    context: Context
    command: str | None = None  # stdio launcher: npx / uvx / docker / node / python / ...
    args: tuple[str, ...] = ()
    transport: Transport = Transport.STDIO
    url: str | None = None  # remote (sse/http) servers
    env_vars: tuple[str, ...] = ()  # env NAMES only
    # Cross-link by value (not an object ref): matches Package.slug ("manager:name")
    # so a consumer can join an MCP launcher to the package it runs.
    package: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "context": self.context.to_dict(),
            "command": self.command,
            "args": list(self.args),
            "transport": str(self.transport),
            "url": self.url,
            "env_vars": list(self.env_vars),
            "package": self.package,
        }


def _command_basename(command: str) -> str:
    """Bare executable name (strip any directory + .exe)."""
    return command.rsplit("/", 1)[-1].rsplit("\\", 1)[-1].removesuffix(".exe").lower()


def _package_cross_link(command: str | None, args: tuple[str, ...]) -> str | None:
    """Derive a "manager:name" package from a launcher command + its args."""
    if not command:
        return None
    cmd = _command_basename(command)
    if cmd == "docker":
        skip_value = False
        for arg in args:
            if skip_value:  # this token is a flag's value, not the image
                skip_value = False
                continue
            if arg in ("run", "pull"):
                continue
            if arg.startswith("-"):
                skip_value = arg in _DOCKER_VALUE_FLAGS
                continue
            return f"docker:{arg}"
        return None
    manager = _RUNNER_TO_MANAGER.get(cmd)
    if not manager:
        return None  # node/python/bash/... launch a local script, not a package
    for arg in args:
        if arg.lower() in _RUNNER_SKIP_ARGS or arg.startswith("-"):
            continue
        return f"{manager}:{arg}"
    return None


def _transport_of(entry: dict) -> tuple[Transport, str | None]:
    """Return (transport, url) for a server entry."""
    url = entry.get("url")
    if not url:
        return (Transport.STDIO, None)
    declared = (entry.get("type") or entry.get("transport") or "").lower()
    if declared == "sse":
        return (Transport.SSE, url)
    if "http" in declared:  # http / streamable-http
        return (Transport.HTTP, url)
    # Undeclared remote: infer from the URL shape.
    return (Transport.SSE if str(url).rstrip("/").endswith("sse") else Transport.HTTP, url)


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
        yield Mcp(
            name=str(name),
            context=context_for(path, _find_line(lines, str(name)), RefForm.MCP_CONFIG),
            command=str(command) if command else None,
            args=args,
            transport=transport,
            url=str(url) if url else None,
            env_vars=env_vars,
            package=_package_cross_link(str(command) if command else None, args),
        )


def extract_mcps(files: Mapping[str, str]) -> list[Mcp]:
    """Extract MCP server references from a {relative-path: content} map.

    Scans JSON files for an ``mcpServers`` object. Deduped by server name; the
    first occurrence wins.
    """
    mcps: list[Mcp] = []
    seen: set[str] = set()
    for path, content in files.items():
        if suffix(path) != ".json" and "mcpServers" not in content:
            continue
        for mcp in _iter_mcps(path, content):
            if mcp.name in seen:
                continue
            seen.add(mcp.name)
            mcps.append(mcp)
    return mcps
