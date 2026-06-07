# Marketplace addition (air/space) — NOT part of upstream NVIDIA SkillSpector.
# Licensed under Apache-2.0, consistent with the vendored SkillSpector source.

"""claude_cli provider package — Claude via the Claude Code CLI (OAuth)."""

from .provider import (
    REGISTRY_PATH,
    ClaudeCliChatModel,
    ClaudeCliProvider,
    build_claude_cli_chat_model,
    claude_cli_available,
)

__all__ = [
    "REGISTRY_PATH",
    "ClaudeCliChatModel",
    "ClaudeCliProvider",
    "build_claude_cli_chat_model",
    "claude_cli_available",
]
