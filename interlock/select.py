"""Choose which source files to put in front of the model, cheaply and deterministically."""
from __future__ import annotations
from pathlib import Path

SKIP_DIRS = {"node_modules", ".git", "dist-types", "__pycache__", "fixtures", "testdata", "vendor"}
SOURCE_SUFFIXES = {".js", ".mjs", ".cjs", ".ts", ".tsx", ".py", ".go", ".rs", ".java", ".rb", ".json", ".yaml", ".yml", ".md"}
TEST_MARKERS = (".test.", ".spec.", "_test.", "test_")
MAX_FILE_CHARS = 120_000

def _is_candidate(p: Path, root: Path) -> bool:
    rel = p.relative_to(root)
    if any(part in SKIP_DIRS for part in rel.parts):
        return False
    if p.suffix.lower() not in SOURCE_SUFFIXES:
        return False
    return not any(m in p.name for m in TEST_MARKERS)

def select_files(root: Path, tool_names: list[str], budget_bytes: int = 400_000) -> list[tuple[str, str]]:
    root = Path(root)
    scored: list[tuple[int, int, str, str]] = []
    for p in sorted(root.rglob("*")):
        if not p.is_file() or not _is_candidate(p, root):
            continue
        try:
            text = p.read_text(errors="strict")
        except (UnicodeDecodeError, ValueError):
            continue
        hits = sum(text.count(name) for name in tool_names)
        if hits:
            scored.append((-hits, len(text), str(p.relative_to(root)), text[:MAX_FILE_CHARS]))
    out, used = [], 0
    for _, _, rel, text in sorted(scored):
        if used + len(text) > budget_bytes and out:
            break
        out.append((rel, text))
        used += len(text)
    return out
