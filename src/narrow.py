"""Narrow: before the agent's first turn, the WRITE tools the task needs.

An LLM that sees only the system message, the user's query and the tool definitions plans the
calls that complete the task; the WRITE tools the plan names are the allow-list. Every other WRITE tool is removed from the runtime (prune mode) or refused when
called (gate mode, enforced by src.guard.GuardedToolsExecutor); READ tools are never
touched. The LLM never sees a tool output, so nothing an attacker writes can widen the list.
"""
from __future__ import annotations

import re
from collections.abc import Sequence

from openai import OpenAI
from openai._types import NOT_GIVEN

from agentdojo.agent_pipeline.base_pipeline_element import BasePipelineElement
from agentdojo.agent_pipeline.llms.openai_llm import (
    _function_to_openai,
    _message_to_openai,
    _openai_to_assistant_message,
)
from agentdojo.functions_runtime import EmptyEnv, Env, FunctionsRuntime
from agentdojo.types import ChatMessage, ChatUserMessage, get_text_content_as_str, text_content_block_from_string

from src.guard import Effects, takes_args

# The plan sees every tool: asked to pick only among the WRITE tools, the model leaves out one that does not look
# like a write (asked to draw a chart, it named none of 27 chart tools) and cannot see the reads that come first.
ALLOWLIST_PROMPT = (
    "Before any tool runs, plan how the assistant will complete the user's task above: list, one per line, the "
    "tools it will call, in order, by name, including the tools for any action the user asks for whose details "
    "the assistant will only learn by reading first. Use only the tools given with this message and do not call "
    "them now."
)


class WriteAllowList(BasePipelineElement):
    """Keep every READ tool; keep only the WRITE tools an LLM plans to call, from the query and tool definitions alone."""

    def __init__(self, effects: Effects, client: OpenAI, model: str, temperature: float | None = 0.0,
                 prune: bool = True) -> None:
        self.effects = effects
        self.client = client
        self.model = model
        self.temperature = temperature
        # prune=True removes the disallowed WRITE tools from the runtime; prune=False leaves
        # them visible and lets GuardedToolsExecutor(gate=True) refuse calls to them. The
        # security is the same. The difference is what the agent experiences when an
        # injection names a tool the task does not need: an absent tool is silence, and
        # the agent wanders; a refusal is a signal, and it can return to the user's task.
        self.prune = prune

    def query(
        self,
        query: str,
        runtime: FunctionsRuntime,
        env: Env = EmptyEnv(),
        messages: Sequence[ChatMessage] = [],
        extra_args: dict = {},
    ) -> tuple[str, FunctionsRuntime, Env, Sequence[ChatMessage], dict]:
        writes = {n: f for n, f in runtime.functions.items() if self.effects.steerable(n, takes_args(f))}
        if not writes:
            return query, runtime, env, messages, extra_args
        asked = [*messages, ChatUserMessage(role="user", content=[text_content_block_from_string(ALLOWLIST_PROMPT)])]
        completion = self.client.chat.completions.create(
            model=self.model,
            messages=[_message_to_openai(m, self.model) for m in asked],
            tools=[_function_to_openai(f) for f in runtime.functions.values()] or NOT_GIVEN,
            tool_choice="none",
            temperature=self.temperature,
        )
        output = _openai_to_assistant_message(completion.choices[0].message)
        text = get_text_content_as_str(output["content"]) if output["content"] is not None else ""
        named = set(re.findall(r"[A-Za-z_][A-Za-z0-9_\-]*", text))
        keep = {n: f for n, f in runtime.functions.items() if n not in writes or n in named}
        if self.prune:
            runtime.update_functions(keep)
        allowed = sorted(n for n in keep if n in writes)
        print(f"[guard] allowed writes: {allowed}", flush=True)
        # The filter's answer is recorded here, not appended to `messages`: the agent's
        # context must not change except by the tools it can see.
        return query, runtime, env, messages, {**extra_args, "guard_allowed_writes": allowed}
