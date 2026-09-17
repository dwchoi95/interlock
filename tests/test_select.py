from pathlib import Path
from interlock.select import select_files, MAX_FILE_CHARS, WINDOW_SEPARATOR

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

def test_numbers_every_line_of_a_small_file(tmp_path):
    tool = "read_file"
    text = f'line one\nname: "{tool}"\nline three'
    root = make(tmp_path, {"a.js": text})
    out = dict(select_files(root, [tool]))
    expected = "\n".join(f"{i:>6}| {line}" for i, line in enumerate(text.splitlines(), start=1))
    assert out["a.js"] == expected

def test_excerpt_of_large_file_has_true_line_numbers(tmp_path):
    # Simulates a multi-megabyte minified bundle: the tool name occurs once, deep in the file.
    tool = "special_tool_name"
    line = "x" * 79  # 79 chars + "\n" = 80 chars per line
    n_lines = 5000  # ~400,000 chars total, well over MAX_FILE_CHARS once numbered
    lines = [line] * n_lines
    lines[4499] = f'name: "{tool}" MARK'  # 0-indexed -> true line number 4500
    text = "\n".join(lines)
    root = make(tmp_path, {"bundle.js": text})

    out = dict(select_files(root, [tool]))
    result = out["bundle.js"]
    assert f"  4500| {lines[4499]}" in result

def test_excerpts_single_line_minified_file_when_over_budget(tmp_path):
    # A minified bundle with no newlines at all: one true line, far longer than any window.
    tool = "special_tool_name"
    text = ("x" * 200_000) + f'name:"{tool}"' + ("y" * 200_000)
    root = make(tmp_path, {"bundle.min.js": text})

    out = dict(select_files(root, [tool]))
    result = out["bundle.min.js"]
    assert result.startswith("     1| ")
    assert "…[truncated]" in result
    assert len(result) <= MAX_FILE_CHARS

def test_single_line_file_emits_a_slice_per_occurrence_across_regions(tmp_path):
    # One giant physical line (no newlines anywhere) with three different tools' evidence
    # spread across it. Each must survive as its own numbered, truncation-marked slice.
    tool_a, tool_b, tool_c = "tool_alpha", "tool_beta", "tool_gamma"
    filler = "z" * 150_000
    text = f"{tool_a}_START" + filler + f"{tool_b}_MID" + filler + f"{tool_c}_END"
    root = make(tmp_path, {"bundle.min.js": text})

    out = dict(select_files(root, [tool_a, tool_b, tool_c]))
    result = out["bundle.min.js"]
    assert f"{tool_a}_START" in result
    assert f"{tool_b}_MID" in result
    assert f"{tool_c}_END" in result

    slices = result.split(f"\n{WINDOW_SEPARATOR}\n")
    assert len(slices) == 3
    for s in slices:
        assert s.startswith("     1| ")
    assert len(result) <= MAX_FILE_CHARS

def test_merges_overlapping_slices_on_same_long_line(tmp_path):
    # Two occurrences of the same tool name on one over-long line, closer together than
    # 2*EXCERPT_RADIUS: their windows overlap and must collapse into a single slice.
    tool = "shared_tool_name"
    text = ("a" * 200_000) + tool + ("b" * 1000) + tool + ("c" * 200_000)
    root = make(tmp_path, {"bundle.min.js": text})

    out = dict(select_files(root, [tool]))
    result = out["bundle.min.js"]
    assert result.count(tool) == 2
    assert WINDOW_SEPARATOR not in result
    assert len(result) <= MAX_FILE_CHARS

def test_returned_total_never_exceeds_budget(tmp_path):
    tool = "read_file"
    root = make(tmp_path, {f"f{i}.js": f'name: "{tool}"' + "z" * 5000 for i in range(20)})
    budget = 12_000
    out = select_files(root, [tool], budget_bytes=budget)
    assert sum(len(text) for _, text in out) <= budget

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
