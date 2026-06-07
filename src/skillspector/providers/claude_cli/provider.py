# Marketplace addition (air/space) — NOT part of upstream NVIDIA SkillSpector.
# Licensed under Apache-2.0, consistent with the vendored SkillSpector source.

"""claude_cli provider — Claude via the Claude Code CLI (OAuth, your login).

Instead of an API key or Vertex ADC, it shells out to the locally-installed
``claude`` CLI

    claude -p --output-format json --allowedTools "" [--model <alias>]
           [--system-prompt <s>] [--json-schema <schema>] <prompt>

so scans run on the operator's own Claude subscription (``claude login`` or a
``CLAUDE_CODE_OAUTH_TOKEN``). No API key, no Vertex project, no per-token bill.

SkillSpector drives its LLM through a langchain ``BaseChatModel``
(``llm_utils.get_chat_model`` → ``.with_structured_output(schema).invoke(...)``),
so this wraps the CLI as a ``BaseChatModel`` (``ClaudeCliChatModel``) plus a
metadata provider (``ClaudeCliProvider``) for token budgets.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, SystemMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.prompt_values import PromptValue
from langchain_core.runnables import Runnable, RunnableLambda
from pydantic import BaseModel

from skillspector.providers import registry

REGISTRY_PATH = str(Path(__file__).with_name("model_registry.yaml"))

# How long a single `claude -p` turn may run before we give up.
DEFAULT_TURN_TIMEOUT = 600


def claude_cli_available() -> bool:
    """True when the ``claude`` CLI is on PATH (the only "credential" needed)."""
    return shutil.which("claude") is not None


def _cli_model_alias(model: str | None) -> str | None:
    """Map a marketplace model label to a CLI-accepted alias (sonnet/opus/haiku)."""
    if not model:
        return None
    low = model.lower()
    if "opus" in low:
        return "opus"
    if "haiku" in low:
        return "haiku"
    if "sonnet" in low:
        return "sonnet"
    return model


def _coerce_to_text(value: Any) -> tuple[str, str | None]:
    """Coerce a Runnable input into ``(user_prompt, system_prompt | None)``."""
    if isinstance(value, str):
        return value, None
    if isinstance(value, PromptValue):
        return _messages_to_text(value.to_messages())
    if isinstance(value, BaseMessage):
        return str(value.content), None
    if isinstance(value, (list, tuple)) and all(isinstance(m, BaseMessage) for m in value):
        return _messages_to_text(list(value))
    return str(value), None


def _messages_to_text(messages: list[BaseMessage]) -> tuple[str, str | None]:
    """Split messages into a joined user prompt + an optional system prompt."""
    system_parts = [str(m.content) for m in messages if isinstance(m, SystemMessage)]
    user_parts = [str(m.content) for m in messages if not isinstance(m, SystemMessage)]
    system = "\n\n".join(p for p in system_parts if p) or None
    return "\n\n".join(user_parts), system


def _schema_and_parser(schema: Any):
    """Return ``(json_schema_dict, parser)`` for ``with_structured_output``."""
    if isinstance(schema, type) and issubclass(schema, BaseModel):
        return schema.model_json_schema(), schema.model_validate
    if isinstance(schema, dict):
        return schema, lambda data: data
    raise TypeError(f"unsupported structured-output schema: {type(schema).__name__}")


class ClaudeCliChatModel(BaseChatModel):
    """langchain chat model backed by the ``claude`` CLI (OAuth)."""

    model: str = ""
    timeout: int = DEFAULT_TURN_TIMEOUT
    allowed_tools: str = ""

    @property
    def _llm_type(self) -> str:
        return "claude_cli"

    def _run_claude(
        self,
        prompt: str,
        *,
        system_prompt: str | None = None,
        json_schema: dict | None = None,
    ) -> dict:
        if not claude_cli_available():
            raise RuntimeError(
                "`claude` CLI not found on PATH. Install Claude Code and run "
                "`claude login`, or pick another SKILLSPECTOR_PROVIDER."
            )
        cmd = ["claude", "-p", "--output-format", "json", "--allowedTools", self.allowed_tools]
        alias = _cli_model_alias(self.model)
        if alias:
            cmd += ["--model", alias]
        if system_prompt:
            cmd += ["--system-prompt", system_prompt]
        if json_schema is not None:
            cmd += ["--json-schema", json.dumps(json_schema)]
        cmd.append(prompt)

        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=self.timeout)
        if proc.returncode != 0:
            raise RuntimeError(f"claude CLI failed (rc={proc.returncode}): {proc.stderr[:500]}")
        try:
            return json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"claude CLI returned non-JSON output: {proc.stdout[:500]}") from exc

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        user_prompt, system_prompt = _messages_to_text(messages)
        data = self._run_claude(user_prompt, system_prompt=system_prompt)
        text = data.get("result") or ""
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=text))])

    def with_structured_output(  # type: ignore[override]
        self,
        schema: Any,
        *,
        include_raw: bool = False,
        **kwargs: Any,
    ) -> Runnable:
        """Enforce ``schema`` via the CLI ``--json-schema`` flag and parse the result."""
        if include_raw:
            raise NotImplementedError("claude_cli provider does not support include_raw=True")
        json_schema, parser = _schema_and_parser(schema)
        model = self

        def _invoke(value: Any) -> Any:
            user_prompt, system_prompt = _coerce_to_text(value)
            data = model._run_claude(
                user_prompt, system_prompt=system_prompt, json_schema=json_schema
            )
            structured = data.get("structured_output")
            if structured is None:
                # Older CLI without native structured_output: parse the text result.
                structured = json.loads(data.get("result") or "{}")
            return parser(structured)

        return RunnableLambda(_invoke)


def build_claude_cli_chat_model(model: str | None = None) -> ClaudeCliChatModel:
    """Construct the CLI-backed chat model (raises early if the CLI is absent)."""
    if not claude_cli_available():
        raise ValueError(
            "claude_cli provider selected but the `claude` CLI is not on PATH. "
            "Install Claude Code and run `claude login`."
        )
    return ClaudeCliChatModel(model=model or "")


class ClaudeCliProvider:
    """Metadata provider for claude_cli — token budgets + model defaults.

    Auth is the CLI's own OAuth, so ``resolve_credentials`` returns ``None``;
    ``llm_utils`` special-cases this provider in ``get_chat_model`` /
    ``is_llm_available``.
    """

    DEFAULT_MODEL = "sonnet"
    SLOT_DEFAULTS: dict[str, str] = {}

    def resolve_credentials(self) -> tuple[str, str | None] | None:
        return None

    def get_context_length(self, model: str) -> int | None:
        return registry.lookup_context_length(REGISTRY_PATH, model)

    def get_max_output_tokens(self, model: str) -> int | None:
        return registry.lookup_max_output_tokens(REGISTRY_PATH, model)

    def resolve_model(self, slot: str = "default") -> str:
        """``SKILLSPECTOR_MODEL`` env > slot default > ``DEFAULT_MODEL``."""
        user_input = os.environ.get("SKILLSPECTOR_MODEL", "").strip()
        return user_input or self.SLOT_DEFAULTS.get(slot, "") or self.DEFAULT_MODEL
