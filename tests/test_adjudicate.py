import json
from interlock.adjudicate import RUBRIC, EFFECT_SCHEMA, build_messages, adjudicate

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
