import stat, subprocess, tarfile, zipfile, io, json
from pathlib import Path
import pytest
from interlock.source import fetch_source, source_digest

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
