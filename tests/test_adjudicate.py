import json
import pytest
from interlock.adjudicate import RUBRIC, EFFECT_SCHEMA, MAX_TOOLS_CHARS, build_messages, adjudicate, parse_effects

SURFACE = {"package": "a", "version": "1", "kind": "npm",
           "tools": [{"name": "read_file", "description": "d", "inputSchema": {"type": "object"}, "annotations": None}]}
FILES = [("src/server.js", "fs.readFileSync(p)\n")]

def test_rubric_carries_label_definitions():
    for label in ("SECRET", "UNTRUSTED", "SINK", "HOSTEXEC"):
        assert label in RUBRIC
    assert "file:line" in RUBRIC

def test_schema_requires_evidence_per_tool():
    tool = EFFECT_SCHEMA["properties"]["tools"]["items"]
    assert set(tool["required"]) >= {"name", "labels", "evidence", "rationale", "default_enabled", "undetermined"}
    assert tool["additionalProperties"] is False

def test_schema_every_object_forbids_additional_properties():
    """This is the check that would have caught the real-API rejection: structured outputs
    require every object node in the schema to set additionalProperties exactly False."""
    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node.get("additionalProperties") is False, node
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(EFFECT_SCHEMA)

def test_build_messages_caches_stable_prefix_and_lists_every_tool():
    msgs = build_messages(SURFACE, FILES)
    blocks = msgs[0]["content"]
    assert blocks[0]["cache_control"] == {"type": "ephemeral"}      # source context is cached per package
    body = json.dumps(msgs)
    assert "read_file" in body and "src/server.js" in body

def test_build_messages_instructs_model_to_cite_line_numbers():
    msgs = build_messages(SURFACE, FILES)
    intro = msgs[0]["content"][0]["text"]
    assert "Each source line is prefixed with its line number and a vertical bar; cite those numbers exactly." in intro

def test_adjudicate_parses_structured_output():
    class FakeClient:
        class messages:
            @staticmethod
            def create(**kw):
                assert kw["model"] == "claude-opus-5"
                assert kw["output_config"]["format"]["type"] == "json_schema"
                assert "thinking" not in kw
                class Block: type = "text"; text = json.dumps(
                    {"tools": [{"name": "read_file", "labels": ["SECRET"], "evidence": ["src/server.js:1"],
                                "rationale": "calls `readFileSync`", "default_enabled": True, "undetermined": False}],
                     "value_conditions": [], "notes": []})
                class R: content = [Block()]
                return R()
    out = adjudicate(SURFACE, FILES, client=FakeClient())
    assert out["tools"]["read_file"]["labels"] == ["SECRET"]

def test_build_messages_rejects_tool_list_over_the_char_limit():
    tool = {"name": "t", "description": "x" * 1000, "inputSchema": {"type": "object"}, "annotations": None}
    per_tool = len(json.dumps(tool, separators=(",", ":")))
    huge = {"package": "big-pkg", "version": "1", "kind": "npm",
            "tools": [tool] * (MAX_TOOLS_CHARS // per_tool + 10)}
    with pytest.raises(ValueError, match="big-pkg"):
        build_messages(huge, FILES)

def test_adjudicate_fills_usage_out_from_response_usage():
    class FakeClient:
        class messages:
            @staticmethod
            def create(**kw):
                class Block: type = "text"; text = json.dumps(
                    {"tools": [{"name": "read_file", "labels": ["SECRET"], "evidence": ["src/server.js:1"],
                                "rationale": "calls `readFileSync`", "default_enabled": True, "undetermined": False}],
                     "value_conditions": [], "notes": []})
                class Usage:
                    input_tokens = 1200
                    output_tokens = 340
                    cache_creation_input_tokens = 500
                    cache_read_input_tokens = 0
                class R: content = [Block()]; usage = Usage()
                return R()
    usage_out: dict = {}
    adjudicate(SURFACE, FILES, client=FakeClient(), usage_out=usage_out)
    assert usage_out == {"input_tokens": 1200, "output_tokens": 340,
                          "cache_creation_input_tokens": 500, "cache_read_input_tokens": 0}

def test_adjudicate_usage_out_defaults_missing_cache_fields_to_zero():
    class FakeClient:
        class messages:
            @staticmethod
            def create(**kw):
                class Block: type = "text"; text = json.dumps(
                    {"tools": [{"name": "read_file", "labels": ["SECRET"], "evidence": ["src/server.js:1"],
                                "rationale": "calls `readFileSync`", "default_enabled": True, "undetermined": False}],
                     "value_conditions": [], "notes": []})
                class Usage:
                    input_tokens = 100
                    output_tokens = 20
                    # no cache_creation_input_tokens / cache_read_input_tokens on this usage object
                class R: content = [Block()]; usage = Usage()
                return R()
    usage_out: dict = {}
    adjudicate(SURFACE, FILES, client=FakeClient(), usage_out=usage_out)
    assert usage_out == {"input_tokens": 100, "output_tokens": 20,
                          "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}

def test_adjudicate_raises_valueerror_when_no_text_block():
    class FakeClient:
        class messages:
            @staticmethod
            def create(**kw):
                class Block: type = "other"
                class R:
                    content = [Block()]
                    stop_reason = "refusal"
                return R()
    with pytest.raises(ValueError, match="refusal") as excinfo:
        adjudicate(SURFACE, FILES, client=FakeClient())
    assert SURFACE["package"] in str(excinfo.value)

def test_parse_effects_keys_tools_by_name_and_drops_duplicates():
    text = json.dumps({"tools": [
        {"name": "read_file", "labels": ["SECRET"], "evidence": ["a:1"], "rationale": "r1",
         "default_enabled": True, "undetermined": False},
        {"name": "write_file", "labels": ["SINK"], "evidence": ["a:2"], "rationale": "r2",
         "default_enabled": True, "undetermined": False},
        {"name": "read_file", "labels": ["HOSTEXEC"], "evidence": ["a:3"], "rationale": "r3-dup",
         "default_enabled": True, "undetermined": False},
    ], "value_conditions": [], "notes": ["pre-existing note"]})
    out = parse_effects(text)
    assert set(out["tools"]) == {"read_file", "write_file"}
    assert "name" not in out["tools"]["read_file"]
    assert out["tools"]["read_file"]["rationale"] == "r1"          # first occurrence kept
    assert out["tools"]["write_file"]["labels"] == ["SINK"]
    assert out["notes"] == ["pre-existing note", "duplicate judgement for tool read_file ignored"]
