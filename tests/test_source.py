import stat, subprocess, tarfile, zipfile, io, json
from pathlib import Path
import pytest
from interlock.source import fetch_dependencies, fetch_source, source_digest

def fake_npm_runner(tmp_path):
    def run(cmd, **kw):
        assert "install" not in cmd, "must never install"
        if cmd[:2] == ["npm", "pack"]:
            tgz = Path(kw["cwd"]) / "pkg.tgz"
            with tarfile.open(tgz, "w:gz") as t:
                data = b"const x = 1;\n"
                info = tarfile.TarInfo("package/index.js"); info.size = len(data)
                t.addfile(info, io.BytesIO(data))
            class R: returncode = 0; stdout = "pkg.tgz\n"; stderr = ""
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

def fake_pypi_malicious_wheel_runner(tmp_path):
    def run(cmd, **kw):
        assert "install" not in cmd, "must never install"
        assert cmd[:2] == ["pip", "download"]
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
        assert cmd[:2] == ["pip", "download"]
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


def _npm_pack_package_name(cmd):
    # cmd[2] is "name@version"; rpartition keeps a scoped name's leading '@' intact.
    return cmd[2].rpartition("@")[0]

def fake_npm_pack_runner(contents_by_package: dict[str, dict[str, bytes]]):
    """contents_by_package: pkg name -> {relative path in tarball -> file bytes}, laid
    out under a package/ prefix like a real npm tarball."""
    def run(cmd, **kw):
        assert "install" not in cmd, "must never install"
        assert cmd[:2] == ["npm", "pack"]
        files = contents_by_package[_npm_pack_package_name(cmd)]
        tgz = Path(kw["cwd"]) / "pkg.tgz"
        with tarfile.open(tgz, "w:gz") as t:
            for rel, data in files.items():
                info = tarfile.TarInfo(f"package/{rel}")
                info.size = len(data)
                t.addfile(info, io.BytesIO(data))
        class R: returncode = 0; stdout = "pkg.tgz\n"; stderr = ""
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
    kept = fetch_dependencies(root, ["browser_navigate"], tmp_path / "cache", run=runner)
    assert kept == ["dep-code"]
    assert (root / ".deps" / "dep-code" / "lib" / "x.js").read_text() == "function browser_navigate() { return true; }\n"
    assert not (root / ".deps" / "dep-doc-only").exists()

def test_fetch_dependencies_idempotent_copy_skips_failing_fetch(tmp_path):
    root = tmp_path / "root"; root.mkdir()
    (root / "package.json").write_text(json.dumps(
        {"dependencies": {"dep-code": "1.0.0", "dep-broken": "9.9.9"}}))
    base_runner = fake_npm_pack_runner({"dep-code": {"lib/x.js": b"function browser_navigate() {}\n"}})
    def run(cmd, **kw):
        if _npm_pack_package_name(cmd) == "dep-broken":
            raise subprocess.CalledProcessError(1, cmd)
        return base_runner(cmd, **kw)

    kept1 = fetch_dependencies(root, ["browser_navigate"], tmp_path / "cache", run=run)
    assert kept1 == ["dep-code"]
    # dep-broken's fetch always raises and must be skipped, not fatal; a second call must
    # not re-copy into (or fail on) the already-populated .deps/dep-code directory.
    kept2 = fetch_dependencies(root, ["browser_navigate"], tmp_path / "cache", run=run)
    assert kept2 == ["dep-code"]
    assert (root / ".deps" / "dep-code" / "lib" / "x.js").read_text() == "function browser_navigate() {}\n"

def test_fetch_dependencies_respects_max_deps(tmp_path):
    root = tmp_path / "root"; root.mkdir()
    names = [f"dep-{i}" for i in range(4)]
    (root / "package.json").write_text(json.dumps({"dependencies": {n: "1.0.0" for n in names}}))
    runner = fake_npm_pack_runner({n: {"lib/x.js": b"function browser_navigate() {}\n"} for n in names})
    kept = fetch_dependencies(root, ["browser_navigate"], tmp_path / "cache", run=runner, max_deps=2)
    assert kept == sorted(names)[:2]
