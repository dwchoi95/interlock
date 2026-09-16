from pathlib import Path
from interlock.pipeline import verify

SURFACE = {"package": "a", "version": "1", "kind": "npm", "tools": [
    {"name": "read_file", "description": "", "inputSchema": {}, "annotations": None},
    {"name": "post_note", "description": "", "inputSchema": {}, "annotations": None},
    {"name": "forgotten", "description": "", "inputSchema": {}, "annotations": None}]}

RAW = {"tools": {
    "read_file": {"labels": ["SECRET"], "evidence": ["src/s.js:1"], "rationale": "calls `readFileSync`",
                  "default_enabled": True, "undetermined": False},
    "post_note": {"labels": ["SINK"], "evidence": ["src/s.js:9"], "rationale": "posts to `fetch`",
                  "default_enabled": True, "undetermined": False}},
    "value_conditions": [], "notes": []}

def tree(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "s.js").write_text("fs.readFileSync(p)\n")
    return tmp_path

def test_verified_claim_stays_determined(tmp_path):
    profile, stats = verify(RAW, SURFACE, tree(tmp_path))
    assert profile.tools["read_file"].undetermined is False
    assert stats["verified"] == 1 and stats["claims"] == 2

def test_unverifiable_claim_is_demoted_but_keeps_labels(tmp_path):
    profile, stats = verify(RAW, SURFACE, tree(tmp_path))
    assert profile.tools["post_note"].undetermined is True
    assert profile.tools["post_note"].labels == ["SINK"]
    assert stats["demoted"] == 1

def test_unjudged_tool_takes_the_least_restrictive_reading(tmp_path):
    profile, stats = verify(RAW, SURFACE, tree(tmp_path))
    assert profile.tools["forgotten"].labels == ["HOSTEXEC"]
    assert profile.tools["forgotten"].undetermined is True
    assert stats["missing_tools"] == ["forgotten"]

def test_profile_union_reflects_demotions(tmp_path):
    profile, _ = verify(RAW, SURFACE, tree(tmp_path))
    assert {"SECRET", "SINK", "UNTRUSTED", "HOSTEXEC"} == profile.union()
