from pathlib import Path
from src.evidence import parse_citation, check_citation, check_tool, classify_evidence
from src.profile import ToolEffect

def tree(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "server.js").write_text(
        "line1\nline2\nfunction read_file(p) { return fs.readFileSync(p); }\nline4\n")
    return tmp_path

def test_parse_citation_forms():
    assert parse_citation("src/server.js:3") == [("src/server.js", 3, 3)]
    assert parse_citation("src/server.js:3-9") == [("src/server.js", 3, 9)]
    assert parse_citation("a.js:1; b.js:2-3") == [("a.js", 1, 1), ("b.js", 2, 3)]
    assert parse_citation("no line numbers here") == []

def test_check_citation_hit_and_miss(tmp_path):
    root = tree(tmp_path)
    assert check_citation(root, "src/server.js:3", ["readFileSync"]) is True
    assert check_citation(root, "src/server.js:1", ["readFileSync"]) is False   # wrong span
    assert check_citation(root, "src/missing.js:3", ["readFileSync"]) is False  # no such file

def test_check_tool_uses_tool_name_and_backticked_identifiers(tmp_path):
    root = tree(tmp_path)
    ok, _, cls = check_tool(root, ToolEffect(labels=["SECRET"], evidence=["src/server.js:3"],
                                        rationale="calls `readFileSync` on an agent path"), "read_file")
    assert ok is True and cls == "code"
    bad, reason, cls = check_tool(root, ToolEffect(labels=["SECRET"], evidence=["src/server.js:4"],
                                              rationale="calls `spawnSync`"), "read_file")
    assert bad is False and "no cited span" in reason and cls == "none"

def test_check_tool_without_citation_is_unverified(tmp_path):
    ok, reason, cls = check_tool(tree(tmp_path), ToolEffect(labels=["SINK"], evidence=[], rationale="x"), "post")
    assert ok is False and "no citation" in reason and cls == "none"

def test_classify_evidence_doc_vs_code():
    assert classify_evidence("README.md") == "doc"
    assert classify_evidence("docs/guide.rst") == "doc"
    assert classify_evidence("CHANGELOG") == "doc"
    assert classify_evidence("src/server.ts") == "code"
    assert classify_evidence("lib/coreBundle.js") == "code"

def doc_tree(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "server.js").write_text("function read_file(p) { return fs.readFileSync(p); }\n")
    (tmp_path / "README.md").write_text("## read_file\nCalls `readFileSync` under the hood.\n")
    return tmp_path

def test_check_tool_evidence_class_doc_only(tmp_path):
    root = doc_tree(tmp_path)
    effect = ToolEffect(labels=["SECRET"], evidence=["README.md:2"], rationale="calls `readFileSync`")
    ok, _, cls = check_tool(root, effect, "read_file")
    assert ok is True and cls == "doc"

def test_check_tool_evidence_class_code_when_code_citation_present_alongside_doc(tmp_path):
    root = doc_tree(tmp_path)
    effect = ToolEffect(labels=["SECRET"], evidence=["src/server.js:1", "README.md:2"],
                        rationale="calls `readFileSync`")
    ok, _, cls = check_tool(root, effect, "read_file")
    assert ok is True and cls == "code"

def test_check_tool_evidence_class_none_when_nothing_verifies(tmp_path):
    root = doc_tree(tmp_path)
    effect = ToolEffect(labels=["SECRET"], evidence=["README.md:1"], rationale="x")
    ok, reason, cls = check_tool(root, effect, "other_tool")
    assert ok is False and cls == "none"

def test_check_citation_rejects_paths_that_escape_root(tmp_path, tmp_path_factory):
    root = tree(tmp_path)
    # Hermetic stand-in for a real /etc/passwd: a file that genuinely exists, outside
    # root, containing the token — proving the rejection is containment, not mere absence.
    outside = tmp_path_factory.mktemp("outside")
    (outside / "passwd.conf").write_text("readFileSync\n")

    absolute_citation = f"{outside / 'passwd.conf'}:1"
    assert check_citation(root, absolute_citation, ["readFileSync"]) is False

    traversal = Path("..") / outside.relative_to(tmp_path.parent) / "passwd.conf"
    traversal_citation = f"{traversal}:1"
    assert check_citation(root, traversal_citation, ["readFileSync"]) is False

def test_check_citation_bare_basename_ambiguous_suffix_path_is_not(tmp_path):
    # Simulates an npm-style fetch prefix ("pkg/") the citation omits, with two same-named
    # files: a bare "index.js" must not guess between them, but a path-suffix disambiguates.
    (tmp_path / "pkg" / "src").mkdir(parents=True)
    (tmp_path / "pkg" / "lib").mkdir(parents=True)
    (tmp_path / "pkg" / "src" / "index.js").write_text("module.exports = read_file;\n")
    (tmp_path / "pkg" / "lib" / "index.js").write_text("module.exports = spawnSync;\n")

    assert check_citation(tmp_path, "index.js:1", ["read_file"]) is False
    assert check_citation(tmp_path, "src/index.js:1", ["read_file"]) is True

def range_tree(tmp_path):
    (tmp_path / "src2").mkdir()
    (tmp_path / "src2" / "server.js").write_text(
        "line1\nline2\nline3\nline4\nconst range_token = 1;\n")
    return tmp_path

def test_range_citation_widen_reaches_two_lines_past_end(tmp_path):
    root = range_tree(tmp_path)
    assert check_citation(root, "src2/server.js:2-3", ["range_token"]) is True

def test_single_line_citation_does_not_widen(tmp_path):
    root = range_tree(tmp_path)
    assert check_citation(root, "src2/server.js:3", ["range_token"]) is False

def test_check_tool_accepts_citation_into_copied_dependency_tree(tmp_path):
    # A dependency's source copied under the view's .deps by prepare_sources (bounded
    # dependency following) must verify just like the package's own tree —
    # proves the containment rule accepts .deps/... rather than special-casing it.
    (tmp_path / ".deps" / "dep-a" / "lib").mkdir(parents=True)
    (tmp_path / ".deps" / "dep-a" / "lib" / "x.js").write_text(
        "function browser_navigate(url) { return fetch(url); }\n")
    effect = ToolEffect(labels=["SINK"], evidence=[".deps/dep-a/lib/x.js:1"], rationale="calls `fetch`")
    ok, _, cls = check_tool(tmp_path, effect, "browser_navigate")
    assert ok is True and cls == "code"
