import json
import pytest
from interlock.surface import load_surface, latest_version

def fixture(tmp_path):
    p = tmp_path / "surfaces.jsonl"
    rows = [
        {"kind": "npm", "pkg": "a", "version": "1.0.0", "published": "2025-05-01T00:00:00Z", "ok": True,
         "tools": [{"name": "t1", "description": "d", "inputSchema": {"type": "object"}, "annotations": None}]},
        {"kind": "npm", "pkg": "a", "version": "2.0.0", "published": "2026-01-01T00:00:00Z", "ok": True,
         "tools": [{"name": "t2", "description": "d", "inputSchema": {"type": "object"}, "annotations": None}]},
        {"kind": "npm", "pkg": "a", "version": "3.0.0", "published": "2026-02-01T00:00:00Z", "ok": False, "tools": []},
    ]
    p.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return p

def test_latest_version_skips_failed_runs(tmp_path):
    assert latest_version(fixture(tmp_path), "a") == "2.0.0"

def test_load_specific_version(tmp_path):
    s = load_surface(fixture(tmp_path), "a", "1.0.0")
    assert [t["name"] for t in s["tools"]] == ["t1"] and s["kind"] == "npm"

def test_missing_package_raises(tmp_path):
    with pytest.raises(KeyError):
        load_surface(fixture(tmp_path), "nope")
