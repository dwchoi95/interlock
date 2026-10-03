"""Choose which source files to put in front of the model, cheaply and deterministically."""
from __future__ import annotations
import re
from collections import Counter
from pathlib import Path

SKIP_DIRS = {"node_modules", ".git", "dist-types", "__pycache__", "fixtures", "testdata", "vendor", "third_party",
             "third-party", "thirdparty", "test", "__tests__"}
SOURCE_SUFFIXES = {".js", ".mjs", ".cjs", ".ts", ".tsx", ".py", ".go", ".rs", ".cs", ".java", ".rb", ".json", ".yaml", ".yml", ".md", ".snap"}
TEST_MARKERS = (".test.", ".spec.", "_test.")
MAX_FILE_CHARS = 120_000
EXCERPT_WINDOWS = 20
EXCERPT_RADIUS = 1_500
WINDOW_SEPARATOR = "   ..."
# ponytail: bounded scan so a pathological file can't make position-finding O(n^2);
# raise this if a real provider file has more genuine tool-name hits than this.
MAX_POSITIONS_SCANNED = 20_000
# A file that names no tool is still worth showing when it defines the methods the tool files call on their
# response/result/context/client objects: the content a handler turns on is assembled there.
RECEIVER_CALL = re.compile(r"\b(?:response|res|resp|result|reply|ctx|context|client)\.(\w+)\(")
DEFN = re.compile(r"(?m)^\s*(?:export\s+|async\s+|static\s+|public\s+|private\s+|protected\s+|function\s+|def\s+|fn\s+|pub\s+"
                  r"|func\s+(?:\([^)]*\)\s*)?|const\s+|let\s+|get\s+)*#?(\w+)\s*(?:<[^>\n]*>)?\s*(?:\([^)\n]*\)\s*(?:->\s*[\w\[\], |]+)?"
                  r"\s*(?::\s*[\w<>\[\]| ,.]+)?\s*[{:=]|=\s*(?:async\s*)?(?:\([^)\n]*\)|\w+)\s*=>)")
NOT_CODE = {".md", ".json", ".yaml", ".yml", ".snap", ".lock", ".toml", ".txt", ".csv"}
MIN_DEFINED = 3
# The shapes in which a file that defines a tool states its name: a `name` key or argument, the first string of
# a registering call (`Tool("x"`, `server.tool("x"`, `NewTool("x"`), or a function of that name.
DEF_MARK = r"(?:name\s*[:=]\s*|['\"]name['\"]\s*:\s*|\(\s*)['\"]{name}['\"]|(?:def|function|func|fn)\s+{name}(?!\w)"


def _name_regex(tool_names: list[str]) -> re.Pattern | None:
    """One pass finds every tool name as a whole token: `fill` in `'fill'` or `fill(`, not in `polyfill` or
    `fill_form`. Longer names first so that a name that is a prefix of another does not steal its match."""
    names = sorted({n for n in tool_names if n}, key=len, reverse=True)
    return re.compile(r"(?<!\w)(?:" + "|".join(map(re.escape, names)) + r")(?!\w)") if names else None


def _aliased(root: Path, tool_names: list[str]) -> list[str]:
    """A tool whose advertised name occurs in no candidate file of the tree is looked for under the name without
    its first segment: a server that mounts sub-servers registers `add_comment` and advertises `jira_add_comment`
    (the checker applies the same rule when it verifies a citation)."""
    rx = _name_regex(tool_names)
    if rx is None:
        return tool_names
    seen: set[str] = set()
    for p in root.rglob("*"):
        if p.is_file() and is_candidate(p, root):
            try:
                seen |= set(rx.findall(p.read_text(errors="strict")))
            except (UnicodeDecodeError, ValueError):
                continue
    out = []
    for n in tool_names:
        m = re.match(r"[A-Za-z0-9]+[_\-.](.{6,})$", n)
        out.append(m.group(1) if n not in seen and m else n)
    return out


def _defines(text: str, name: str) -> bool:
    return re.search(DEF_MARK.format(name=re.escape(name)), text) is not None




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
    tool_names = _aliased(root, tool_names)
    rx = _name_regex(tool_names)
    scored: list[tuple[int, bool, int, str, str]] = []
    texts: dict[str, str] = {}        # rel -> original text of every scored file
    counts: dict[str, dict] = {}      # rel -> tool name -> mentions
    zero: list[tuple[str, str]] = []  # code files that name no tool
    called: set[str] = set()          # methods the tool files call on their response/result/context/client objects
    for p in sorted(root.rglob("*")):
        if not p.is_file() or not is_candidate(p, root):
            continue
        try:
            text = p.read_text(errors="strict")
        except (UnicodeDecodeError, ValueError):
            continue
        found = Counter(rx.findall(text)) if rx else Counter()
        is_doc = p.suffix.lower() == ".md"
        rel = str(p.relative_to(root))
        if not found:
            if p.suffix.lower() not in NOT_CODE:
                zero.append((rel, text))
            continue
        if not is_doc:
            called |= set(RECEIVER_CALL.findall(text))
        texts[rel], counts[rel] = text, found
        # Ranking is computed on the original text; only the returned copy is numbered.
        scored.append((-len(found), is_doc, len(text), rel, _excerpt(text, tool_names)))
    admitted: list[tuple[int, bool, int, str, str]] = []
    for rel, text in zero:
        defined = called & set(DEFN.findall(text))
        if len(defined) >= MIN_DEFINED:
            admitted.append((-len(defined), False, len(text), rel, _excerpt(text, tool_names)))
    # Order: each tool's defining file, then the admitted receiver classes, then the ranking. The defining file of
    # a tool is the code file that states its name in a defining shape (DEF_MARK) and names the fewest other
    # tools: a handler file over a CLI option table or a registry that lists every tool, so that a file naming
    # many tools cannot crowd out the handlers of the tools that occur in one file each; a snapshot, a lock file
    # or an __init__ that only imports the name is never a defining file. A server with one source file gets
    # exactly the list it got before.
    ranked = sorted(scored)
    must: list[int] = []
    for name in tool_names:
        holders = [(hits, -counts[rel][name], i) for i, (hits, is_doc, _, rel, _x) in enumerate(ranked)
                   if not is_doc and Path(rel).suffix.lower() not in NOT_CODE and counts[rel].get(name)
                   and _defines(texts[rel], name)]
        if holders:
            i = max(holders)[2]   # fewest distinct tool names (hits is negative), then the most mentions of this one
            if i not in must:
                must.append(i)
    ordered = [ranked[i] for i in must] + sorted(admitted) + [r for i, r in enumerate(ranked) if i not in set(must)]
    out, used = [], 0
    for _, _, _, rel, text in ordered:
        if used + len(text) > budget_bytes and out:
            break
        out.append((rel, text))
        used += len(text)
    return out
