import json
import pytest
from interlock.adjudicate import RUBRIC, EFFECT_SCHEMA, MAX_TOOLS_CHARS, build_messages, adjudicate

SURFACE = {"package": "a", "version": "1", "kind": "npm",
           "tools": [{"name": "read_file", "description": "d", "inputSchema": {"type": "object"}, "annotations": None}]}
FILES = [("src/server.js", "fs.readFileSync(p)\n")]

def test_rubric_carries_label_definitions():
    for label in ("SECRET", "UNTRUSTED", "SINK", "HOSTEXEC"):
        assert label in RUBRIC
    assert "file:line" in RUBRIC

def test_schema_requires_evidence_per_tool():
    tool = EFFECT_SCHEMA["properties"]["tools"]["additionalProperties"]
    assert set(tool["required"]) >= {"labels", "evidence", "rationale", "default_enabled"}
    assert tool["additionalProperties"] is False

def test_build_messages_caches_stable_prefix_and_lists_every_tool():
    msgs = build_messages(SURFACE, FILES)
    blocks = msgs[0]["content"]
    assert blocks[0]["cache_control"] == {"type": "ephemeral"}      # source context is cached per package
    body = json.dumps(msgs)
    assert "read_file" in body and "src/server.js" in body

def test_adjudicate_parses_structured_output():
    class FakeClient:
        class messages:
            @staticmethod
            def create(**kw):
                assert kw["model"] == "claude-opus-5"
                assert kw["output_config"]["format"]["type"] == "json_schema"
                assert "thinking" not in kw
                class Block: type = "text"; text = json.dumps(
                    {"tools": {"read_file": {"labels": ["SECRET"], "evidence": ["src/server.js:1"],
                                             "rationale": "calls `readFileSync`", "default_enabled": True}},
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
                    {"tools": {"read_file": {"labels": ["SECRET"], "evidence": ["src/server.js:1"],
                                             "rationale": "calls `readFileSync`", "default_enabled": True}},
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
                    {"tools": {"read_file": {"labels": ["SECRET"], "evidence": ["src/server.js:1"],
                                             "rationale": "calls `readFileSync`", "default_enabled": True}},
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
