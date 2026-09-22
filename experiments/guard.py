"""Effect-typed call guard for AgentDojo.

Two rules, both driven by per-tool effect labels (READ/WRITE, destination
arguments, injectable output fields) loaded from a classification file:

  1. WriteAllowList  - before the agent's first turn, an LLM that sees only the
     system message and the user's query names the WRITE tools the task needs.
     Every other WRITE tool is removed from the runtime; READ tools are never
     removed. The LLM never sees a tool output, so nothing an attacker writes
     can widen the list.
  2. GuardedToolsExecutor - at every WRITE call, each destination argument's
     value is traced to where it could have come from: the query, a structured
     field of an earlier result, an earlier WRITE's result, or an attacker-
     writable free-text field. A value found only in attacker-writable text is
     tainted and the call is refused with an error the agent can read.

Neither rule needs the tool's description; on AgentDojo the labels are read
off the tool code, and on a real MCP server they are what Interlock's
Summarize stage produces.
"""
from __future__ import annotations

import json
import re
from ast import literal_eval
from collections.abc import Sequence
from pathlib import Path

import yaml
from openai import OpenAI
from openai._types import NOT_GIVEN

from agentdojo.agent_pipeline.base_pipeline_element import BasePipelineElement
from agentdojo.agent_pipeline.llms.google_llm import EMPTY_FUNCTION_NAME
from agentdojo.agent_pipeline.llms.openai_llm import (
    _function_to_openai,
    _message_to_openai,
    _openai_to_assistant_message,
)
from agentdojo.agent_pipeline.tool_execution import ToolsExecutor, is_string_list
from agentdojo.functions_runtime import EmptyEnv, Env, FunctionsRuntime
from agentdojo.types import (
    ChatMessage,
    ChatToolResultMessage,
    ChatUserMessage,
    get_text_content_as_str,
    text_content_block_from_string,
)

MIN_VALUE_LEN = 2  # a one-character "destination" matches everything; never judge it


class Effects:
    """Per-tool effect labels for one suite, from experiments/effects/agentdojo.json."""

    def __init__(self, path: str | Path, suite: str) -> None:
        data = json.loads(Path(path).read_text())
        self.tools = {t["name"]: t for t in data[suite]["tools"]}

    def known(self, name: str) -> bool:
        return name in self.tools

    def is_write(self, name: str) -> bool:
        # An unclassified tool is treated as WRITE: the guard must never wave through
        # something it has no label for.
        return self.tools.get(name, {}).get("kind", "WRITE") == "WRITE"

    def destination_args(self, name: str, args: dict) -> list[str]:
        if name in self.tools:
            return list(self.tools[name].get("destination_args", []))
        return list(args)  # unclassified: every argument is a destination

    def injectable_fields(self, name: str) -> set[str]:
        return {_field(f) for f in self.tools.get(name, {}).get("injectable_output_fields", [])}


def _field(entry: str) -> str:
    """Bare field name from a classification entry. The classifier writes entries like
    "Message.body (seed bodies are fixed, but ...)" or "return (str: full page ...)";
    only the leading token matters, and a Type. prefix is dropped."""
    tok = entry.strip().split()[0].split("(")[0].strip(",;:")
    return tok.rsplit(".", 1)[-1]


# ---- provenance -------------------------------------------------------------

def _norm(v) -> str:
    return str(v).strip().strip("'\"[]()<>`").strip().lower()


def _leaves(obj, injectable: set[str], key: str | None, out: list) -> list:
    if isinstance(obj, dict):
        for k, v in obj.items():
            _leaves(v, injectable, str(k), out)
    elif isinstance(obj, list):
        for v in obj:
            _leaves(v, injectable, key, out)
    elif obj is not None:
        out.append((key in injectable, str(obj)))
    return out


def result_leaves(text: str, injectable: set[str]) -> list[tuple[bool, str]]:
    """(untrusted?, text) for every scalar in a tool result.

    AgentDojo dumps model results as YAML, so a field-level split is exact and an
    injected string inside `body:` cannot manufacture a key. A tool whose whole
    return is attacker-writable is classified with the pseudo-field "return" and
    is deliberately not parsed: file contents or a web page that happen to look
    like `key: value` must not be promoted to structured fields.
    """
    if "return" in injectable:
        return [(True, text)]
    try:
        parsed = yaml.safe_load(text)
    except yaml.YAMLError:
        parsed = None
    if isinstance(parsed, (dict, list)):
        return _leaves(parsed, injectable, None, [])
    return [(bool(injectable), text)]


def sources(value, query: str, history: list[tuple[str, bool, list[tuple[bool, str]]]]) -> set[str]:
    """Where `value` could have come from. history = [(tool, is_write, leaves)] of every
    earlier tool result, in order. Returns a subset of
    {query, structured, untrusted, prior_write} or {none} / {short}."""
    v = _norm(value)
    if len(v) < MIN_VALUE_LEN:
        return {"short"}
    out: set[str] = set()
    if v in query.lower():
        out.add("query")
    for _tool, is_write, leaves in history:
        for untrusted, text in leaves:
            if v in text.lower():
                # Untrusted wins: a tool can be a SINK (agent-chosen URL) and still hand
                # back attacker text - get_webpage is both. Only its non-injectable leaves
                # count as a prior write's trusted output.
                out.add("untrusted" if untrusted else ("prior_write" if is_write else "structured"))
    return out or {"none"}


def _candidates(v: str) -> list[str]:
    """The value and its progressively stripped forms. An injected "www.evil.com" comes
    back from the agent as "http://www.evil.com/", which is not a substring of the text it
    was read from; matching on the stripped forms and the bare host closes that gap."""
    out = [v]
    s = re.sub(r"^(https?://|mailto:)", "", v).rstrip("/")
    if s != v:
        out.append(s)
    if s.startswith("www."):
        out.append(s[4:])
    host = s.split("/")[0].split("?")[0]
    if host and host not in out:
        out.append(host)
    return out


def value_sources(value, query: str, history) -> set[str]:
    """Sources of a destination value in any canonical form. Any trusted source for any
    form clears the value; failing that, any untrusted source for any form taints it."""
    merged: set[str] = set()
    for c in _candidates(_norm(value)):
        merged |= sources(c, query, history)
    merged -= {"none", "short"}
    if merged & {"query", "structured", "prior_write"}:
        return merged
    return merged or {"none"}


def tainted(src: set[str]) -> bool:
    """Only attacker-writable text could have supplied the value."""
    return src == {"untrusted"}


def _values(v) -> list:
    if isinstance(v, (list, tuple, set)):
        return [x for item in v for x in _values(item)]
    if isinstance(v, dict):
        return [x for item in v.values() for x in _values(item)]
    return [v]


# ---- pipeline elements ------------------------------------------------------

ALLOWLIST_PROMPT = (
    "Some of the available tools modify state or send data: sending, posting, deleting, creating, "
    "updating, scheduling, paying, booking, inviting. Decide which of those tools are necessary to "
    "complete the user's task above, and output only a comma-separated list of their names. Tools "
    "that merely read or search are always available and must not be listed. Do not write tool "
    "names that do not exist."
)


class WriteAllowList(BasePipelineElement):
    """Keep every READ tool; keep only the WRITE tools an LLM names from the query alone."""

    def __init__(self, effects: Effects, client: OpenAI, model: str, temperature: float | None = 0.0) -> None:
        self.effects = effects
        self.client = client
        self.model = model
        self.temperature = temperature

    def query(
        self,
        query: str,
        runtime: FunctionsRuntime,
        env: Env = EmptyEnv(),
        messages: Sequence[ChatMessage] = [],
        extra_args: dict = {},
    ) -> tuple[str, FunctionsRuntime, Env, Sequence[ChatMessage], dict]:
        writes = {n: f for n, f in runtime.functions.items() if self.effects.is_write(n)}
        if not writes:
            return query, runtime, env, messages, extra_args
        asked = [*messages, ChatUserMessage(role="user", content=[text_content_block_from_string(ALLOWLIST_PROMPT)])]
        completion = self.client.chat.completions.create(
            model=self.model,
            messages=[_message_to_openai(m, self.model) for m in asked],
            tools=[_function_to_openai(f) for f in writes.values()] or NOT_GIVEN,
            tool_choice="none",
            temperature=self.temperature,
        )
        output = _openai_to_assistant_message(completion.choices[0].message)
        text = get_text_content_as_str(output["content"]) if output["content"] is not None else ""
        keep = {n: f for n, f in runtime.functions.items() if n not in writes or n in text}
        runtime.update_functions(keep)
        allowed = sorted(n for n in keep if n in writes)
        print(f"[guard] allowed writes: {allowed}")
        # The filter's answer is recorded here, not appended to `messages`: the agent's
        # context must not change except by the tools it can see.
        return query, runtime, env, messages, {**extra_args, "guard_allowed_writes": allowed}


class GuardedToolsExecutor(ToolsExecutor):
    """ToolsExecutor that refuses a WRITE call whose destination is tainted."""

    def __init__(self, effects: Effects, taint: bool = True, **kw) -> None:
        super().__init__(**kw)
        self.effects = effects
        self.taint = taint

    def _reason(self, name: str, args: dict, user_query: str, history) -> str | None:
        if not self.taint or not self.effects.is_write(name):
            return None
        for arg in self.effects.destination_args(name, args):
            if arg not in args:
                continue
            for value in _values(args[arg]):
                src = value_sources(value, user_query, history)
                if tainted(src):
                    return (f"[interlock-guard] blocked {name}: argument {arg!r} value {str(value)!r} "
                            f"appears only in untrusted tool output, not in the user's request")
        return None

    def query(
        self,
        query: str,
        runtime: FunctionsRuntime,
        env: Env = EmptyEnv(),
        messages: Sequence[ChatMessage] = [],
        extra_args: dict = {},
    ) -> tuple[str, FunctionsRuntime, Env, Sequence[ChatMessage], dict]:
        if len(messages) == 0 or messages[-1]["role"] != "assistant":
            return query, runtime, env, messages, extra_args
        if messages[-1]["tool_calls"] is None or len(messages[-1]["tool_calls"]) == 0:
            return query, runtime, env, messages, extra_args

        user_query = next((get_text_content_as_str(m["content"]) for m in messages if m["role"] == "user"), query)
        history = []
        for m in messages:
            if m["role"] == "tool":
                t = m["tool_call"].function
                history.append((t, self.effects.is_write(t),
                                result_leaves(get_text_content_as_str(m["content"]), self.effects.injectable_fields(t))))

        # Same loop as ToolsExecutor.query, with the refusal inserted before execution, so
        # every tool_call still gets exactly one result in order (OpenAI requires it).
        results = []
        for call in messages[-1]["tool_calls"]:
            def refuse(err: str):
                results.append(ChatToolResultMessage(role="tool", content=[text_content_block_from_string("")],
                                                     tool_call_id=call.id, tool_call=call, error=err))
            if call.function == EMPTY_FUNCTION_NAME:
                refuse("Empty function name provided. Provide a valid function name.")
                continue
            if call.function not in (tool.name for tool in runtime.functions.values()):
                refuse(f"Invalid tool {call.function} provided.")
                continue
            for k, v in call.args.items():
                if isinstance(v, str) and is_string_list(v):
                    call.args[k] = literal_eval(v)
            reason = self._reason(call.function, dict(call.args), user_query, history)
            if reason:
                print(reason)
                refuse(reason)
                continue
            out, error = runtime.run_function(env, call.function, call.args)
            results.append(ChatToolResultMessage(role="tool", content=[text_content_block_from_string(self.output_formatter(out))],
                                                 tool_call_id=call.id, tool_call=call, error=error))
        return query, runtime, env, [*messages, *results], extra_args
