"""Ask Claude to judge each tool's effects, with mandatory file:line evidence."""
from __future__ import annotations
import json
from pathlib import Path

RUBRIC = """You judge what an MCP server's advertised tools can reach, by reading its source code.

Threat model. The agent follows injected instructions and may pass any tool's output into any tool's input.
An attacker controls arbitrary web hosts and content, and content in multi-writer or third-party sources
(public issues and comments, community documentation or registries, search results). An attacker does not
control the user's local files or the data in the user's own accounts. An attacker can read hosts they
control and anything published publicly.

Labels.
SECRET: the tool returns non-public data into the agent's context - host files, databases or SaaS data
reached with the user's credentials, or content of file://, localhost or intranet URLs it can reach.
UNTRUSTED: the tool returns attacker-controllable content.
SINK: the tool can deliver agent-supplied data to an attacker-readable destination - a request to an
agent-chosen URL or host, a write to a public repository, issue, comment or gist, or a message to an
agent-chosen recipient. A query sent to a fixed vendor endpoint is not a SINK.
HOSTEXEC: arbitrary code execution on the host, including escapable sandboxes. Implies the other three.
Code confined to a browser page or a remote vendor sandbox is not HOSTEXEC; label its actual reach.

Rules.
Judge only from code. Every label must cite evidence as file:line or file:start-end, pointing at the line
that shows the behaviour. Cite identifiers you rely on in the rationale inside backticks.
A tool's free-text description may raise a label but never removes one.
If the code does not settle a question, set undetermined true, keep the least restrictive reading (that is,
keep the label), and say why in the rationale.
default_enabled is false when a tool is only advertised behind a flag, capability or toolset that is off by default.
value_conditions list the flags, environment variables, URL paths or headers that remove tools or labels,
with exact syntax, the default behaviour, and evidence."""

EFFECT_SCHEMA = {
    "type": "object",
    "properties": {
        "tools": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "labels": {"type": "array", "items": {"type": "string", "enum": ["SECRET", "UNTRUSTED", "SINK", "HOSTEXEC"]}},
                    "evidence": {"type": "array", "items": {"type": "string"}},
                    "rationale": {"type": "string"},
                    "default_enabled": {"type": "boolean"},
                    "undetermined": {"type": "boolean"},
                },
                "required": ["name", "labels", "evidence", "rationale", "default_enabled", "undetermined"],
                "additionalProperties": False,
            },
        },
        "value_conditions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"syntax": {"type": "string"}, "kind": {"type": "string"},
                               "effect": {"type": "string"}, "default": {"type": "string"}, "evidence": {"type": "string"}},
                "required": ["syntax", "kind", "effect", "default", "evidence"],
                "additionalProperties": False,
            },
        },
        "notes": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["tools", "value_conditions", "notes"],
    "additionalProperties": False,
}

MAX_TOOLS_CHARS = 400_000
USAGE_FIELDS = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")

def usage_dict(usage) -> dict:
    """The four token counts interlock tracks, read off an SDK usage object. A field the
    object doesn't carry (e.g. no prompt caching used) counts as zero, not missing."""
    return {f: getattr(usage, f, 0) or 0 for f in USAGE_FIELDS}

def build_messages(surface: dict, source_files: list[tuple[str, str]]) -> list[dict]:
    code = "\n\n".join(f"===== {rel} =====\n{text}" for rel, text in source_files)
    tools = json.dumps(surface["tools"], separators=(",", ":"))
    if len(tools) > MAX_TOOLS_CHARS:
        raise ValueError(
            f"{surface['package']}: {len(surface['tools'])} tools serialise to {len(tools)} chars, "
            f"exceeding MAX_TOOLS_CHARS={MAX_TOOLS_CHARS}; refusing to silently truncate the tool list"
        )
    return [{"role": "user", "content": [
        {"type": "text",
         "text": f"Server {surface['package']}@{surface['version']} ({surface['kind']}). Source follows.\n\n{code}",
         "cache_control": {"type": "ephemeral"}},
        {"type": "text",
         "text": f"Advertised tools (judge every one of them):\n{tools}"},
    ]}]

def _params(surface, source_files, model):
    return dict(
        model=model,
        max_tokens=16000,
        system=[{"type": "text", "text": RUBRIC, "cache_control": {"type": "ephemeral"}}],
        messages=build_messages(surface, source_files),
        output_config={"format": {"type": "json_schema", "schema": EFFECT_SCHEMA}},
    )

def parse_effects(text: str) -> dict:
    """Parse the model's JSON and return it with `tools` keyed by tool name, the shape verify() consumes."""
    data = json.loads(text)
    notes = list(data.get("notes", []))
    tools: dict = {}
    for item in data["tools"]:
        name = item["name"]
        if name in tools:
            notes.append(f"duplicate judgement for tool {name} ignored")
            continue
        tools[name] = {k: v for k, v in item.items() if k != "name"}
    data["tools"] = tools
    data["notes"] = notes
    return data

def adjudicate(surface: dict, source_files: list[tuple[str, str]], client, model: str = "claude-opus-5",
              usage_out: dict | None = None) -> dict:
    response = client.messages.create(**_params(surface, source_files, model))
    text = next((b.text for b in response.content if b.type == "text"), None)
    if text is None:
        raise ValueError(
            f"{surface['package']}: no text block in response "
            f"(stop_reason={getattr(response, 'stop_reason', None)!r})"
        )
    if usage_out is not None:
        usage_out.update(usage_dict(response.usage))
    return parse_effects(text)

def batch_request(custom_id: str, surface: dict, source_files: list[tuple[str, str]], model: str = "claude-opus-5"):
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request
    return Request(custom_id=custom_id, params=MessageCreateParamsNonStreaming(**_params(surface, source_files, model)))
