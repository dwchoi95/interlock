from pathlib import Path
from interlock.evidence import parse_citation, check_citation, check_tool
from interlock.profile import ToolEffect

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
    ok, _ = check_tool(root, ToolEffect(labels=["SECRET"], evidence=["src/server.js:3"],
                                        rationale="calls `readFileSync` on an agent path"), "read_file")
    assert ok is True
    bad, reason = check_tool(root, ToolEffect(labels=["SECRET"], evidence=["src/server.js:4"],
                                              rationale="calls `spawnSync`"), "read_file")
    assert bad is False and "no cited span" in reason

def test_check_tool_without_citation_is_unverified(tmp_path):
    ok, reason = check_tool(tree(tmp_path), ToolEffect(labels=["SINK"], evidence=[], rationale="x"), "post")
    assert ok is False and "no citation" in reason
