"""Choose which source files to put in front of the model, cheaply and deterministically."""
from __future__ import annotations
from pathlib import Path

SKIP_DIRS = {"node_modules", ".git", "dist-types", "__pycache__", "fixtures", "testdata", "vendor", "test", "__tests__"}
SOURCE_SUFFIXES = {".js", ".mjs", ".cjs", ".ts", ".tsx", ".py", ".go", ".rs", ".java", ".rb", ".json", ".yaml", ".yml", ".md", ".snap"}
TEST_MARKERS = (".test.", ".spec.", "_test.")
MAX_FILE_CHARS = 120_000
EXCERPT_WINDOWS = 20
EXCERPT_RADIUS = 1_500
WINDOW_SEPARATOR = "   ..."
# ponytail: bounded scan so a pathological file can't make position-finding O(n^2);
# raise this if a real provider file has more genuine tool-name hits than this.
MAX_POSITIONS_SCANNED = 20_000


def _is_test(name: str) -> bool:
    return name.startswith("test_") or any(m in name for m in TEST_MARKERS)


def is_candidate(p: Path, root: Path) -> bool:
    """True if `p` (a file under `root`) is one select_files would ever consider: not
    inside a skipped (vendored/test/build) directory, not a declaration-only `.d.ts`,
    not itself a test file, and on the source suffix allowlist. Shared with
    prepare_sources's missing-name detection and fetch_dependencies's keep decision so
    detection and selection never disagree about which files count."""
    rel = p.relative_to(root)
    if any(part in SKIP_DIRS for part in rel.parts):
        return False
    if p.name.lower().endswith(".d.ts"):
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


def _numbered_line(n: int, content: str) -> str:
    return f"{n:>6}| {content}"


def _number_all(text: str) -> str:
    """Every line of text, prefixed with its true 1-based line number."""
    return "\n".join(_numbered_line(i, line) for i, line in enumerate(text.splitlines(), start=1))


def _line_begin(text: str, x: int) -> int:
    """Offset of the start of the line containing offset x."""
    return text.rfind("\n", 0, x) + 1


def _line_end(text: str, x: int) -> int:
    """Offset of the end of the line containing offset x (the newline itself, or EOF)."""
    idx = text.find("\n", x)
    return len(text) if idx == -1 else idx


def _excerpt(text: str, tool_names: list[str]) -> str:
    """Numbered full text if it fits MAX_FILE_CHARS; otherwise whole-line windows around
    tool-name hits, each line numbered with its true line number in the original file,
    windows separated by a WINDOW_SEPARATOR line, capped at MAX_FILE_CHARS. A physical line
    longer than the window (a minified bundle, a one-line embedded schema) is not expanded;
    instead each occurrence on that line gets its own radius-clipped, truncation-marked
    slice, so multiple tools' evidence on the same over-long line all survive."""
    numbered_full = _number_all(text)
    if len(numbered_full) <= MAX_FILE_CHARS:
        return numbered_full

    positions = _spread(_tool_positions(text, tool_names))
    if not positions:
        return numbered_full[:MAX_FILE_CHARS]

    window_len = 2 * EXCERPT_RADIUS
    mergeable: list[tuple[int, int]] = []
    truncated: list[tuple[int, int]] = []
    for p in positions:
        ws = max(0, p - EXCERPT_RADIUS)
        we = min(len(text), p + EXCERPT_RADIUS)
        # Check the length of the physical line the hit is on, not the expanded window
        # (expanding an ordinary window out to whole lines can overshoot window_len by a
        # partial line at each edge; that's fine. What we're detecting here is a single
        # minified line so long it swallows the window on its own.)
        if _line_end(text, p) - _line_begin(text, p) > window_len:
            truncated.append((ws, we))
        else:
            mergeable.append((_line_begin(text, ws), _line_end(text, we)))

    # Two occurrences on the same over-long line whose ±radius spans overlap (closer together
    # than 2*EXCERPT_RADIUS) collapse into one slice instead of two redundant, overlapping ones.
    # Spans on different physical lines can never overlap here (line ranges are disjoint), so
    # merging the whole list is safe and never merges across a line boundary.
    blocks = [(start, end, False) for start, end in _merge_windows(mergeable)]
    blocks += [(start, end, True) for start, end in _merge_windows(truncated)]
    blocks.sort(key=lambda b: b[0])

    parts = []
    for start, end, is_truncated in blocks:
        line_no = text.count("\n", 0, start) + 1
        if is_truncated:
            line_start = _line_begin(text, start)
            line_end = _line_end(text, start)
            prefix = "…" if start > line_start else ""
            suffix = " …[truncated]" if end < line_end else ""
            parts.append(_numbered_line(line_no, prefix + text[start:end] + suffix))
        else:
            segment_lines = text[start:end].split("\n")
            parts.append("\n".join(_numbered_line(line_no + i, ln) for i, ln in enumerate(segment_lines)))

    return f"\n{WINDOW_SEPARATOR}\n".join(parts)[:MAX_FILE_CHARS]


def select_files(root: Path, tool_names: list[str], budget_bytes: int = 400_000) -> list[tuple[str, str]]:
    root = Path(root)
    scored: list[tuple[int, bool, int, str, str]] = []
    for p in sorted(root.rglob("*")):
        if not p.is_file() or not is_candidate(p, root):
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
        # Ranking is computed on the original text; only the returned copy is numbered.
        scored.append((-distinct_hits, is_doc, len(text), rel, _excerpt(text, tool_names)))
    out, used = [], 0
    for _, _, _, rel, text in sorted(scored):
        if used + len(text) > budget_bytes and out:
            break
        out.append((rel, text))
        used += len(text)
    return out
