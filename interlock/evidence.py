"""Re-check model-produced evidence against the source tree. No model involved here."""
from __future__ import annotations
import re
from pathlib import Path
from interlock.profile import ToolEffect

CITATION = re.compile(r"([\w./@+-]+\.[A-Za-z0-9]+):(\d+)(?:\s*-\s*(\d+))?")
WIDEN = 2

def parse_citation(text: str) -> list[tuple[str, int, int]]:
    out = []
    for m in CITATION.finditer(text or ""):
        start = int(m.group(2))
        out.append((m.group(1), start, int(m.group(3)) if m.group(3) else start))
    return out

def _span_text(root: Path, rel: str, start: int, end: int) -> str | None:
    f = Path(root) / rel
    if not f.is_file():
        for cand in Path(root).rglob(Path(rel).name):          # tolerate a path prefix we did not fetch
            f = cand
            break
        else:
            return None
    lines = f.read_text(errors="replace").splitlines()
    if start > len(lines):
        return None
    # A single-line citation must match its exact line: widening it would let an unrelated
    # nearby line (e.g. the real definition, mis-cited by a line or two) falsely verify.
    # A genuine range citation gets the widen, to tolerate drifted start/end boundaries.
    widen = WIDEN if end > start else 0
    lo, hi = max(0, start - 1 - widen), min(len(lines), end + widen)
    return "\n".join(lines[lo:hi])

def check_citation(root: Path, citation: str, must_contain: list[str]) -> bool:
    for rel, start, end in parse_citation(citation):
        span = _span_text(root, rel, start, end)
        if span is not None and any(tok and tok in span for tok in must_contain):
            return True
    return False

def check_tool(root: Path, effect: ToolEffect, tool_name: str) -> tuple[bool, str]:
    citations = [c for c in effect.evidence if parse_citation(c)]
    if not citations:
        return False, "no citation with a file:line reference"
    tokens = [tool_name] + re.findall(r"`([^`]+)`", effect.rationale or "")
    for c in citations:
        if check_citation(root, c, tokens):
            return True, "ok"
    return False, "no cited span contains the tool name or a backticked identifier"
