import stat, subprocess, tarfile, zipfile, io, json
from pathlib import Path
import pytest
from interlock.source import cache_path, fetch_dependencies, fetch_source, source_digest, _slug

def _npm_spec(cmd):
    # The spec is always the single argument after "--", so it can never be read as a flag.
    return cmd[cmd.index("--") + 1]

def _pack_json(name, version):
    return json.dumps([{"id": f"{name}@{version}", "name": name, "version": version,
                        "filename": "pkg.tgz", "files": []}])

def fake_npm_runner(tmp_path):
    def run(cmd, **kw):
        assert "install" not in cmd, "must never install"
        if cmd[:2] == ["npm", "pack"]:
            tgz = Path(kw["cwd"]) / "pkg.tgz"
            with tarfile.open(tgz, "w:gz") as t:
                data = b"const x = 1;\n"
                info = tarfile.TarInfo("package/index.js"); info.size = len(data)
                t.addfile(info, io.BytesIO(data))
            name, _, version = _npm_spec(cmd).rpartition("@")
            class R: returncode = 0; stdout = _pack_json(name, version); stderr = ""
            return R()
        raise AssertionError(f"unexpected command {cmd}")
    return run

def test_fetch_npm_unpacks_without_installing(tmp_path):
    out = fetch_source("npm", "left-pad", "1.0.0", tmp_path, run=fake_npm_runner(tmp_path))
    assert (out / "index.js").read_text() == "const x = 1;\n"

def test_fetch_is_cached(tmp_path):
    calls = []
    runner = fake_npm_runner(tmp_path)
    def counting(cmd, **kw):
        calls.append(cmd); return runner(cmd, **kw)
    fetch_source("npm", "left-pad", "1.0.0", tmp_path, run=counting)
    fetch_source("npm", "left-pad", "1.0.0", tmp_path, run=counting)
    assert len(calls) == 1

def test_digest_is_content_addressed(tmp_path):
    a = tmp_path / "a"; a.mkdir(); (a / "f.js").write_text("x")
    b = tmp_path / "b"; b.mkdir(); (b / "f.js").write_text("x")
    c = tmp_path / "c"; c.mkdir(); (c / "f.js").write_text("y")
    assert source_digest(a) == source_digest(b) != source_digest(c)

def test_unknown_kind_raises(tmp_path):
    with pytest.raises(ValueError):
        fetch_source("cargo", "serde", "1.0", tmp_path)

def _pip_download_dir(cmd):
    return Path(cmd[cmd.index("-d") + 1])

def _assert_safe_pip(cmd):
    assert cmd[:2] == ["pip", "download"]
    assert "--only-binary=:all:" in cmd and "--no-deps" in cmd
    assert cmd[-2] == "--", "the requirement must follow -- so it can't be read as an option"

def fake_pypi_malicious_wheel_runner(tmp_path):
    def run(cmd, **kw):
        assert "install" not in cmd, "must never install"
        _assert_safe_pip(cmd)
        whl = _pip_download_dir(cmd) / "pkg-1.0-py3-none-any.whl"
        with zipfile.ZipFile(whl, "w") as z:
            z.writestr("pkg/index.js", "const x = 1;\n")
            # path traversal member: escapes two levels above the
            # extraction dir (work/unpacked -> cache_dir/escape.js)
            z.writestr("../../escape.js", "evil\n")
            # symlink member pointing outside the destination
            link_info = zipfile.ZipInfo("pkg/evil_link")
            link_info.external_attr = (stat.S_IFLNK | 0o777) << 16
            z.writestr(link_info, "/etc/passwd")
        class R: returncode = 0; stdout = ""; stderr = ""
        return R()
    return run

def test_fetch_zip_rejects_traversal_and_symlinks(tmp_path):
    out = fetch_source("pypi", "pkg", "1.0", tmp_path, run=fake_pypi_malicious_wheel_runner(tmp_path))
    assert (out / "index.js").read_text() == "const x = 1;\n"
    assert not (out / "evil_link").exists()
    assert not (out / "evil_link").is_symlink()
    assert not (tmp_path / "escape.js").exists()

def failing_npm_runner(cmd, **kw):
    assert "install" not in cmd, "must never install"
    raise subprocess.CalledProcessError(1, cmd)

def test_fetch_failure_cleans_up_work_dir(tmp_path):
    with pytest.raises(subprocess.CalledProcessError):
        fetch_source("npm", "left-pad", "1.0.0", tmp_path, run=failing_npm_runner)
    assert list(tmp_path.glob(".work_*")) == []

def fake_pypi_wheel_runner(tmp_path):
    def run(cmd, **kw):
        assert "install" not in cmd, "must never install"
        _assert_safe_pip(cmd)
        whl = _pip_download_dir(cmd) / "pkg-1.0-py3-none-any.whl"
        with zipfile.ZipFile(whl, "w") as z:
            z.writestr("pkg/__init__.py", "x = 1\n")
            z.writestr("pkg-1.0.dist-info/METADATA", "Metadata-Version: 2.1\n")
        class R: returncode = 0; stdout = ""; stderr = ""
        return R()
    return run

def test_fetch_pypi_wheel_root_excludes_dist_info(tmp_path):
    out = fetch_source("pypi", "pkg", "1.0", tmp_path, run=fake_pypi_wheel_runner(tmp_path))
    assert (out / "__init__.py").read_text() == "x = 1\n"
    assert not any(p.name.endswith(".dist-info") for p in out.iterdir())

def test_pypi_package_without_wheel_is_a_clear_error_not_an_sdist_build(tmp_path):
    def run(cmd, **kw):
        _assert_safe_pip(cmd)
        class R: returncode = 1; stdout = ""; stderr = "ERROR: No matching distribution found for pkg==1.0"
        return R()
    with pytest.raises(RuntimeError, match="wheel"):
        fetch_source("pypi", "pkg", "1.0", tmp_path / "cache", run=run)
    assert list((tmp_path / "cache").glob(".work_*")) == []


@pytest.mark.parametrize("kind,package,version", [
    ("npm", "left-pad", "a/../../../victim"),
    ("npm", "left-pad", "file:../x"),
    ("npm", "left-pad", "git+https://example.com/x.git"),
    ("npm", "left-pad", "github:owner/repo"),
    ("npm", "--registry=http://evil/", "1.0.0"),
    ("npm", "../escape", "1.0.0"),
    ("git", "https://evil.example/x", "main"),
    # npm reads "x@." as a directory spec and "x@evil.tgz" as a local tarball, though
    # neither contains ':' or '/'
    ("npm", "left-pad", "."),
    ("npm", "left-pad", "evil.tgz"),
    ("npm", "left-pad", 5),
    ("git", "https://github.com/owner/repo", "../main"),
    ("git", "https://github.com/owner/repo/../x", "main"),
    ("pypi", "-r", "1.0"),
    ("pypi", "pkg", "1.0/../x"),
])
def test_malicious_spec_rejected_before_runner(tmp_path, kind, package, version):
    calls = []
    def run(cmd, **kw):
        calls.append(cmd)
        raise AssertionError(f"runner must not be called: {cmd}")
    with pytest.raises(ValueError):
        fetch_source(kind, package, version, tmp_path / "cache", run=run)
    assert calls == []
    assert list(tmp_path.iterdir()) == [], "nothing may be created before validation"

def test_npm_runner_gets_ignore_scripts_and_separator_before_spec(tmp_path):
    calls = []
    runner = fake_npm_runner(tmp_path)
    def recording(cmd, **kw):
        calls.append(cmd); return runner(cmd, **kw)
    fetch_source("npm", "@scope/left-pad", "1.0.0", tmp_path, run=recording)
    [cmd] = calls
    assert "--ignore-scripts" in cmd
    assert cmd.index("--") == len(cmd) - 2 and cmd[-1] == "@scope/left-pad@1.0.0"

def test_git_accepts_github_https_and_separates_url(tmp_path):
    calls = []
    def run(cmd, **kw):
        calls.append(cmd)
        repo = Path(cmd[-1]); repo.mkdir(parents=True); (repo / "server.py").write_text("x = 1\n")
        class R: returncode = 0; stdout = ""; stderr = ""
        return R()
    out = fetch_source("git", "https://github.com/owner/repo.git", "v1.2/rc", tmp_path, run=run)
    assert (out / "server.py").exists()
    assert "--branch=v1.2/rc" in calls[0]
    assert calls[0][-3:-1] == ["--", "https://github.com/owner/repo.git"]

def test_cache_path_refuses_escape(tmp_path):
    cache = tmp_path / "cache"
    (tmp_path / "outside").mkdir()
    cache.mkdir()
    (cache / "link").symlink_to(tmp_path / "outside")
    for name in ("../x", "link/x", ".", "/etc"):
        with pytest.raises(ValueError):
            cache_path(cache, name)
    assert cache_path(cache, "npm_x_1.0.0") == cache / "npm_x_1.0.0"


def _npm_pack_package_name(cmd):
    # rpartition keeps a scoped name's leading '@' intact.
    return _npm_spec(cmd).rpartition("@")[0]

def fake_npm_pack_runner(contents_by_package: dict[str, dict[str, bytes]], resolved: dict[str, str] | None = None):
    """contents_by_package: pkg name -> {relative path in tarball -> file bytes}, laid
    out under a package/ prefix like a real npm tarball. resolved: pkg name -> the
    version `npm pack --json` reports (defaults to the requested one)."""
    def run(cmd, **kw):
        assert "install" not in cmd, "must never install"
        assert cmd[:2] == ["npm", "pack"] and "--ignore-scripts" in cmd
        name, _, requested = _npm_spec(cmd).rpartition("@")
        files = contents_by_package[name]
        tgz = Path(kw["cwd"]) / "pkg.tgz"
        with tarfile.open(tgz, "w:gz") as t:
            for rel, data in files.items():
                info = tarfile.TarInfo(f"package/{rel}")
                info.size = len(data)
                t.addfile(info, io.BytesIO(data))
        class R: returncode = 0; stdout = _pack_json(name, (resolved or {}).get(name, requested)); stderr = ""
        return R()
    return run

def test_fetch_dependencies_keeps_only_code_evidence(tmp_path):
    root = tmp_path / "root"; root.mkdir()
    (root / "package.json").write_text(json.dumps(
        {"dependencies": {"dep-code": "1.0.0", "dep-doc-only": "2.0.0"}}))
    runner = fake_npm_pack_runner({
        "dep-code": {"lib/x.js": b"function browser_navigate() { return true; }\n"},
        "dep-doc-only": {"README.md": b"This package implements browser_navigate.\n",
                          "lib/y.js": b"module.exports = {};\n"},
    })
    kept, skipped = fetch_dependencies(root, ["browser_navigate"], tmp_path / "cache", run=runner)
    assert [(name, version) for name, version, _ in kept] == [("dep-code", "1.0.0")]
    assert (kept[0][2] / "lib" / "x.js").read_text() == "function browser_navigate() { return true; }\n"
    assert skipped == []
    assert not (root / ".deps").exists(), "fetch_dependencies must not write into the package tree"

def test_fetch_dependencies_does_not_keep_a_dependency_matching_only_in_its_test_folder(tmp_path):
    # select_files would never surface a dependency's test/ folder, so a match only there
    # must not justify keeping (and reporting) that dependency (I2).
    root = tmp_path / "root"; root.mkdir()
    (root / "package.json").write_text(json.dumps({"dependencies": {"dep-test-only": "1.0.0"}}))
    runner = fake_npm_pack_runner({
        "dep-test-only": {"test/spec.js": b"function browser_navigate() { return true; }\n",
                          "lib/y.js": b"module.exports = {};\n"},
    })
    kept, skipped = fetch_dependencies(root, ["browser_navigate"], tmp_path / "cache", run=runner)
    assert kept == []
    assert skipped == []


def test_fetch_dependencies_records_failing_fetch_as_skipped(tmp_path):
    root = tmp_path / "root"; root.mkdir()
    (root / "package.json").write_text(json.dumps(
        {"dependencies": {"dep-code": "1.0.0", "dep-broken": "9.9.9"}}))
    base_runner = fake_npm_pack_runner({"dep-code": {"lib/x.js": b"function browser_navigate() {}\n"}})
    def run(cmd, **kw):
        if _npm_pack_package_name(cmd) == "dep-broken":
            raise subprocess.CalledProcessError(1, cmd)
        return base_runner(cmd, **kw)

    kept, skipped = fetch_dependencies(root, ["browser_navigate"], tmp_path / "cache", run=run)
    assert [name for name, _, _ in kept] == ["dep-code"]
    assert [name for name, _ in skipped] == ["dep-broken"]
    assert "fetch failed" in skipped[0][1]

def test_fetch_dependencies_respects_max_deps(tmp_path):
    root = tmp_path / "root"; root.mkdir()
    names = [f"dep-{i}" for i in range(4)]
    (root / "package.json").write_text(json.dumps({"dependencies": {n: "1.0.0" for n in names}}))
    runner = fake_npm_pack_runner({n: {"lib/x.js": b"function browser_navigate() {}\n"} for n in names})
    kept, _ = fetch_dependencies(root, ["browser_navigate"], tmp_path / "cache", run=runner, max_deps=2)
    assert [name for name, _, _ in kept] == sorted(names)[:2]

def test_fetch_dependencies_caches_range_under_resolved_version(tmp_path):
    root = tmp_path / "root"; root.mkdir()
    (root / "package.json").write_text(json.dumps({"dependencies": {"dep-code": "^1.2.0"}}))
    runner = fake_npm_pack_runner({"dep-code": {"lib/x.js": b"function browser_navigate() {}\n"}},
                                  resolved={"dep-code": "1.4.3"})
    cache = tmp_path / "cache"
    kept, _ = fetch_dependencies(root, ["browser_navigate"], cache, run=runner)
    dep_slug = _slug("npm", "dep-code", "1.4.3")
    assert kept == [("dep-code", "1.4.3", cache / dep_slug)]
    assert sorted(p.name for p in cache.iterdir()) == [dep_slug]

def test_fetch_source_gives_colliding_sanitised_names_different_cache_dirs(tmp_path):
    # "@a/b" and "a_b" both sanitise to the same slug prefix ("a_b"); the cache slug must
    # still differ, or one package's judgement could be read from the other's source.
    def run(cmd, **kw):
        name, _, version = _npm_spec(cmd).rpartition("@")
        tgz = Path(kw["cwd"]) / "pkg.tgz"
        with tarfile.open(tgz, "w:gz") as t:
            data = f"module.exports = {json.dumps(name)};\n".encode()
            info = tarfile.TarInfo("package/index.js"); info.size = len(data)
            t.addfile(info, io.BytesIO(data))
        class R: returncode = 0; stdout = _pack_json(name, version); stderr = ""
        return R()

    out_a = fetch_source("npm", "@a/b", "1.0.0", tmp_path, run=run)
    out_b = fetch_source("npm", "a_b", "1.0.0", tmp_path, run=run)
    assert out_a != out_b
    assert (out_a / "index.js").read_text() == 'module.exports = "@a/b";\n'
    assert (out_b / "index.js").read_text() == 'module.exports = "a_b";\n'


def test_reviewer_repro_dependency_range_cannot_delete_outside_cache(tmp_path):
    # Default layout: cache at <repo>/.cache/sources, so "a/../../../victim" from a work
    # dir inside the cache lands on <repo>/victim.
    cache = tmp_path / ".cache" / "sources"
    victim = tmp_path / "victim"; victim.mkdir(); (victim / "keep.txt").write_text("precious\n")
    root = tmp_path / "pkg"; root.mkdir()
    (root / "package.json").write_text(json.dumps(
        {"dependencies": {"@x": "a/../../../victim", "x": "a/../../../victim"}}))
    calls = []
    def run(cmd, **kw):
        calls.append(cmd)
        raise subprocess.CalledProcessError(1, cmd)

    result = fetch_dependencies(root, ["tool"], cache, run=run)
    assert (victim / "keep.txt").read_text() == "precious\n"
    assert calls == []
    kept, skipped = result
    assert kept == []
    assert sorted(name for name, _ in skipped) == ["@x", "x"]
    assert not cache.exists() or list(cache.iterdir()) == []
