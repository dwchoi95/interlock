"""Ask a model to judge each tool's effects, with mandatory file:line evidence."""
from __future__ import annotations
import json
from pathlib import Path

_HEAD_CODE = "You judge what an MCP server's advertised tools can reach, by reading its source code."
_HEAD_DESCRIPTION = ("You judge what an MCP server's advertised tools can reach, from the text the server advertises: "
                     "each tool's name, description, input schema and annotations. No source code is available.")

_THREAT_MODEL = """Threat model. The agent follows injected instructions and may pass any tool's output into any tool's input.
An attacker controls arbitrary web hosts and content, and content in multi-writer or third-party sources
(public issues and comments, community documentation or registries, search results). An attacker does not
control the user's local files or the data in the user's own accounts. An attacker can read hosts they
control and anything published publicly."""

_LABELS = """Labels.
SECRET: the tool returns non-public data into the agent's context - host files, databases or SaaS data
reached with the user's credentials, or content of file://, localhost or intranet URLs it can reach.
UNTRUSTED: the tool returns attacker-controllable content.
SINK: the tool can deliver agent-supplied data to an attacker-readable destination - a request to an
agent-chosen URL or host, a write to a public repository, issue, comment or gist, or a message to an
agent-chosen recipient. A query sent to a fixed vendor endpoint is not a SINK.
HOSTEXEC: arbitrary code execution on the host, including escapable sandboxes. Implies the other three.
Code confined to a browser page or a remote vendor sandbox is not HOSTEXEC; label its actual reach."""

_ENFORCEMENT = """Enforcement fields. Besides the labels, record for every tool what a call-time guard reads (empty lists when none).
kind: WRITE if the tool changes state anywhere or sends data out of the agent's context (sending, posting,
creating, updating, deleting, paying, booking, inviting, executing); READ otherwise.
destination_args: for a WRITE, the argument names that decide where the data goes or which record is changed -
recipients, addresses, URLs, hosts, channels, user names, account numbers, and the identifier of the record that
is written, updated or deleted. For a READ, only an argument that makes the tool contact an agent-chosen host
(a URL), because that request itself carries data out. Search terms, filters, dates, queries and names that only
select what to read are not destinations. Use only argument names the input schema declares.
value_args: the argument names that carry content the tool sends or stores - body, text, content, subject, amount.
injectable_output_fields: result fields whose text an outside party can author, whatever they are called - the
body or subject of a message another user wrote, an issue, comment or review, a web page, a shared file, a
transaction subject set by the payer. Write "return" when the whole result is such text.
identifier_output_fields: every result field whose value a later call can take as an argument - ids, names,
titles, addresses, user and channel names, account numbers, file names, URLs, keys - and the keys of a returned
mapping. Write "return" when the result is one identifier or a list of them. A field that is both an identifier
and attacker-authored belongs in both lists.
attacker_named_fields: the subset of identifier_output_fields whose value the author of a message or record set:
the sender of a received message, the payer of an incoming transaction, the author of a comment or review. The
user's own contacts, participants the user invited, files the user owns and identifiers the system assigned are
not attacker-named."""

_RULES_CODE = """Rules.
Judge only from code. Every label must cite evidence as file:line or file:start-end, pointing at the line
that shows the behaviour. Cite identifiers you rely on in the rationale inside backticks.
A tool's free-text description may raise a label but never removes one.
If the code does not settle a question, set undetermined true, keep the least restrictive reading (that is,
keep the label), and say why in the rationale.
The enforcement fields follow the same rule: name only arguments and result fields the code shows.
default_enabled is false when a tool is only advertised behind a flag, capability or toolset that is off by default.
value_conditions list the flags, environment variables, URL paths or headers that remove tools or labels,
with exact syntax, the default behaviour, and evidence."""

_RULES_DESCRIPTION = """Rules.
Judge only from the advertised text, as a policy generator that never sees the implementation would. Write the
string "description" as the evidence of every judgement. Cite the words you rely on in the rationale inside
backticks.
If the text does not settle a question, set undetermined true, keep the least restrictive reading (that is,
keep the label), and say why in the rationale.
default_enabled is true unless the text says a tool is off by default.
value_conditions list the flags, environment variables, URL paths or headers the text says remove tools or labels."""

# The threat model is an input of the stage. The default follows the lethal-trifecta framing: the
# user's own files and accounts are private. "received-documents" is the variant in which documents
# the user received from other parties and keeps among their files (bills, notices, shared or
# downloaded files, attachments) are written by those parties - the threat model under which
# AgentDojo plants its injections in such files.
_OWN_FILES = ("An attacker does not\ncontrol the user's local files or the data in the user's own accounts.")
_RECEIVED_DOCUMENTS = ("An attacker does not\ncontrol files the user wrote or the data in the user's own accounts, but documents the user\n"
                       "received from other parties and keeps among their files or accounts (bills, invoices, notices, shared or\n"
                       "downloaded files, attachments) are written by those parties, so an attacker can author their content.")
assert _OWN_FILES in _THREAT_MODEL
THREAT_MODELS = {"default": _THREAT_MODEL, "received-documents": _THREAT_MODEL.replace(_OWN_FILES, _RECEIVED_DOCUMENTS)}


def rubric(threat_model: str = "default", description_only: bool = False) -> str:
    tm = THREAT_MODELS[threat_model]
    if description_only:
        return "\n\n".join([_HEAD_DESCRIPTION, tm, _LABELS, _ENFORCEMENT, _RULES_DESCRIPTION])
    return "\n\n".join([_HEAD_CODE, tm, _LABELS, _ENFORCEMENT, _RULES_CODE])


RUBRIC = rubric()
# The same rubric with the code withheld: what a description-reading policy generator would conclude.
RUBRIC_DESCRIPTION_ONLY = rubric(description_only=True)

_STRING_LIST = {"type": "array", "items": {"type": "string"}}
_TOOL_PROPERTIES = {
    "name": {"type": "string"},
    "labels": {"type": "array", "items": {"type": "string", "enum": ["SECRET", "UNTRUSTED", "SINK", "HOSTEXEC"]}},
    "evidence": _STRING_LIST,
    "rationale": {"type": "string"},
    "default_enabled": {"type": "boolean"},
    "undetermined": {"type": "boolean"},
    "kind": {"type": "string", "enum": ["READ", "WRITE"]},
    "destination_args": _STRING_LIST,
    "value_args": _STRING_LIST,
    "injectable_output_fields": _STRING_LIST,
    "identifier_output_fields": _STRING_LIST,
    "attacker_named_fields": _STRING_LIST,
}
EFFECT_SCHEMA = {
    "type": "object",
    "properties": {
        "tools": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": _TOOL_PROPERTIES,
                "required": list(_TOOL_PROPERTIES),
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
MAX_OUTPUT_TOKENS = 64_000
USAGE_FIELDS = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")

def usage_dict(usage) -> dict:
    """The four token counts interlock tracks, read off an SDK usage object. A field the
    object doesn't carry (e.g. no prompt caching used) counts as zero, not missing."""
    return {f: getattr(usage, f, 0) or 0 for f in USAGE_FIELDS}

def is_openai_model(model: str) -> bool:
    return model.startswith(("gpt-", "o1", "o3", "o4"))

def build_messages(surface: dict, source_files: list[tuple[str, str]]) -> list[dict]:
    tools = json.dumps(surface["tools"], separators=(",", ":"))
    if len(tools) > MAX_TOOLS_CHARS:
        raise ValueError(
            f"{surface['package']}: {len(surface['tools'])} tools serialise to {len(tools)} chars, "
            f"exceeding MAX_TOOLS_CHARS={MAX_TOOLS_CHARS}; refusing to silently truncate the tool list"
        )
    if source_files:
        code = "\n\n".join(f"===== {rel} =====\n{text}" for rel, text in source_files)
        head = (f"Server {surface['package']}@{surface['version']} ({surface['kind']}). Source follows. "
                f"Each source line is prefixed with its line number and a vertical bar; cite those numbers exactly.\n\n{code}")
    else:
        head = f"Server {surface['package']}@{surface['version']} ({surface['kind']}). No source code is provided."
    return [{"role": "user", "content": [
        {"type": "text", "text": head, "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": f"Advertised tools (judge every one of them):\n{tools}"},
    ]}]

def _params(surface, source_files, model, rubric: str = RUBRIC):
    return dict(
        model=model,
        max_tokens=MAX_OUTPUT_TOKENS,
        system=[{"type": "text", "text": rubric, "cache_control": {"type": "ephemeral"}}],
        messages=build_messages(surface, source_files),
        output_config={"format": {"type": "json_schema", "schema": EFFECT_SCHEMA}},
    )

def parse_effects(text: str, package: str = "<unknown>") -> dict:
    """Parse the model's JSON and return it with `tools` keyed by tool name, the shape verify()
    consumes. Raises ValueError naming `package` for any malformed shape - invalid JSON, a
    top-level value that isn't an object, a missing or non-list `tools`, a non-object item, or
    an item with a missing/non-string `name` - instead of a bare KeyError/TypeError/JSONDecodeError."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"{package}: invalid JSON from model: {e}") from e
    if not isinstance(data, dict):
        raise ValueError(f"{package}: expected a JSON object, got {type(data).__name__}")
    tools_raw = data.get("tools")
    if not isinstance(tools_raw, list):
        raise ValueError(f"{package}: expected `tools` to be a list, got {type(tools_raw).__name__}")
    notes = list(data.get("notes", []))
    tools: dict = {}
    for i, item in enumerate(tools_raw):
        if not isinstance(item, dict):
            raise ValueError(f"{package}: tools[{i}] is not an object (got {type(item).__name__})")
        name = item.get("name")
        if not isinstance(name, str):
            raise ValueError(f"{package}: tools[{i}] is missing a string `name`")
        if name in tools:
            notes.append(f"duplicate judgement for tool {name} ignored")
            continue
        tools[name] = {k: v for k, v in item.items() if k != "name"}
    data["tools"] = tools
    data["notes"] = notes
    return data

def _adjudicate_openai(surface: dict, source_files, client, model: str, usage_out, rubric: str) -> dict:
    """The same rubric, messages and schema through the OpenAI chat API (structured output)."""
    blocks = build_messages(surface, source_files)[0]["content"]
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": rubric},
                  {"role": "user", "content": "\n\n".join(b["text"] for b in blocks)}],
        response_format={"type": "json_schema",
                         "json_schema": {"name": "effects", "schema": EFFECT_SCHEMA, "strict": True}},
        max_completion_tokens=MAX_OUTPUT_TOKENS,
    )
    choice = response.choices[0]
    if choice.finish_reason == "length":
        raise ValueError(f"{surface['package']}: output truncated at max_tokens={MAX_OUTPUT_TOKENS}")
    text = choice.message.content
    if not text:
        raise ValueError(f"{surface['package']}: no text in response (finish_reason={choice.finish_reason!r})")
    if usage_out is not None:
        u = response.usage
        cached = getattr(getattr(u, "prompt_tokens_details", None), "cached_tokens", 0) or 0
        usage_out.update({"input_tokens": (u.prompt_tokens or 0) - cached, "output_tokens": u.completion_tokens or 0,
                          "cache_creation_input_tokens": 0, "cache_read_input_tokens": cached})
    return parse_effects(text, surface["package"])

def adjudicate(surface: dict, source_files: list[tuple[str, str]], client, model: str = "claude-opus-5",
              usage_out: dict | None = None, rubric: str = RUBRIC) -> dict:
    if is_openai_model(model):
        return _adjudicate_openai(surface, source_files, client, model, usage_out, rubric)
    # Stream: a non-streaming request with max_tokens this large risks the SDK's HTTP timeout.
    with client.messages.stream(**_params(surface, source_files, model, rubric)) as stream:
        response = stream.get_final_message()
    if response.stop_reason == "max_tokens":
        # Caught here, before parsing, so a truncated response is reported as what it is
        # rather than surfacing as a confusing "invalid JSON" error out of parse_effects.
        raise ValueError(f"{surface['package']}: output truncated at max_tokens={MAX_OUTPUT_TOKENS}")
    text = next((b.text for b in response.content if b.type == "text"), None)
    if text is None:
        raise ValueError(
            f"{surface['package']}: no text block in response "
            f"(stop_reason={getattr(response, 'stop_reason', None)!r})"
        )
    if usage_out is not None:
        usage_out.update(usage_dict(response.usage))
    return parse_effects(text, surface["package"])

def batch_request(custom_id: str, surface: dict, source_files: list[tuple[str, str]], model: str = "claude-opus-5"):
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request
    return Request(custom_id=custom_id, params=MessageCreateParamsNonStreaming(**_params(surface, source_files, model)))
