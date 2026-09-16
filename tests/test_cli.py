import json
import re
from pathlib import Path
from interlock.cli import main, _custom_id

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


def test_custom_id_is_sanitised_and_stays_unique_after_truncation():
    cid = _custom_id("pypi", "awslabs.aws-documentation-mcp-server")
    assert re.fullmatch(r"[A-Za-z0-9_-]{1,64}", cid)

    long_a = _custom_id("npm", "x" * 100 + "AAAA")
    long_b = _custom_id("npm", "x" * 100 + "BBBB")
    assert long_a != long_b
    assert re.fullmatch(r"[A-Za-z0-9_-]{1,64}", long_a)
    assert re.fullmatch(r"[A-Za-z0-9_-]{1,64}", long_b)


def _seed_source(cache_dir, kind, package, version):
    root = cache_dir / f"{kind}_{package}_{version}" / "src"
    root.mkdir(parents=True)
    (root / "s.js").write_text("fs.readFileSync(p)\n")


def test_batch_writes_manifest_with_batch_id_and_one_entry_per_package(tmp_path, monkeypatch):
    surfaces = tmp_path / "surfaces.jsonl"
    rows = [
        {"kind": "npm", "pkg": "a", "version": "1.0.0", "published": "2026-01-01T00:00:00Z", "ok": True,
         "tools": [{"name": "read_file", "description": "", "inputSchema": {}, "annotations": None}]},
        {"kind": "npm", "pkg": "b", "version": "2.0.0", "published": "2026-01-01T00:00:00Z", "ok": True,
         "tools": [{"name": "read_file", "description": "", "inputSchema": {}, "annotations": None}]},
    ]
    surfaces.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    _seed_source(tmp_path / "cache", "npm", "a", "1.0.0")
    _seed_source(tmp_path / "cache", "npm", "b", "2.0.0")
    packages = tmp_path / "packages.txt"
    packages.write_text("npm:a\nnpm:b\n")

    class FakeBatches:
        def __init__(self):
            self.created_requests = None
        def create(self, **kw):
            self.created_requests = kw["requests"]
            class B: id = "batch_abc123"
            return B()
        def retrieve(self, batch_id):
            class S: processing_status = "ended"; request_counts = {"succeeded": 2}
            return S()
        def results(self, batch_id):
            return []
    class FakeMessages:
        def __init__(self):
            self.batches = FakeBatches()
    class FakeClient:
        def __init__(self):
            self.messages = FakeMessages()

    fake = FakeClient()
    monkeypatch.setattr("interlock.cli.make_client", lambda: fake)
    rc = main(["batch", str(packages), "--surfaces", str(surfaces),
               "--cache", str(tmp_path / "cache"), "--out", str(tmp_path / "profiles")])
    assert rc == 0

    manifest = json.loads((tmp_path / "profiles" / "batch-manifest.json").read_text())
    assert manifest["batch_id"] == "batch_abc123"
    assert len(manifest["packages"]) == 2
    packages_seen = {(e["kind"], e["package"]) for e in manifest["packages"]}
    assert packages_seen == {("npm", "a"), ("npm", "b")}
    assert len(fake.messages.batches.created_requests) == 2
    manifest_ids = {e["custom_id"] for e in manifest["packages"]}
    request_ids = {r["custom_id"] for r in fake.messages.batches.created_requests}
    assert manifest_ids == request_ids


def test_resume_skips_submission_and_writes_profiles_from_results(tmp_path, monkeypatch):
    surfaces = tmp_path / "surfaces.jsonl"
    surfaces.write_text(json.dumps({"kind": "npm", "pkg": "a", "version": "1.0.0", "published": "2026-01-01T00:00:00Z",
                                    "ok": True, "tools": [{"name": "read_file", "description": "", "inputSchema": {}, "annotations": None}]}) + "\n")
    root = tmp_path / "cache" / "npm_a_1.0.0"
    (root / "src").mkdir(parents=True)
    (root / "src" / "s.js").write_text("fs.readFileSync(p)\n")

    out_dir = tmp_path / "profiles"
    out_dir.mkdir()
    manifest = {"batch_id": "batch_resumed", "packages": [
        {"custom_id": "npm_a-deadbeef", "kind": "npm", "package": "a", "version": "1.0.0", "root": str(root)}]}
    (out_dir / "batch-manifest.json").write_text(json.dumps(manifest))

    class FakeBatches:
        def create(self, **kw):
            raise AssertionError("batches.create must not be called on resume")
        def retrieve(self, batch_id):
            assert batch_id == "batch_resumed"
            class S: processing_status = "ended"; request_counts = {"succeeded": 1}
            return S()
        def results(self, batch_id):
            class Block: type = "text"; text = json.dumps({"tools": {"read_file": {
                "labels": ["SECRET"], "evidence": ["src/s.js:1"], "rationale": "calls `readFileSync`",
                "default_enabled": True, "undetermined": False}}, "value_conditions": [], "notes": []})
            class Message: content = [Block()]
            class Result: type = "succeeded"; message = Message()
            class R: custom_id = "npm_a-deadbeef"; result = Result()
            return [R()]
    class FakeMessages:
        batches = FakeBatches()
    class FakeClient:
        messages = FakeMessages()

    monkeypatch.setattr("interlock.cli.make_client", lambda: FakeClient())

    rc = main(["batch", str(tmp_path / "unused.txt"), "--resume", "batch_resumed",
               "--surfaces", str(surfaces), "--cache", str(tmp_path / "cache"), "--out", str(out_dir)])
    assert rc == 0
    written = json.loads((out_dir / "npm_a_1.0.0.json").read_text())
    assert written["tools"]["read_file"]["labels"] == ["SECRET"]
