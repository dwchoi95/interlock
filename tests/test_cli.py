import json
import re
from pathlib import Path
from types import SimpleNamespace
from interlock.cli import main, _custom_id, _prepare_batch, estimate_cost_usd, PRICE_PER_MTOK
from interlock.source import _slug, source_digest

def test_profile_subcommand_writes_profile_and_stats(tmp_path, monkeypatch):
    surfaces = tmp_path / "surfaces.jsonl"
    surfaces.write_text(json.dumps({"kind": "npm", "pkg": "a", "version": "1.0.0", "published": "2026-01-01T00:00:00Z",
                                    "ok": True, "tools": [{"name": "read_file", "description": "", "inputSchema": {}, "annotations": None}]}) + "\n")
    src = tmp_path / "cache" / _slug("npm", "a", "1.0.0") / "src"
    src.mkdir(parents=True)
    (src / "s.js").write_text("fs.readFileSync(p)\n")

    class _FakeStream:
        def __init__(self, response):
            self._response = response
        def __enter__(self):
            return self
        def __exit__(self, *exc):
            return False
        def get_final_message(self):
            return self._response

    class FakeClient:
        class messages:
            @staticmethod
            def stream(**kw):
                class Block: type = "text"; text = json.dumps({"tools": [{"name": "read_file",
                    "labels": ["SECRET"], "evidence": ["src/s.js:1"], "rationale": "calls `readFileSync`",
                    "default_enabled": True, "undetermined": False}], "value_conditions": [], "notes": []})
                class Usage:
                    input_tokens = 1000
                    output_tokens = 200
                    cache_creation_input_tokens = 0
                    cache_read_input_tokens = 0
                class R: content = [Block()]; usage = Usage(); stop_reason = "end_turn"
                return _FakeStream(R())
    monkeypatch.setattr("interlock.cli.make_client", lambda: FakeClient())

    rc = main(["profile", "npm:a", "--surfaces", str(surfaces), "--cache", str(tmp_path / "cache"), "--out", str(tmp_path / "profiles")])
    assert rc == 0
    written = json.loads((tmp_path / "profiles" / "npm_a_1.0.0.json").read_text())
    assert written["tools"]["read_file"]["labels"] == ["SECRET"]
    stats = [json.loads(l) for l in (tmp_path / "profiles" / "stats.jsonl").open()]
    assert stats[0]["verified"] == 1 and stats[0]["package"] == "a"
    assert stats[0]["usage"] == {"input_tokens": 1000, "output_tokens": 200,
                                  "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
    assert stats[0]["cost_usd"] > 0
    assert stats[0]["wall_seconds"] >= 0


def test_estimate_cost_usd_matches_hand_computed_value_and_batch_halves_it():
    usage = {"input_tokens": 1_000_000, "output_tokens": 1_000_000,
             "cache_creation_input_tokens": 1_000_000, "cache_read_input_tokens": 1_000_000}
    p = PRICE_PER_MTOK["claude-opus-5"]
    expected = (usage["input_tokens"] * p["input"]
                + usage["cache_creation_input_tokens"] * p["input"] * 1.25
                + usage["cache_read_input_tokens"] * p["input"] * 0.10
                + usage["output_tokens"] * p["output"]) / 1_000_000
    assert estimate_cost_usd(usage) == round(expected, 6)
    assert estimate_cost_usd(usage, batch=True) == round(expected * 0.5, 6)


def test_estimate_cost_usd_hardcoded_figures():
    usage = {"input_tokens": 1000, "output_tokens": 500,
             "cache_creation_input_tokens": 200, "cache_read_input_tokens": 300}
    assert estimate_cost_usd(usage) == 0.0189
    assert estimate_cost_usd(usage, batch=True) == 0.00945
    assert estimate_cost_usd({"output_tokens": 1_000_000}) == 25.0
    assert estimate_cost_usd({"cache_read_input_tokens": 1_000_000}) == 0.5


def test_custom_id_is_sanitised_and_stays_unique_after_truncation():
    cid = _custom_id("pypi", "awslabs.aws-documentation-mcp-server")
    assert re.fullmatch(r"[A-Za-z0-9_-]{1,64}", cid)

    long_a = _custom_id("npm", "x" * 100 + "AAAA")
    long_b = _custom_id("npm", "x" * 100 + "BBBB")
    assert long_a != long_b
    assert re.fullmatch(r"[A-Za-z0-9_-]{1,64}", long_a)
    assert re.fullmatch(r"[A-Za-z0-9_-]{1,64}", long_b)


def _seed_source(cache_dir, kind, package, version):
    root = cache_dir / _slug(kind, package, version) / "src"
    root.mkdir(parents=True)
    (root / "s.js").write_text("fs.readFileSync(p)\n")


def _batch_result(custom_id, text, usage=None, stop_reason="end_turn"):
    usage = usage or {"input_tokens": 100, "output_tokens": 20,
                       "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
    block = SimpleNamespace(type="text", text=text)
    message = SimpleNamespace(content=[block], usage=SimpleNamespace(**usage), stop_reason=stop_reason)
    result = SimpleNamespace(type="succeeded", message=message)
    return SimpleNamespace(custom_id=custom_id, result=result)


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


def test_batch_continues_past_malformed_result_and_records_failure(tmp_path, monkeypatch):
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

    good_text = json.dumps({"tools": [{"name": "read_file", "labels": ["SECRET"], "evidence": ["src/s.js:1"],
                                        "rationale": "calls `readFileSync`", "default_enabled": True, "undetermined": False}],
                             "value_conditions": [], "notes": []})
    cid_a, cid_b = _custom_id("npm", "a"), _custom_id("npm", "b")

    class FakeBatches:
        def create(self, **kw):
            class B: id = "batch_mixed"
            return B()
        def retrieve(self, batch_id):
            class S: processing_status = "ended"; request_counts = {"succeeded": 2}
            return S()
        def results(self, batch_id):
            return [_batch_result(cid_a, good_text), _batch_result(cid_b, "{not valid json")]
    class FakeMessages:
        batches = FakeBatches()
    class FakeClient:
        messages = FakeMessages()

    monkeypatch.setattr("interlock.cli.make_client", lambda: FakeClient())
    rc = main(["batch", str(packages), "--surfaces", str(surfaces),
               "--cache", str(tmp_path / "cache"), "--out", str(tmp_path / "profiles")])
    assert rc == 0

    assert (tmp_path / "profiles" / "npm_a_1.0.0.json").exists()
    assert not (tmp_path / "profiles" / "npm_b_2.0.0.json").exists()
    stats = [json.loads(l) for l in (tmp_path / "profiles" / "stats.jsonl").open()]
    assert len(stats) == 1 and stats[0]["package"] == "a"

    failures = [json.loads(l) for l in (tmp_path / "profiles" / "batch-failures.jsonl").open()]
    assert len(failures) == 1
    assert failures[0]["package"] == "b" and failures[0]["custom_id"] == cid_b
    assert failures[0]["batch_id"] == "batch_mixed" and failures[0]["stage"] == "result"
    assert "invalid JSON" in failures[0]["message"]


def test_batch_records_truncated_result_as_failure_and_keeps_going(tmp_path, monkeypatch):
    surfaces = tmp_path / "surfaces.jsonl"
    rows = [
        {"kind": "npm", "pkg": "a", "version": "1.0.0", "published": "2026-01-01T00:00:00Z", "ok": True,
         "tools": [{"name": "read_file", "description": "", "inputSchema": {}, "annotations": None}]},
        {"kind": "npm", "pkg": "big", "version": "1.0.0", "published": "2026-01-01T00:00:00Z", "ok": True,
         "tools": [{"name": "read_file", "description": "", "inputSchema": {}, "annotations": None}]},
    ]
    surfaces.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    _seed_source(tmp_path / "cache", "npm", "a", "1.0.0")
    _seed_source(tmp_path / "cache", "npm", "big", "1.0.0")
    packages = tmp_path / "packages.txt"
    packages.write_text("npm:a\nnpm:big\n")

    good_text = json.dumps({"tools": [{"name": "read_file", "labels": ["SECRET"], "evidence": ["src/s.js:1"],
                                        "rationale": "calls `readFileSync`", "default_enabled": True, "undetermined": False}],
                             "value_conditions": [], "notes": []})
    # A response cut off mid-JSON by hitting max_tokens - must be reported as truncated,
    # not as the "invalid JSON" error parsing the fragment would otherwise raise.
    cid_a, cid_big = _custom_id("npm", "a"), _custom_id("npm", "big")

    class FakeBatches:
        def create(self, **kw):
            class B: id = "batch_truncated"
            return B()
        def retrieve(self, batch_id):
            class S: processing_status = "ended"; request_counts = {"succeeded": 1}
            return S()
        def results(self, batch_id):
            return [_batch_result(cid_a, good_text),
                    _batch_result(cid_big, '{"tools": [{"name": "read_fi', stop_reason="max_tokens")]
    class FakeMessages:
        batches = FakeBatches()
    class FakeClient:
        messages = FakeMessages()

    monkeypatch.setattr("interlock.cli.make_client", lambda: FakeClient())
    rc = main(["batch", str(packages), "--surfaces", str(surfaces),
               "--cache", str(tmp_path / "cache"), "--out", str(tmp_path / "profiles")])
    assert rc == 0

    assert (tmp_path / "profiles" / "npm_a_1.0.0.json").exists()
    assert not (tmp_path / "profiles" / "npm_big_1.0.0.json").exists()
    stats = [json.loads(l) for l in (tmp_path / "profiles" / "stats.jsonl").open()]
    assert len(stats) == 1 and stats[0]["package"] == "a"

    failures = [json.loads(l) for l in (tmp_path / "profiles" / "batch-failures.jsonl").open()]
    assert len(failures) == 1
    assert failures[0]["package"] == "big" and failures[0]["custom_id"] == cid_big
    assert "truncated" in failures[0]["message"]


def test_batch_records_a_failure_for_every_bad_result_and_keeps_going(tmp_path, monkeypatch):
    # One result with no text block (would previously raise StopIteration and kill the run),
    # one non-"succeeded" result (errored), one result whose JSON makes verify() raise a
    # KeyError, and one good result: the good one is written and the other three are each
    # recorded as a "result" failure, never stopping the batch (I3).
    surfaces = tmp_path / "surfaces.jsonl"
    rows = [{"kind": "npm", "pkg": p, "version": "1.0.0", "published": "2026-01-01T00:00:00Z", "ok": True,
             "tools": [{"name": "read_file", "description": "", "inputSchema": {}, "annotations": None}]}
            for p in ("a", "b", "c", "d")]
    surfaces.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    for p in ("a", "b", "c", "d"):
        _seed_source(tmp_path / "cache", "npm", p, "1.0.0")
    packages = tmp_path / "packages.txt"
    packages.write_text("npm:a\nnpm:b\nnpm:c\nnpm:d\n")

    good_text = json.dumps({"tools": [{"name": "read_file", "labels": ["SECRET"], "evidence": ["src/s.js:1"],
                                        "rationale": "calls `readFileSync`", "default_enabled": True, "undetermined": False}],
                             "value_conditions": [], "notes": []})
    # Missing "labels": parse_effects accepts it (only "name" is required), verify() then
    # raises KeyError trying to read j["labels"] - a raise this fix must catch too.
    keyerror_text = json.dumps({"tools": [{"name": "read_file", "evidence": ["src/s.js:1"],
                                           "rationale": "x", "default_enabled": True, "undetermined": False}],
                                "value_conditions": [], "notes": []})
    cid_a, cid_b, cid_c, cid_d = (_custom_id("npm", p) for p in ("a", "b", "c", "d"))

    def no_text_result(custom_id):
        message = SimpleNamespace(content=[], usage=SimpleNamespace(
            input_tokens=0, output_tokens=0, cache_creation_input_tokens=0, cache_read_input_tokens=0),
            stop_reason="end_turn")
        return SimpleNamespace(custom_id=custom_id, result=SimpleNamespace(type="succeeded", message=message))

    def errored_result(custom_id):
        return SimpleNamespace(custom_id=custom_id, result=SimpleNamespace(type="errored"))

    class FakeBatches:
        def create(self, **kw):
            class B: id = "batch_hardening"
            return B()
        def retrieve(self, batch_id):
            class S: processing_status = "ended"; request_counts = {"succeeded": 1}
            return S()
        def results(self, batch_id):
            return [_batch_result(cid_a, good_text), no_text_result(cid_b),
                    errored_result(cid_c), _batch_result(cid_d, keyerror_text)]
    class FakeMessages:
        batches = FakeBatches()
    class FakeClient:
        messages = FakeMessages()

    monkeypatch.setattr("interlock.cli.make_client", lambda: FakeClient())
    rc = main(["batch", str(packages), "--surfaces", str(surfaces),
               "--cache", str(tmp_path / "cache"), "--out", str(tmp_path / "profiles")])
    assert rc == 0

    assert (tmp_path / "profiles" / "npm_a_1.0.0.json").exists()
    for p in ("b", "c", "d"):
        assert not (tmp_path / "profiles" / f"npm_{p}_1.0.0.json").exists()
    stats = [json.loads(l) for l in (tmp_path / "profiles" / "stats.jsonl").open()]
    assert len(stats) == 1 and stats[0]["package"] == "a"

    failures = [json.loads(l) for l in (tmp_path / "profiles" / "batch-failures.jsonl").open()]
    assert len(failures) == 3
    assert all(f["stage"] == "result" and f["batch_id"] == "batch_hardening" for f in failures)
    assert {f["custom_id"] for f in failures} == {cid_b, cid_c, cid_d}


def test_batch_prepare_continues_past_one_invalid_package_name(tmp_path, monkeypatch):
    surfaces = tmp_path / "surfaces.jsonl"
    rows = [
        {"kind": "npm", "pkg": "INVALID_NAME", "version": "1.0.0", "published": "2026-01-01T00:00:00Z", "ok": True,
         "tools": [{"name": "read_file", "description": "", "inputSchema": {}, "annotations": None}]},
        {"kind": "npm", "pkg": "a", "version": "1.0.0", "published": "2026-01-01T00:00:00Z", "ok": True,
         "tools": [{"name": "read_file", "description": "", "inputSchema": {}, "annotations": None}]},
    ]
    surfaces.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    _seed_source(tmp_path / "cache", "npm", "a", "1.0.0")
    packages = tmp_path / "packages.txt"
    packages.write_text("npm:INVALID_NAME\nnpm:a\n")

    class FakeBatches:
        def __init__(self):
            self.created_requests = None
        def create(self, **kw):
            self.created_requests = kw["requests"]
            class B: id = "batch_prep_fail"
            return B()
        def retrieve(self, batch_id):
            class S: processing_status = "ended"; request_counts = {"succeeded": 0}
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

    assert len(fake.messages.batches.created_requests) == 1
    manifest = json.loads((tmp_path / "profiles" / "batch-manifest.json").read_text())
    assert [e["package"] for e in manifest["packages"]] == ["a"]

    failures = [json.loads(l) for l in (tmp_path / "profiles" / "batch-failures.jsonl").open()]
    assert len(failures) == 1
    assert failures[0]["package"] == "INVALID_NAME"
    assert failures[0]["stage"] == "prepare" and failures[0]["batch_id"] is None
    assert failures[0]["custom_id"] is None


def test_batch_prepare_does_not_leave_a_manifest_entry_when_batch_request_fails(tmp_path, monkeypatch):
    # batch_request (e.g. its MAX_TOOLS_CHARS guard) can raise after a custom_id has
    # already been computed. The manifest must then list only the package that was
    # actually queued, and the failure line must carry that computed custom_id rather
    # than null, so the manifest and the failure log can always be matched up.
    from interlock.adjudicate import batch_request as real_batch_request

    surfaces = tmp_path / "surfaces.jsonl"
    rows = [
        {"kind": "npm", "pkg": "big", "version": "1.0.0", "published": "2026-01-01T00:00:00Z", "ok": True,
         "tools": [{"name": "read_file", "description": "", "inputSchema": {}, "annotations": None}]},
        {"kind": "npm", "pkg": "a", "version": "1.0.0", "published": "2026-01-01T00:00:00Z", "ok": True,
         "tools": [{"name": "read_file", "description": "", "inputSchema": {}, "annotations": None}]},
    ]
    surfaces.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    _seed_source(tmp_path / "cache", "npm", "big", "1.0.0")
    _seed_source(tmp_path / "cache", "npm", "a", "1.0.0")
    packages = tmp_path / "packages.txt"
    packages.write_text("npm:big\nnpm:a\n")

    def flaky_batch_request(cid, surface, files):
        if surface["package"] == "big":
            raise ValueError("tools list too large")
        return real_batch_request(cid, surface, files)
    monkeypatch.setattr("interlock.cli.batch_request", flaky_batch_request)

    class FakeBatches:
        def __init__(self):
            self.created_requests = None
        def create(self, **kw):
            self.created_requests = kw["requests"]
            class B: id = "batch_ghost"
            return B()
        def retrieve(self, batch_id):
            class S: processing_status = "ended"; request_counts = {"succeeded": 1}
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

    assert len(fake.messages.batches.created_requests) == 1
    manifest = json.loads((tmp_path / "profiles" / "batch-manifest.json").read_text())
    assert [e["package"] for e in manifest["packages"]] == ["a"]

    failures = [json.loads(l) for l in (tmp_path / "profiles" / "batch-failures.jsonl").open()]
    assert len(failures) == 1
    assert failures[0]["package"] == "big"
    assert failures[0]["custom_id"] == _custom_id("npm", "big")
    assert failures[0]["stage"] == "prepare" and failures[0]["batch_id"] is None


def test_batch_failures_file_appends_across_separate_runs(tmp_path, monkeypatch):
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
    out_dir = tmp_path / "profiles"
    cid_a, cid_b = _custom_id("npm", "a"), _custom_id("npm", "b")

    def make_fake_client(batch_id, result):
        class FakeBatches:
            def create(self, **kw):
                class B: id = batch_id
                return B()
            def retrieve(self, bid):
                class S: processing_status = "ended"; request_counts = {"errored": 1}
                return S()
            def results(self, bid):
                return [result]
        class FakeMessages:
            batches = FakeBatches()
        class FakeClient:
            messages = FakeMessages()
        return FakeClient()

    packages_a = tmp_path / "packages_a.txt"; packages_a.write_text("npm:a\n")
    monkeypatch.setattr("interlock.cli.make_client", lambda: make_fake_client(
        "batch_one", SimpleNamespace(custom_id=cid_a, result=SimpleNamespace(type="errored"))))
    rc1 = main(["batch", str(packages_a), "--surfaces", str(surfaces),
                "--cache", str(tmp_path / "cache"), "--out", str(out_dir)])
    assert rc1 == 0

    packages_b = tmp_path / "packages_b.txt"; packages_b.write_text("npm:b\n")
    monkeypatch.setattr("interlock.cli.make_client", lambda: make_fake_client(
        "batch_two", SimpleNamespace(custom_id=cid_b, result=SimpleNamespace(type="errored"))))
    rc2 = main(["batch", str(packages_b), "--surfaces", str(surfaces),
                "--cache", str(tmp_path / "cache"), "--out", str(out_dir)])
    assert rc2 == 0

    failures = [json.loads(l) for l in (out_dir / "batch-failures.jsonl").open()]
    assert len(failures) == 2
    assert {f["batch_id"] for f in failures} == {"batch_one", "batch_two"}
    assert {f["custom_id"] for f in failures} == {cid_a, cid_b}


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
        {"custom_id": "npm_a-deadbeef", "kind": "npm", "package": "a", "version": "1.0.0", "root": str(root),
         "digest": source_digest(root)}]}
    (out_dir / "batch-manifest.json").write_text(json.dumps(manifest))

    class FakeBatches:
        def create(self, **kw):
            raise AssertionError("batches.create must not be called on resume")
        def retrieve(self, batch_id):
            assert batch_id == "batch_resumed"
            class S: processing_status = "ended"; request_counts = {"succeeded": 1}
            return S()
        def results(self, batch_id):
            class Block: type = "text"; text = json.dumps({"tools": [{"name": "read_file",
                "labels": ["SECRET"], "evidence": ["src/s.js:1"], "rationale": "calls `readFileSync`",
                "default_enabled": True, "undetermined": False}], "value_conditions": [], "notes": []})
            class Usage:
                input_tokens = 800
                output_tokens = 150
                cache_creation_input_tokens = 300
                cache_read_input_tokens = 400
            class Message: content = [Block()]; usage = Usage(); stop_reason = "end_turn"
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
    stats = [json.loads(l) for l in (out_dir / "stats.jsonl").open()]
    assert stats[0]["usage"] == {"input_tokens": 800, "output_tokens": 150,
                                  "cache_creation_input_tokens": 300, "cache_read_input_tokens": 400}
    assert stats[0]["cost_usd"] > 0
    assert stats[0]["batch_wall_seconds"] >= 0


def test_batch_manifest_carries_dependency_note_when_tool_missing_from_own_code(tmp_path, monkeypatch):
    surfaces = tmp_path / "surfaces.jsonl"
    surfaces.write_text(json.dumps({"kind": "npm", "pkg": "a", "version": "1.0.0", "published": "2026-01-01T00:00:00Z",
                                    "ok": True, "tools": [{"name": "browser_navigate", "description": "", "inputSchema": {}, "annotations": None}]}) + "\n")
    cache_dir = tmp_path / "cache"
    root = cache_dir / _slug("npm", "a", "1.0.0")
    (root / "src").mkdir(parents=True)
    (root / "src" / "index.js").write_text("module.exports = require('dep-code');\n")
    (root / "package.json").write_text(json.dumps({"dependencies": {"dep-code": "1.0.0"}}))
    dep_root = cache_dir / _slug("npm", "dep-code", "1.0.0")
    (dep_root / "lib").mkdir(parents=True)
    (dep_root / "lib" / "x.js").write_text("function browser_navigate() {}\n")
    packages = tmp_path / "packages.txt"
    packages.write_text("npm:a\n")

    class FakeBatches:
        def __init__(self):
            self.created_requests = None
        def create(self, **kw):
            self.created_requests = kw["requests"]
            class B: id = "batch_dep123"
            return B()
        def retrieve(self, batch_id):
            class S: processing_status = "ended"; request_counts = {"succeeded": 1}
            return S()
        def results(self, batch_id):
            return []
    class FakeMessages:
        def __init__(self):
            self.batches = FakeBatches()
    class FakeClient:
        def __init__(self):
            self.messages = FakeMessages()

    monkeypatch.setattr("interlock.cli.make_client", lambda: FakeClient())
    rc = main(["batch", str(packages), "--surfaces", str(surfaces),
               "--cache", str(cache_dir), "--out", str(tmp_path / "profiles")])
    assert rc == 0

    manifest = json.loads((tmp_path / "profiles" / "batch-manifest.json").read_text())
    entry = manifest["packages"][0]
    assert entry["notes"] == ["dependency sources included: dep-code@1.0.0"]
    view = Path(entry["root"])
    assert view == cache_dir / ".views" / _slug("npm", "a", "1.0.0")
    assert (view / ".deps" / "dep-code" / "lib" / "x.js").exists()
    assert not (root / ".deps").exists(), "the cached package tree must stay pristine"


def test_resume_adds_manifest_notes_to_written_profile(tmp_path, monkeypatch):
    surfaces = tmp_path / "surfaces.jsonl"
    surfaces.write_text(json.dumps({"kind": "npm", "pkg": "a", "version": "1.0.0", "published": "2026-01-01T00:00:00Z",
                                    "ok": True, "tools": [{"name": "read_file", "description": "", "inputSchema": {}, "annotations": None}]}) + "\n")
    root = tmp_path / "cache" / "npm_a_1.0.0"
    (root / "src").mkdir(parents=True)
    (root / "src" / "s.js").write_text("fs.readFileSync(p)\n")

    out_dir = tmp_path / "profiles"
    out_dir.mkdir()
    manifest = {"batch_id": "batch_resumed_notes", "packages": [
        {"custom_id": "npm_a-deadbeef", "kind": "npm", "package": "a", "version": "1.0.0", "root": str(root),
         "digest": source_digest(root), "notes": ["dependency sources included: dep-code@1.0.0"]}]}
    (out_dir / "batch-manifest.json").write_text(json.dumps(manifest))

    class FakeBatches:
        def create(self, **kw):
            raise AssertionError("batches.create must not be called on resume")
        def retrieve(self, batch_id):
            assert batch_id == "batch_resumed_notes"
            class S: processing_status = "ended"; request_counts = {"succeeded": 1}
            return S()
        def results(self, batch_id):
            class Block: type = "text"; text = json.dumps({"tools": [{"name": "read_file",
                "labels": ["SECRET"], "evidence": ["src/s.js:1"], "rationale": "calls `readFileSync`",
                "default_enabled": True, "undetermined": False}], "value_conditions": [], "notes": []})
            class Usage:
                input_tokens = 100
                output_tokens = 20
                cache_creation_input_tokens = 0
                cache_read_input_tokens = 0
            class Message: content = [Block()]; usage = Usage(); stop_reason = "end_turn"
            class Result: type = "succeeded"; message = Message()
            class R: custom_id = "npm_a-deadbeef"; result = Result()
            return [R()]
    class FakeMessages:
        batches = FakeBatches()
    class FakeClient:
        messages = FakeMessages()

    monkeypatch.setattr("interlock.cli.make_client", lambda: FakeClient())

    rc = main(["batch", str(tmp_path / "unused.txt"), "--resume", "batch_resumed_notes",
               "--surfaces", str(surfaces), "--cache", str(tmp_path / "cache"), "--out", str(out_dir)])
    assert rc == 0
    written = json.loads((out_dir / "npm_a_1.0.0.json").read_text())
    assert written["notes"] == ["dependency sources included: dep-code@1.0.0"]


def test_manifest_entry_stores_absolute_root_and_digest(tmp_path, monkeypatch):
    surfaces = tmp_path / "surfaces.jsonl"
    surfaces.write_text(json.dumps({"kind": "npm", "pkg": "a", "version": "1.0.0", "published": "2026-01-01T00:00:00Z",
                                    "ok": True, "tools": [{"name": "read_file", "description": "", "inputSchema": {}, "annotations": None}]}) + "\n")
    cache_dir = tmp_path / "cache"
    _seed_source(cache_dir, "npm", "a", "1.0.0")
    monkeypatch.chdir(tmp_path)  # prove "root" doesn't depend on cwd at resume time

    requests, entries, failures = _prepare_batch(["npm:a"], surfaces, Path("cache"))
    assert failures == []
    entry = entries[0]
    root = Path(entry["root"])
    assert root.is_absolute() and root == root.resolve()
    assert entry["digest"] == source_digest(root)


def test_resume_records_failure_when_root_missing_or_digest_stale(tmp_path, monkeypatch):
    # A batch resumed from a different working directory, or after the cache was cleaned
    # or rebuilt mid-run, must not silently re-verify against nothing or against the wrong
    # tree - it must fail loudly for that one package while the rest of the batch is still
    # written (I1).
    surfaces = tmp_path / "surfaces.jsonl"
    rows = [{"kind": "npm", "pkg": p, "version": "1.0.0", "published": "2026-01-01T00:00:00Z", "ok": True,
             "tools": [{"name": "read_file", "description": "", "inputSchema": {}, "annotations": None}]}
            for p in ("a", "gone", "stale")]
    surfaces.write_text("\n".join(json.dumps(r) for r in rows) + "\n")

    good_root = tmp_path / "cache" / "npm_a_1.0.0"
    (good_root / "src").mkdir(parents=True)
    (good_root / "src" / "s.js").write_text("fs.readFileSync(p)\n")
    good_digest = source_digest(good_root)

    missing_root = tmp_path / "cache" / "npm_gone_1.0.0"  # never created

    stale_root = tmp_path / "cache" / "npm_stale_1.0.0"
    (stale_root / "src").mkdir(parents=True)
    (stale_root / "src" / "s.js").write_text("fs.readFileSync(p)\n")
    stale_digest = source_digest(stale_root)
    (stale_root / "src" / "s.js").write_text("fs.readFileSync(p) // rebuilt differently\n")

    out_dir = tmp_path / "profiles"
    out_dir.mkdir()
    good_text = json.dumps({"tools": [{"name": "read_file", "labels": ["SECRET"], "evidence": ["src/s.js:1"],
                                        "rationale": "calls `readFileSync`", "default_enabled": True, "undetermined": False}],
                             "value_conditions": [], "notes": []})
    manifest = {"batch_id": "batch_stale", "packages": [
        {"custom_id": "cid_a", "kind": "npm", "package": "a", "version": "1.0.0",
         "root": str(good_root), "digest": good_digest},
        {"custom_id": "cid_gone", "kind": "npm", "package": "gone", "version": "1.0.0",
         "root": str(missing_root), "digest": "deadbeef"},
        {"custom_id": "cid_stale", "kind": "npm", "package": "stale", "version": "1.0.0",
         "root": str(stale_root), "digest": stale_digest},
    ]}
    (out_dir / "batch-manifest.json").write_text(json.dumps(manifest))

    class FakeBatches:
        def create(self, **kw):
            raise AssertionError("batches.create must not be called on resume")
        def retrieve(self, batch_id):
            class S: processing_status = "ended"; request_counts = {"succeeded": 3}
            return S()
        def results(self, batch_id):
            return [_batch_result("cid_a", good_text), _batch_result("cid_gone", good_text),
                    _batch_result("cid_stale", good_text)]
    class FakeMessages:
        batches = FakeBatches()
    class FakeClient:
        messages = FakeMessages()

    monkeypatch.setattr("interlock.cli.make_client", lambda: FakeClient())
    rc = main(["batch", str(tmp_path / "unused.txt"), "--resume", "batch_stale",
               "--surfaces", str(surfaces), "--cache", str(tmp_path / "cache"), "--out", str(out_dir)])
    assert rc == 0

    assert (out_dir / "npm_a_1.0.0.json").exists()
    assert not (out_dir / "npm_gone_1.0.0.json").exists()
    assert not (out_dir / "npm_stale_1.0.0.json").exists()

    stats = [json.loads(l) for l in (out_dir / "stats.jsonl").open()]
    assert len(stats) == 1 and stats[0]["package"] == "a"

    failures = [json.loads(l) for l in (out_dir / "batch-failures.jsonl").open()]
    assert {f["package"] for f in failures} == {"gone", "stale"}
    assert all(f["stage"] == "result" and f["batch_id"] == "batch_stale" for f in failures)


def test_profile_stats_carry_code_and_doc_files_shown_for_readme_only_selection(tmp_path, monkeypatch):
    surfaces = tmp_path / "surfaces.jsonl"
    surfaces.write_text(json.dumps({"kind": "npm", "pkg": "a", "version": "1.0.0", "published": "2026-01-01T00:00:00Z",
                                    "ok": True, "tools": [{"name": "read_file", "description": "", "inputSchema": {}, "annotations": None}]}) + "\n")
    root = tmp_path / "cache" / _slug("npm", "a", "1.0.0")
    root.mkdir(parents=True)
    (root / "README.md").write_text("The `read_file` tool is documented here as read_file.\n")

    class _FakeStream:
        def __init__(self, response):
            self._response = response
        def __enter__(self):
            return self
        def __exit__(self, *exc):
            return False
        def get_final_message(self):
            return self._response

    class FakeClient:
        class messages:
            @staticmethod
            def stream(**kw):
                class Block: type = "text"; text = json.dumps({"tools": [{"name": "read_file",
                    "labels": [], "evidence": ["README.md:1"], "rationale": "documented as `read_file`",
                    "default_enabled": True, "undetermined": False}], "value_conditions": [], "notes": []})
                class Usage:
                    input_tokens = 100; output_tokens = 20
                    cache_creation_input_tokens = 0; cache_read_input_tokens = 0
                class R: content = [Block()]; usage = Usage(); stop_reason = "end_turn"
                return _FakeStream(R())
    monkeypatch.setattr("interlock.cli.make_client", lambda: FakeClient())

    rc = main(["profile", "npm:a", "--surfaces", str(surfaces), "--cache", str(tmp_path / "cache"), "--out", str(tmp_path / "profiles")])
    assert rc == 0
    stats = [json.loads(l) for l in (tmp_path / "profiles" / "stats.jsonl").open()]
    assert stats[0]["code_files_shown"] == 0
    assert stats[0]["doc_files_shown"] == 1


def test_batch_manifest_and_stats_carry_code_and_doc_files_shown(tmp_path, monkeypatch):
    surfaces = tmp_path / "surfaces.jsonl"
    surfaces.write_text(json.dumps({"kind": "npm", "pkg": "a", "version": "1.0.0", "published": "2026-01-01T00:00:00Z",
                                    "ok": True, "tools": [{"name": "read_file", "description": "", "inputSchema": {}, "annotations": None}]}) + "\n")
    cache_dir = tmp_path / "cache"
    root = cache_dir / _slug("npm", "a", "1.0.0")
    root.mkdir(parents=True)
    (root / "README.md").write_text("The `read_file` tool is documented here as read_file.\n")
    packages = tmp_path / "packages.txt"
    packages.write_text("npm:a\n")

    good_text = json.dumps({"tools": [{"name": "read_file", "labels": [], "evidence": ["README.md:1"],
                                        "rationale": "documented as `read_file`", "default_enabled": True, "undetermined": False}],
                             "value_conditions": [], "notes": []})
    cid_a = _custom_id("npm", "a")

    class FakeBatches:
        def create(self, **kw):
            class B: id = "batch_docs"
            return B()
        def retrieve(self, batch_id):
            class S: processing_status = "ended"; request_counts = {"succeeded": 1}
            return S()
        def results(self, batch_id):
            return [_batch_result(cid_a, good_text)]
    class FakeMessages:
        batches = FakeBatches()
    class FakeClient:
        messages = FakeMessages()

    monkeypatch.setattr("interlock.cli.make_client", lambda: FakeClient())
    rc = main(["batch", str(packages), "--surfaces", str(surfaces),
               "--cache", str(cache_dir), "--out", str(tmp_path / "profiles")])
    assert rc == 0

    manifest = json.loads((tmp_path / "profiles" / "batch-manifest.json").read_text())
    entry = manifest["packages"][0]
    assert entry["code_files_shown"] == 0 and entry["doc_files_shown"] == 1

    stats = [json.loads(l) for l in (tmp_path / "profiles" / "stats.jsonl").open()]
    assert stats[0]["code_files_shown"] == 0 and stats[0]["doc_files_shown"] == 1
