import json
from pathlib import Path
from interlock.cli import main

def test_profile_subcommand_writes_profile_and_stats(tmp_path, monkeypatch):
    surfaces = tmp_path / "surfaces.jsonl"
    surfaces.write_text(json.dumps({"kind": "npm", "pkg": "a", "version": "1.0.0", "published": "2026-01-01T00:00:00Z",
                                    "ok": True, "tools": [{"name": "read_file", "description": "", "inputSchema": {}, "annotations": None}]}) + "\n")
    src = tmp_path / "cache" / "npm_a_1.0.0" / "src"
    src.mkdir(parents=True)
    (src / "s.js").write_text("fs.readFileSync(p)\n")

    class FakeClient:
        class messages:
            @staticmethod
            def create(**kw):
                class Block: type = "text"; text = json.dumps({"tools": {"read_file": {
                    "labels": ["SECRET"], "evidence": ["src/s.js:1"], "rationale": "calls `readFileSync`",
                    "default_enabled": True, "undetermined": False}}, "value_conditions": [], "notes": []})
                class R: content = [Block()]
                return R()
    monkeypatch.setattr("interlock.cli.make_client", lambda: FakeClient())

    rc = main(["profile", "npm:a", "--surfaces", str(surfaces), "--cache", str(tmp_path / "cache"), "--out", str(tmp_path / "profiles")])
    assert rc == 0
    written = json.loads((tmp_path / "profiles" / "npm_a_1.0.0.json").read_text())
    assert written["tools"]["read_file"]["labels"] == ["SECRET"]
    stats = [json.loads(l) for l in (tmp_path / "profiles" / "stats.jsonl").open()]
    assert stats[0]["verified"] == 1 and stats[0]["package"] == "a"
