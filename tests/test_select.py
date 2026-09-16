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

def test_excerpts_large_file_with_line_markers(tmp_path):
    # Simulates a multi-megabyte minified bundle: occurrences of the tool name are
    # spread near the start, middle and end, far apart from each other.
    tool = "special_tool_name"
    line = "x" * 79  # 79 chars + "\n" = 80 chars per line
    n_lines = 5000  # ~400,000 chars total, well over MAX_FILE_CHARS
    lines = [line] * n_lines
    lines[5] = f'name: "{tool}" START_REGION'
    lines[2500] = f'name: "{tool}" MID_REGION'
    lines[4995] = f'name: "{tool}" END_REGION'
    text = "\n".join(lines)
    root = make(tmp_path, {"bundle.js": text})

    mid_pos = text.index(f'{tool}" MID_REGION')
    window_start = max(0, mid_pos - 1500)
    expected_line = text.count("\n", 0, window_start) + 1

    out = dict(select_files(root, [tool]))
    result = out["bundle.js"]
    assert "START_REGION" in result
    assert "MID_REGION" in result
    assert "END_REGION" in result
    assert f"[line {expected_line}]" in result

def test_anchors_test_marker_to_avoid_false_positives(tmp_path):
    tool = "read_file"
    root = make(tmp_path, {
        "latest_release.go": f'const tool = "{tool}"',
        "test_helpers.go": f'const tool = "{tool}"',
        "server.test.js": f'const tool = "{tool}"',
    })
    picked = {rel for rel, _ in select_files(root, [tool])}
    assert picked == {"latest_release.go"}

def test_includes_snap_files(tmp_path):
    tool = "read_file"
    root = make(tmp_path, {"__toolsnaps__/read-file.snap": f'{{"name": "{tool}"}}'})
    picked = {rel for rel, _ in select_files(root, [tool])}
    assert picked == {"__toolsnaps__/read-file.snap"}

def test_ranks_documentation_after_implementation(tmp_path):
    tools = ["read_file", "write_file", "list_dir"]
    readme = " ".join(t for t in tools for _ in range(20))
    server = " ".join(f'name: "{t}"' for t in tools)
    root = make(tmp_path, {"README.md": readme, "server.js": server})
    picked = [rel for rel, _ in select_files(root, tools)]
    assert picked.index("server.js") < picked.index("README.md")
