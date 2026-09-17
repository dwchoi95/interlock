"""Re-check model-produced evidence against the source tree. No model involved here."""
from __future__ import annotations
import re
from pathlib import Path
from interlock.profile import ToolEffect

CITATION = re.compile(r"([\w./@+-]+\.[A-Za-z0-9]+):(\d+)(?:\s*-\s*(\d+))?")
WIDEN = 2

DOC_SUFFIXES = {".md", ".markdown", ".rst", ".txt", ".adoc"}
DOC_NAME_PREFIXES = ("readme", "changelog", "license")

def classify_evidence(rel: str) -> str:
    """"doc" for documentation (README/CHANGELOG/LICENSE or a doc suffix), else "code".
    Free text may raise an effect but never lower one, so a citation into documentation
    must never on its own justify clearing a tool or standing in for verified code."""
    p = Path(rel)
    if p.suffix.lower() in DOC_SUFFIXES or p.name.lower().startswith(DOC_NAME_PREFIXES):
        return "doc"
    return "code"

def parse_citation(text: str) -> list[tuple[str, int, int]]:
    out = []
    for m in CITATION.finditer(text or ""):
        start = int(m.group(2))
        out.append((m.group(1), start, int(m.group(3)) if m.group(3) else start))
    return out

def _resolve_path(root: Path, rel: str) -> Path | None:
    """Map a cited relative path to a real file inside root, or None. Never escapes root:
    an absolute citation or one laced with '..' is rejected even if it happens to point at
    a real file, and a bare or ambiguous basename fallback match is refused rather than
    guessed (an absolute right-hand side makes `Path(root) / rel` discard root entirely,
    which is exactly what let a citation such as "/etc/passwd:1" resolve outside the tree)."""
    root = Path(root).resolve()
    direct = (root / rel).resolve()
    if direct.is_relative_to(root) and direct.is_file():
        return direct
    # Tolerate a path prefix we did not fetch (e.g. an npm tarball's "package/" root) by
    # matching on basename, but only accept an unambiguous, path-suffix-consistent hit —
    # a bare "index.js" citation must not silently pick one of several same-named files.
    rel_parts = Path(rel).parts
    candidates = sorted(
        p for p in root.rglob(Path(rel).name)
        if p.is_file() and p.resolve().is_relative_to(root) and p.resolve().parts[-len(rel_parts):] == rel_parts
    )
    return candidates[0] if len(candidates) == 1 else None

def _span_text(root: Path, rel: str, start: int, end: int) -> str | None:
    f = _resolve_path(root, rel)
    if f is None:
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

def _verifying_classes(root: Path, citation: str, must_contain: list[str]) -> set[str]:
    """Evidence classes ("code"/"doc") of every file:line reference within `citation`
    whose span actually contains one of `must_contain`. A single evidence string may
    hold several ';'-separated references; each is classified independently."""
    classes: set[str] = set()
    for rel, start, end in parse_citation(citation):
        span = _span_text(root, rel, start, end)
        if span is not None and any(tok and tok in span for tok in must_contain):
            classes.add(classify_evidence(rel))
    return classes

def check_citation(root: Path, citation: str, must_contain: list[str]) -> bool:
    return bool(_verifying_classes(root, citation, must_contain))

def check_tool(root: Path, effect: ToolEffect, tool_name: str) -> tuple[bool, str, str]:
    """Re-check `effect`'s evidence. Returns (verified, reason, evidence_class):
    evidence_class is "code" if a verifying citation is a code file, "doc" if only
    documentation citations verify, "none" if nothing verifies."""
    citations = [c for c in effect.evidence if parse_citation(c)]
    if not citations:
        return False, "no citation with a file:line reference", "none"
    tokens = [tool_name] + re.findall(r"`([^`]+)`", effect.rationale or "")
    classes: set[str] = set()
    for c in citations:
        classes |= _verifying_classes(root, c, tokens)
    if not classes:
        return False, "no cited span contains the tool name or a backticked identifier", "none"
    return True, "ok", "code" if "code" in classes else "doc"
