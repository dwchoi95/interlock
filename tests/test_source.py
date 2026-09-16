import tarfile, io, json
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
