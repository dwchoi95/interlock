from pathlib import Path
from interlock.select import select_files

def make(tmp_path, files):
    for rel, text in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return tmp_path

def test_ranks_files_mentioning_tool_names(tmp_path):
    root = make(tmp_path, {"a.js": "nothing here", "b.js": 'name: "read_file"', "c.js": 'name: "read_file" name: "write_file"'})
    picked = [rel for rel, _ in select_files(root, ["read_file", "write_file"])]
    assert picked[:2] == ["c.js", "b.js"]

def test_skips_vendored_tests_and_binaries(tmp_path):
    root = make(tmp_path, {"node_modules/x/i.js": 'name: "read_file"', "t.test.js": 'name: "read_file"',
                           "img.png": "binary-ish", "src/i.js": 'name: "read_file"'})
    assert [rel for rel, _ in select_files(root, ["read_file"])] == ["src/i.js"]

def test_respects_budget(tmp_path):
    root = make(tmp_path, {f"f{i}.js": 'name: "read_file"' + "x" * 1000 for i in range(10)})
    out = select_files(root, ["read_file"], budget_bytes=3000)
    assert 0 < len(out) <= 3
