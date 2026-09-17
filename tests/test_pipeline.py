import io, json, tarfile
from pathlib import Path
import pytest
from interlock.pipeline import evidence_kind_counts, prepare_sources, verify
from interlock.source import _slug

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
    (tmp_path / "README.md").write_text("Reads files via `readFileSync`, as documented here.\n")
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

SURFACE_DOC = {"package": "a", "version": "1", "kind": "npm", "tools": [
    {"name": "doc_claimed_tool", "description": "", "inputSchema": {}, "annotations": None},
    {"name": "doc_cleared_tool", "description": "", "inputSchema": {}, "annotations": None}]}

RAW_DOC = {"tools": {
    "doc_claimed_tool": {"labels": ["SECRET"], "evidence": ["README.md:1"],
                          "rationale": "calls `readFileSync` per the docs",
                          "default_enabled": True, "undetermined": False},
    "doc_cleared_tool": {"labels": [], "evidence": ["README.md:1"],
                          "rationale": "no-op, see `readFileSync` docs",
                          "default_enabled": True, "undetermined": False}},
    "value_conditions": [], "notes": []}

def test_doc_only_claim_counts_separately_and_keeps_labels(tmp_path):
    profile, stats = verify(RAW_DOC, SURFACE_DOC, tree(tmp_path))
    assert stats["verified_doc"] == 1
    assert profile.tools["doc_claimed_tool"].labels == ["SECRET"]
    # Documentation may raise an effect but must never lower one: a README-backed claim
    # keeps its labels but is never treated as a settled (determined) judgement, or it
    # would implicitly assert the tool has no *other* effect the docs don't mention.
    assert profile.tools["doc_claimed_tool"].undetermined is True
    assert "[evidence: documentation only]" in profile.tools["doc_claimed_tool"].rationale

def test_doc_only_clear_cannot_lower_effect(tmp_path):
    profile, stats = verify(RAW_DOC, SURFACE_DOC, tree(tmp_path))
    assert stats["cleared_unverified"] == 1
    assert profile.tools["doc_cleared_tool"].labels == []
    assert profile.tools["doc_cleared_tool"].undetermined is True
    assert "[unverified:" in profile.tools["doc_cleared_tool"].rationale

def test_accounting_invariant_across_all_categories(tmp_path):
    surface = {"package": "a", "version": "1", "kind": "npm",
               "tools": SURFACE["tools"] + SURFACE_CLEARED["tools"] + SURFACE_DOC["tools"]}
    raw = {"tools": {**RAW["tools"], **RAW_CLEARED["tools"], **RAW_DOC["tools"]},
           "value_conditions": [], "notes": []}
    profile, stats = verify(raw, surface, tree(tmp_path))
    assert stats["claims"] == stats["verified"] + stats["verified_doc"] + stats["demoted"]
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

def test_verify_raises_naming_the_package_when_root_is_missing(tmp_path):
    # A missing root (stale batch manifest, cache wiped mid-run) must fail loudly, not be
    # read as "nothing verifies" - that would demote every claim with no error anywhere (I1).
    missing_root = tmp_path / "does-not-exist"
    with pytest.raises(ValueError) as exc:
        verify(RAW, SURFACE, missing_root)
    assert SURFACE["package"] in str(exc.value)

def test_evidence_kind_counts_splits_code_from_documentation():
    files = [("src/index.js", "code"), ("README.md", "docs"), ("lib/x.py", "code")]
    assert evidence_kind_counts(files) == (2, 1)
    assert evidence_kind_counts([]) == (0, 0)


def _seed_package(cache_dir, deps, own_code="module.exports = require('dep-code');\n"):
    root = cache_dir / _slug("npm", "pkg", "1.0.0")
    (root / "src").mkdir(parents=True)
    (root / "src" / "index.js").write_text(own_code)
    (root / "package.json").write_text(json.dumps({"dependencies": deps}))
    return root


def fake_npm_pack(contents_by_package, resolved=None, calls=None):
    """npm pack stand-in: writes a tarball of contents_by_package[name] and reports
    resolved.get(name, requested) as the version, like `npm pack --json`."""
    def run(cmd, **kw):
        assert "install" not in cmd
        assert cmd[:2] == ["npm", "pack"] and "--ignore-scripts" in cmd
        spec = cmd[cmd.index("--") + 1]
        if calls is not None:
            calls.append(spec)
        name, _, requested = spec.rpartition("@")
        tgz = Path(kw["cwd"]) / "pkg.tgz"
        with tarfile.open(tgz, "w:gz") as t:
            for rel, data in contents_by_package[name].items():
                info = tarfile.TarInfo(f"package/{rel}"); info.size = len(data)
                t.addfile(info, io.BytesIO(data))
        version = (resolved or {}).get(name, requested)
        class R: returncode = 0; stdout = json.dumps([{"name": name, "version": version}]); stderr = ""
        return R()
    return run


def test_prepare_sources_skips_dependency_fetch_when_own_code_covers_tools(tmp_path):
    cache_dir = tmp_path / "cache"
    root = _seed_package(cache_dir, {"dep-code": "1.0.0"}, own_code="function browser_navigate() {}\n")

    def run(cmd, **kw):
        raise AssertionError(f"must not fetch anything: {cmd}")  # root is already cached

    got_root, files, notes = prepare_sources("npm", "pkg", "1.0.0", cache_dir,
                                             ["browser_navigate"], run=run)
    assert got_root == cache_dir / ".views" / _slug("npm", "pkg", "1.0.0")
    assert (got_root / "src" / "index.js").read_text() == "function browser_navigate() {}\n"
    assert notes == []
    assert not (root / ".deps").exists() and not (got_root / ".deps").exists()


def test_prepare_sources_fetches_dependency_when_tool_missing_from_own_code(tmp_path):
    cache_dir = tmp_path / "cache"
    root = _seed_package(cache_dir, {"dep-code": "1.0.0"})
    run = fake_npm_pack({"dep-code": {"lib/x.js": b"function browser_navigate() {}\n"}})

    got_root, files, notes = prepare_sources("npm", "pkg", "1.0.0", cache_dir,
                                             ["browser_navigate"], run=run)
    assert got_root != root
    assert notes == ["dependency sources included: dep-code@1.0.0"]
    assert (got_root / ".deps" / "dep-code" / "lib" / "x.js").exists()
    assert any(rel.startswith(".deps/") for rel, _ in files)
    assert not (root / ".deps").exists(), "the cached package tree must stay pristine"


def test_prepare_sources_treats_test_file_only_mention_as_missing(tmp_path):
    # select_files would never surface src/tool.test.js as evidence (test-file rule), so a
    # tool name that appears only there must not count as "found" in the package's own
    # code either, or dependency following would wrongly be skipped (I2).
    cache_dir = tmp_path / "cache"
    root = _seed_package(cache_dir, {"dep-code": "1.0.0"}, own_code="module.exports = {};\n")
    (root / "src" / "tool.test.js").write_text("function browser_navigate() {}\n")
    run = fake_npm_pack({"dep-code": {"lib/x.js": b"function browser_navigate() {}\n"}})

    got_root, files, notes = prepare_sources("npm", "pkg", "1.0.0", cache_dir,
                                             ["browser_navigate"], run=run)
    assert notes == ["dependency sources included: dep-code@1.0.0"]
    assert (got_root / ".deps" / "dep-code" / "lib" / "x.js").exists()


def test_prepare_sources_does_not_include_dependency_matching_only_in_its_test_folder(tmp_path):
    # select_files would never surface a dependency's test/ folder, so a match only there
    # must not earn the dependency one of the kept slots or a mention in notes (I2).
    cache_dir = tmp_path / "cache"
    root = _seed_package(cache_dir, {"dep-code": "1.0.0"})
    run = fake_npm_pack({"dep-code": {"test/spec.js": b"function browser_navigate() {}\n",
                                      "lib/y.js": b"module.exports = {};\n"}})

    got_root, files, notes = prepare_sources("npm", "pkg", "1.0.0", cache_dir,
                                             ["browser_navigate"], run=run)
    assert notes == []
    assert not (got_root / ".deps").exists()


def test_prepare_sources_notes_resolved_version_and_skipped_dependency(tmp_path):
    cache_dir = tmp_path / "cache"
    _seed_package(cache_dir, {"dep-code": "^1.2.0", "evil": "file:../x"})
    calls = []
    run = fake_npm_pack({"dep-code": {"lib/x.js": b"function browser_navigate() {}\n"}},
                        resolved={"dep-code": "1.4.3"}, calls=calls)

    got_root, _, notes = prepare_sources("npm", "pkg", "1.0.0", cache_dir, ["browser_navigate"], run=run)
    dep_slug = _slug("npm", "dep-code", "1.4.3")
    assert calls == ["dep-code@^1.2.0"]
    assert (cache_dir / dep_slug / "lib" / "x.js").exists()
    assert sorted(p.name for p in cache_dir.glob("npm_dep-code_*")) == [dep_slug]
    assert notes[0] == "dependency sources included: dep-code@1.4.3"
    assert notes[1].startswith("dependency skipped: 'evil' (invalid npm version or range")
    assert len(notes) == 2


def test_prepare_sources_rebuilds_view_so_earlier_dependencies_do_not_leak(tmp_path):
    cache_dir = tmp_path / "cache"
    root = _seed_package(cache_dir, {"dep-a": "1.0.0", "dep-b": "1.0.0"}, own_code="module.exports = {};\n")
    run = fake_npm_pack({"dep-a": {"lib/a.js": b"function tool_a() {}\n"},
                         "dep-b": {"lib/b.js": b"function tool_b() {}\n"}})

    view1, _, notes1 = prepare_sources("npm", "pkg", "1.0.0", cache_dir, ["tool_a"], run=run)
    assert notes1 == ["dependency sources included: dep-a@1.0.0"]
    assert (view1 / ".deps" / "dep-a").is_dir()
    assert not (root / ".deps").exists()

    view2, files2, notes2 = prepare_sources("npm", "pkg", "1.0.0", cache_dir, ["tool_b"], run=run)
    assert notes2 == ["dependency sources included: dep-b@1.0.0"]
    assert sorted(p.name for p in (view2 / ".deps").iterdir()) == ["dep-b"]
    assert not any(rel.startswith(".deps/dep-a") for rel, _ in files2)
    assert not (root / ".deps").exists()


def test_prepare_sources_view_drops_legacy_deps_and_symlinks(tmp_path):
    # A cache populated before per-run views may hold .deps copied in by an earlier run.
    cache_dir = tmp_path / "cache"
    root = _seed_package(cache_dir, {}, own_code="function tool_a() {}\n")
    (root / ".deps" / "old-dep").mkdir(parents=True)
    (root / ".deps" / "old-dep" / "x.js").write_text("function tool_a() {}\n")
    (tmp_path / "outside.js").write_text("function tool_a() {}\n")
    (root / "src" / "link.js").symlink_to(tmp_path / "outside.js")

    view, files, _ = prepare_sources("npm", "pkg", "1.0.0", cache_dir, ["tool_a"], run=None)
    assert not (view / ".deps").exists()
    assert not (view / "src" / "link.js").exists() and not (view / "src" / "link.js").is_symlink()
    assert [rel for rel, _ in files] == ["src/index.js"]


def test_prepare_sources_refuses_view_outside_cache(tmp_path):
    cache_dir = tmp_path / "cache"
    _seed_package(cache_dir, {}, own_code="function t() {}\n")
    outside = tmp_path / "outside" / "npm_pkg_1.0.0"
    outside.mkdir(parents=True)
    (outside / "keep.txt").write_text("precious\n")
    (cache_dir / ".views").symlink_to(tmp_path / "outside")

    def run(cmd, **kw):
        raise AssertionError(f"must not fetch anything: {cmd}")

    with pytest.raises(ValueError):
        prepare_sources("npm", "pkg", "1.0.0", cache_dir, ["t"], run=run)
    assert (outside / "keep.txt").read_text() == "precious\n"
