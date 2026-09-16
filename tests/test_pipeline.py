from pathlib import Path
import pytest
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


SURFACE_CLEARED = {"package": "a", "version": "1", "kind": "npm", "tools": [
    {"name": "safe_read", "description": "", "inputSchema": {}, "annotations": None},
    {"name": "vague_tool", "description": "", "inputSchema": {}, "annotations": None}]}

RAW_CLEARED = {"tools": {
    "safe_read": {"labels": [], "evidence": ["src/s.js:1"],
                  "rationale": "just calls `readFileSync` on a fixed path, nothing leaves the process",
                  "default_enabled": True, "undetermined": False},
    "vague_tool": {"labels": [], "evidence": ["src/s.js:1"], "rationale": "seems harmless",
                   "default_enabled": True, "undetermined": False}},
    "value_conditions": [], "notes": []}

def test_cleared_tool_is_verified_like_a_claim(tmp_path):
    profile, stats = verify(RAW_CLEARED, SURFACE_CLEARED, tree(tmp_path))
    assert stats["cleared_verified"] == 1
    assert stats["cleared_unverified"] == 1
    assert profile.tools["safe_read"].labels == []
    assert profile.tools["safe_read"].undetermined is False
    assert profile.tools["vague_tool"].labels == []
    assert profile.tools["vague_tool"].undetermined is True
    assert "[unverified:" in profile.tools["vague_tool"].rationale

def test_accounting_invariant_across_all_categories(tmp_path):
    surface = {"package": "a", "version": "1", "kind": "npm", "tools": SURFACE["tools"] + SURFACE_CLEARED["tools"]}
    raw = {"tools": {**RAW["tools"], **RAW_CLEARED["tools"]}, "value_conditions": [], "notes": []}
    profile, stats = verify(raw, surface, tree(tmp_path))
    assert stats["tools"] == (stats["claims"] + stats["cleared_verified"]
                               + stats["cleared_unverified"] + len(stats["missing_tools"]))

def test_unknown_judged_tool_is_reported_and_excluded(tmp_path):
    raw = {"tools": {**RAW["tools"], "phantom_tool": {
        "labels": ["SINK"], "evidence": ["src/s.js:1"], "rationale": "invented",
        "default_enabled": True, "undetermined": False}}, "value_conditions": [], "notes": []}
    profile, stats = verify(raw, SURFACE, tree(tmp_path))
    assert stats["unknown_tools"] == ["phantom_tool"]
    assert "phantom_tool" not in profile.tools

def test_invalid_label_names_package_and_tool_in_the_error(tmp_path):
    raw = {"tools": {"read_file": {
        "labels": ["NOT_A_LABEL"], "evidence": ["src/s.js:1"], "rationale": "x",
        "default_enabled": True, "undetermined": False}}, "value_conditions": [], "notes": []}
    with pytest.raises(ValueError) as exc:
        verify(raw, SURFACE, tree(tmp_path))
    message = str(exc.value)
    assert SURFACE["package"] in message
    assert "read_file" in message
