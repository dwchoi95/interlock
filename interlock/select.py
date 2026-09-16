"""Choose which source files to put in front of the model, cheaply and deterministically."""
from __future__ import annotations
from pathlib import Path

SKIP_DIRS = {"node_modules", ".git", "dist-types", "__pycache__", "fixtures", "testdata", "vendor"}
SOURCE_SUFFIXES = {".js", ".mjs", ".cjs", ".ts", ".tsx", ".py", ".go", ".rs", ".java", ".rb", ".json", ".yaml", ".yml", ".md", ".snap"}
TEST_MARKERS = (".test.", ".spec.", "_test.")
MAX_FILE_CHARS = 120_000
EXCERPT_WINDOWS = 20
EXCERPT_RADIUS = 1_500
# ponytail: bounded scan so a pathological file can't make position-finding O(n^2);
# raise this if a real provider file has more genuine tool-name hits than this.
MAX_POSITIONS_SCANNED = 20_000


def _is_test(name: str) -> bool:
    return name.startswith("test_") or any(m in name for m in TEST_MARKERS)


def _is_candidate(p: Path, root: Path) -> bool:
    rel = p.relative_to(root)
    if any(part in SKIP_DIRS for part in rel.parts):
        return False
    if p.suffix.lower() not in SOURCE_SUFFIXES:
        return False
    return not _is_test(p.name)


def _tool_positions(text: str, tool_names: list[str]) -> list[int]:
    """Start offsets of every tool-name occurrence, sorted, capped at MAX_POSITIONS_SCANNED."""
    positions: list[int] = []
    for name in tool_names:
        if not name:
            continue
        start = 0
        while len(positions) < MAX_POSITIONS_SCANNED:
            idx = text.find(name, start)
            if idx == -1:
                break
            positions.append(idx)
            start = idx + 1
    positions.sort()
    return positions


def _spread(positions: list[int], k: int = EXCERPT_WINDOWS) -> list[int]:
    """Pick up to k positions evenly spread across the (sorted) list, not just the first k."""
    if len(positions) <= k:
        return positions
    step = len(positions) / k
    return [positions[int(i * step)] for i in range(k)]


def _merge_windows(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _excerpt(text: str, tool_names: list[str]) -> str:
    """Full text if it fits the budget; otherwise windows around tool-name hits, each
    labeled with its true starting line number in the original file, capped at MAX_FILE_CHARS."""
    if len(text) <= MAX_FILE_CHARS:
        return text
    positions = _spread(_tool_positions(text, tool_names))
    if not positions:
        return text[:MAX_FILE_CHARS]
    spans = [(max(0, p - EXCERPT_RADIUS), min(len(text), p + EXCERPT_RADIUS)) for p in positions]
    parts = []
    for start, end in _merge_windows(spans):
        line_no = text.count("\n", 0, start) + 1
        parts.append(f"... [line {line_no}] ...\n{text[start:end]}")
    return "\n".join(parts)[:MAX_FILE_CHARS]


def select_files(root: Path, tool_names: list[str], budget_bytes: int = 400_000) -> list[tuple[str, str]]:
    root = Path(root)
    scored: list[tuple[int, bool, int, str, str]] = []
    for p in sorted(root.rglob("*")):
        if not p.is_file() or not _is_candidate(p, root):
            continue
        try:
            text = p.read_text(errors="strict")
        except (UnicodeDecodeError, ValueError):
            continue
        distinct_hits = sum(1 for name in tool_names if name in text)
        if not distinct_hits:
            continue
        is_doc = p.suffix.lower() == ".md"
        rel = str(p.relative_to(root))
        scored.append((-distinct_hits, is_doc, len(text), rel, _excerpt(text, tool_names)))
    out, used = [], 0
    for _, _, _, rel, text in sorted(scored):
        if used + len(text) > budget_bytes and out:
            break
        out.append((rel, text))
        used += len(text)
    return out
