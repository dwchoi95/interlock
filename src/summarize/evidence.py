"""Re-check model-produced evidence against the source tree. No model involved here."""
from __future__ import annotations
import re
from pathlib import Path
from src.summarize.profile import ToolEffect

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
    # Every citation is read with the same WIDEN-line margin: a model that cites a definition's head line
    # (the line before the one holding the tool's name) has cited the right place, and a range with drifted
    # boundaries likewise; a citation in the wrong file or function still fails, since the margin is two lines.
    lo, hi = max(0, start - 1 - WIDEN), min(len(lines), end + WIDEN)
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

# ---- the call chain from a handler span: the methods it calls on other objects, where each is defined, and where
# the fields that definition writes are read. The pipeline's follow-up quotes these places to the judge, so that
# content a handler turns on with a flag (a response class, a formatter) is read where it is produced.
# Deterministic, language-agnostic (JS/TS, Python, Go, Rust shapes), no names of any server.
CALL = re.compile(r"(?<![\w.])(?:[\w.]+\.)?(\w+)\s*\(")
_KW = (r"(?:export\s+|async\s+|static\s+|public\s+|private\s+|protected\s+|function\s+|def\s+|fn\s+|pub\s+"
       r"|func\s+(?:\([^)]*\)\s*)?|const\s+|let\s+|get\s+)")
FIELD_WRITE = re.compile(r"\b(?:this|self|cls|[a-z]\w*)\.(#?\w+)\s*=[^=]")
NOISE = set("push join map filter toString length catch then get set slice split replace trim includes indexOf forEach keys "
            "values entries log error warn stringify parse test match find some every reduce concat startsWith endsWith "
            "toLowerCase toUpperCase describe optional string number boolean object array enum default min max int append "
            "extend items format strip lower upper encode decode isoformat add remove pop update copy sort reverse Error "
            "resolve reject all json text status_code raise_for_status dumps loads write read close open exists is_file "
            "mkdir apply call bind assign from isArray freeze now toISOString padStart padEnd repeat substring charAt sub "
            "search finditer findall compile "
            # control statements and builtins that a `NAME(` at the start of a line can also be
            "for while switch if else elif return await typeof try new delete void yield with assert raise str float len "
            "list dict tuple print bool type range super isinstance getattr setattr hasattr String Number Boolean Object "
            "Array Promise Symbol Date RegExp Map Set JSON Math console require import export defer go select".split())
_TREES: dict[str, dict[str, str]] = {}


def _tree(root: Path) -> dict[str, str]:
    """rel -> text of every code file the selector could show (cached per root)."""
    key = str(Path(root).resolve())
    if key not in _TREES:
        from src.summarize.select import is_candidate
        out: dict[str, str] = {}
        for f in sorted(Path(root).rglob("*")):
            if not f.is_file() or not is_candidate(f, root):
                continue
            rel = str(f.relative_to(root))
            if classify_evidence(rel) == "doc" or f.suffix.lower() in {".json", ".yaml", ".yml", ".snap"}:
                continue
            try:
                text = f.read_text(errors="strict")
            except (UnicodeDecodeError, ValueError):
                continue
            if len(text) <= 1_000_000:
                out[rel] = text
        _TREES[key] = out
    return _TREES[key]


def _def_line(text: str, method: str) -> int | None:
    """The line that defines `method`: a keyworded form (`def x(`, `function x(`, `export const x = (...) =>`,
    `func (r *T) x(`, `pub fn x(`) or a class-method shape that opens a block (`x(a, b) {`). A call at the start
    of a line (`str(e),`) has neither, and control statements are in NOISE."""
    if method in NOISE:
        return None
    name = re.escape(method)
    m = (re.search(r"(?m)^[ \t]*" + _KW + r"+#?" + name + r"\s*(?:<[^>\n]*>)?\s*(?:\(|=\s*(?:async\s*)?(?:\([^)\n]*\)|\w+)\s*=>)", text)
         or re.search(r"(?m)^[ \t]*(?:async\s+)?#?" + name + r"\s*(?:<[^>\n]*>)?\s*\([^)\n]*\)\s*(?::\s*[\w<>\[\]| ,.]+)?\s*\{", text))
    return text.count("\n", 0, m.start()) + 1 if m else None


def _bracket_block_end(lines: list[str], start: int, cap: int = 400) -> int | None:
    """The line where the brackets the head line opens close again, skipping string and template literals and
    line comments; None when the head opens no bracket or they do not close within `cap` lines."""
    depth = 0
    opened = False
    quote: str | None = None
    for i in range(start, min(len(lines), start + cap) + 1):
        ln = lines[i - 1]
        j = 0
        while j < len(ln):
            ch = ln[j]
            if quote:
                if ch == "\\":
                    j += 2
                    continue
                if ch == quote:
                    quote = None
            elif ch in "'\"`":
                quote = ch
            elif ln.startswith("//", j) or ln.startswith("#", j) and not ln.startswith("#!", j):
                break
            elif ch in "([{":
                depth += 1; opened = True
            elif ch in ")]}":
                depth -= 1
            j += 1
        if quote == "`":
            continue   # a template literal may span lines
        quote = None
        if opened and depth <= 0:
            return i   # closed by the end of this line (a `) {` head keeps the block open)
        if i == start and not opened:
            return None
    return None


def _block_end(lines: list[str], start: int) -> int:
    """Last line of the block that starts at 1-based `start`: where the bracket the head opens closes, if it
    does within reach (a JS/TS object or call, a Python literal), else up to the next non-blank line indented no
    deeper (a Python or indentation-defined block)."""
    by_bracket = _bracket_block_end(lines, start)
    if by_bracket is not None and by_bracket > start:
        return by_bracket
    indent = lambda ln: len(ln) - len(ln.lstrip(" \t"))
    base, i = indent(lines[start - 1]), start
    while i < len(lines) and (not lines[i].strip() or indent(lines[i]) > base):
        i += 1
    return max(start, i)


def method_chain(root: Path, method: str, exclude: tuple[str, int, int] | None = None) -> tuple[str, int, list[int]] | None:
    """(def_file, def_line, lines reading a field the definition writes) for `method`, or None when no file of
    the tree defines it; `exclude` is a span whose own definitions do not count (the calling handler)."""
    tree = _tree(root)
    for drel, dtext in sorted(tree.items(), key=lambda kv: (exclude is not None and kv[0] != exclude[0], len(kv[0]))):
        dl = _def_line(dtext, method)
        if dl is None or (exclude is not None and drel == exclude[0] and exclude[1] <= dl <= exclude[2]):
            continue
        dlines = dtext.split("\n")
        de = _block_end(dlines, dl)
        fields = list(dict.fromkeys(FIELD_WRITE.findall("\n".join(dlines[dl - 1:de]))))
        reads = [i for f in fields for i, ln in enumerate(dlines, start=1)
                 if not (dl <= i <= de) and re.search(r"\." + re.escape(f) + r"(?!\w)", ln)
                 and not re.search(r"\." + re.escape(f) + r"(?!\w)\s*=(?!=)", ln)]
        return drel, dl, sorted(set(reads))[:8]
    return None


def reach(root: Path, rel: str, start: int, end: int) -> list[tuple[str, str, int, list[int]]]:
    """(method, def_file, def_line, lines reading a field the definition writes) for every method that lines
    start..end of `rel` call and another place in the tree defines."""
    tree = _tree(root)
    if rel not in tree:
        return []
    lines = tree[rel].split("\n")
    span = "\n".join(lines[start - 1:end])
    called = [m for m in dict.fromkeys(CALL.findall(span)) if m not in NOISE and len(m) > 2]
    out = []
    for m in called:
        chain = method_chain(root, m, (rel, start, end))
        if chain is not None:
            out.append((m, *chain))
    return out


GUARDED_LINES = 60
QUOTE_LINES_PER_METHOD = 120


def guarded_blocks(root: Path, drel: str, reads: list[int], depth: int = 2) -> list[tuple[int, int]]:
    """(start, end) of the block each read of the field guards, at most GUARDED_LINES long; a one-line
    `return this.x` getter is not a block. A field written inside such a block (a snapshot the flag collects, a
    list it fills) is followed one level further: the blocks its own reads guard are added, so that content a
    flag collects in one method and a formatter writes out in another is reached."""
    text = _tree(root).get(drel)
    if text is None:
        return []
    lines = text.split("\n")
    out: list[tuple[int, int]] = []
    seen: set[int] = set()
    frontier = list(reads)
    for _level in range(depth):
        nxt: list[int] = []
        for r in frontier:
            if r in seen or r > len(lines) or lines[r - 1].strip().startswith("return"):
                continue
            seen.add(r)
            end = min(_block_end(lines, r), r + GUARDED_LINES - 1, len(lines))
            out.append((r, end))
            for f in dict.fromkeys(FIELD_WRITE.findall("\n".join(lines[r - 1:end]))):
                nxt += [i for i, ln in enumerate(lines, start=1)
                        if not (r <= i <= end) and re.search(r"\." + re.escape(f) + r"(?!\w)", ln)
                        and not re.search(r"\." + re.escape(f) + r"(?!\w)\s*=(?!=)", ln)]
        frontier = sorted(set(nxt))[:8]
        if not frontier:
            break
    # merge overlaps, keep source order
    merged: list[tuple[int, int]] = []
    for a, b in sorted(out):
        if merged and a <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    return merged


def quote_blocks(root: Path, drel: str, blocks: list[tuple[int, int]], cap: int = QUOTE_LINES_PER_METHOD) -> list[str]:
    """Numbered text of the blocks, at most `cap` lines in all, as `file:start-end` headed strings."""
    text = _tree(root).get(drel)
    if text is None:
        return []
    lines = text.split("\n")
    out, used = [], 0
    for a, b in blocks:
        if used >= cap or a > len(lines):
            break
        b = min(b, a + (cap - used) - 1, len(lines))   # a citation past the end of the file is cut at the end
        out.append(f"{drel}:{a}-{b}\n" + "\n".join(f"{i}| {lines[i - 1]}" for i in range(a, b + 1)))
        used += b - a + 1
    return out


# A line inside a guarded block that can put text into the result: a push/append/write call, a return, or a
# template that interpolates a value. Language-agnostic shapes; no server, method or field name.
OUTPUT_STATEMENT = re.compile(r"\.(?:push|append|appendLine|add|write|extend|insert|unshift)\s*\(|\breturn\b|\$\{|%s|\{\w+\}|f\"|f'|\.format\(|\+=")


def candidate_statements(root: Path, drel: str, blocks: list[tuple[int, int]], cap: int | None = None) -> list[tuple[int, str]]:
    """(line, text) of every output statement inside the blocks, in source order (all of them unless `cap`)."""
    text = _tree(root).get(drel)
    if text is None:
        return []
    lines = text.split("\n")
    out: list[tuple[int, str]] = []
    for a, b in blocks:
        for i in range(a, min(b, len(lines)) + 1):
            ln = lines[i - 1].strip()
            if OUTPUT_STATEMENT.search(ln) and not ln.startswith(("//", "#", "*", "/*")):
                out.append((i, ln[:200]))
    return out if cap is None else out[:cap]


def _interpolations(stmt: str) -> list[str]:
    """The interpolated expressions of a template statement: JS `${expr}` and, on an f-string or .format line,
    Python `{expr}`; braces are balanced one level. A line without templates yields none."""
    exprs: list[str] = []
    is_js = "${" in stmt
    is_py = bool(re.search(r"\bf['\"]|\.format\(", stmt)) and not is_js
    if not (is_js or is_py):
        return exprs
    i = 0
    while i < len(stmt):
        start = stmt.find("${" if is_js else "{", i)
        if start == -1:
            break
        j = start + (2 if is_js else 1)
        depth = 1
        while j < len(stmt) and depth:
            depth += {"{": 1, "}": -1}.get(stmt[j], 0)
            j += 1
        expr = stmt[start + (2 if is_js else 1):j - 1].strip()
        if expr and not (is_py and expr in ("", "}")) and not expr.startswith("{"):
            exprs.append(expr[:80])
        i = j
    return exprs


def candidate_elements(root: Path, drel: str, blocks: list[tuple[int, int]]) -> list[dict]:
    """Every candidate the classifier must answer for: one per output statement, and for a template statement one
    more per interpolated expression (so `title` and `url` on one line are separate candidates) plus one for the
    template's fixed text. Each carries a stable id `<file>:<line>#<k>` (k = 0 the statement or the fixed text)."""
    out: list[dict] = []
    for line, text in candidate_statements(root, drel, blocks):
        exprs = _interpolations(text)
        out.append({"id": f"{drel}:{line}#0", "file": drel, "line": line, "text": text,
                    "element": "fixed text of the template" if exprs else "the statement"})
        for k, expr in enumerate(exprs, start=1):
            out.append({"id": f"{drel}:{line}#{k}", "file": drel, "line": line, "text": text, "element": expr})
    return out


# ---- where supplied code runs, read off the line that runs it. A known browser-automation API (Puppeteer,
# Playwright, Selenium) runs the code inside the page, whatever the callback contains; a known process, shell,
# interpreter or eval API runs it on the host; any other line is unknown.
PAGE_API = re.compile(r"\.(?:evaluate|evaluateOnNewDocument|evaluateHandle|addScriptTag|addInitScript|exposeFunction|"
                      r"execute_script|executeScript|execute_async_script|evaluate_handle|evaluate_on_new_document|"
                      r"add_init_script|add_script_tag|\$eval|\$\$eval)\s*\(")
HOST_API = re.compile(r"\b(?:child_process|subprocess|os\.system|os\.popen|os\.exec\w*|Popen|execSync|execFileSync|execFile|"
                      r"spawnSync|spawn|fork|runpy|vm\.run\w*|new\s+Function|shell=True|exec_command|run_command)\b|"
                      r"(?<![\w.])(?:exec|eval|system)\s*\(")


def exec_where(root: Path, at: str) -> str | None:
    """"browser_page" when the cited line hands code to a known browser-automation API, "host" when it calls a
    known host execution API, "unknown" otherwise (an assignment, an unknown method), None when the line cannot
    be read."""
    for rel, s, e in parse_citation(at or ""):
        f = _resolve_path(root, rel)
        if f is None:
            continue
        lines = f.read_text(errors="replace").splitlines()
        span = "\n".join(lines[s - 1:e])   # the cited lines exactly: a neighbouring line must not decide
        if not span:
            continue
        if PAGE_API.search(span):
            return "browser_page"
        if HOST_API.search(span):
            return "host"
        return "unknown"
    return None


# ---- where an outbound call goes, resolved from code. Only the destination can decide it: the receiver of the
# call (a client whose base URL is configured or written in the code) and the argument in the address position
# (a URL, a recipient), followed through their definitions; the payload and other arguments are never followed.
URL_LITERAL = re.compile(r"https?://([\w.-]+)")
CONFIG_READ = re.compile(r"process\.env|os\.environ|getenv|environ\[|\bdotenv\b|\bconfig\b|\bsettings\b|baseUrl|base_url|BASE_URL|"
                         r"[A-Z][A-Z0-9_]*_URL\b|[A-Z][A-Z0-9_]*_HOST\b|\bendpoint\b|\bargv\b|--base|--url|--host", re.I)
IDENT = re.compile(r"[A-Za-z_]\w{2,}")
MAX_RESOLVE = 24
CALL_SHAPE = re.compile(r"((?:[\w.#$]+\.)?\w+)\s*\(")


def _destination_seeds(line: str) -> list[str]:
    """Identifiers of the call's receiver chain and of its first argument (the address position), for the last
    call on the line that has a dotted receiver or a URL-ish name; nothing from the other arguments."""
    line = re.sub(r"\(0,\s*([\w.$]+)\)\s*\(", r"\1(", line)   # CommonJS indirect call `(0, mod.fn)(...)`
    best = None
    for m in CALL_SHAPE.finditer(line):
        callee = m.group(1)
        if "." in callee or re.search(r"fetch|request|http|url|goto|navigate|send|post|get|put|delete|open|connect|generate|call|client",
                                      callee, re.I):
            best = m
    if best is None:
        return []
    callee = best.group(1)
    receiver = callee.replace(".", " ")   # the receiver chain and the function itself (a module's helper knows the destination)
    # the first argument: up to the first top-level comma
    j, depth = best.end(), 0
    while j < len(line):
        ch = line[j]
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            if depth == 0:
                break
            depth -= 1
        elif ch == "," and depth == 0:
            break
        j += 1
    first_arg = line[best.end():j]
    seeds = [w for w in IDENT.findall(receiver) if w not in NOISE] + [w for w in IDENT.findall(first_arg) if w not in NOISE]
    return list(dict.fromkeys(seeds))


def _definition_blocks(root: Path, ident: str) -> list[tuple[str, int, str]]:
    """(file, line, block) where `ident` is defined or assigned, in at most three code files."""
    out = []
    rx = re.compile(r"(?m)^[ \t]*(?:export\s+|const\s+|let\s+|var\s+|static\s+|async\s+|def\s+|function\s+|fn\s+|pub\s+|func\s+)*"
                    r"(?:(?:self|this|cls)\.#?)?" + re.escape(ident) + r"\s*(?:[:=(]|\s*=)")   # `x = `, `def x(`, `this.x = `
    for rel, text in _tree(root).items():
        lines = text.split("\n")
        for m in rx.finditer(text):   # a declaration and a later assignment both count
            start = text.count("\n", 0, m.start()) + 1
            end = min(_block_end(lines, start), start + GUARDED_LINES - 1, len(lines))
            out.append((rel, start, "\n".join(lines[start - 1:end])))
            if len(out) >= 4:
                return out
    return out


def _followable(block: str) -> str:
    """The part of a definition block that can carry the destination onward: the right-hand side of an
    assignment, or the return lines and the address-ish lines of a function; never a whole body."""
    first = block.split("\n", 1)[0]
    is_function = bool(re.match(r"^\s*(?:export\s+)?(?:def|function|async|fn|pub|func)\b", first)) or "=>" in first or "function" in first
    if "=" in first and not is_function:
        return first.split("=", 1)[1] + "\n" + "\n".join(ln for ln in block.split("\n")[1:3])
    return "\n".join(ln for ln in block.split("\n") if re.search(r"\breturn\b|https?://|url|base|host|endpoint|env|config", ln, re.I))


def resolve_destination(root: Path, at: str, token: str) -> tuple[str | None, str]:
    """("literal", host) when the destination reaches a URL literal, ("configured", marker) when it reaches a
    configuration read, ("literal", "host (configuration override: marker)") when a literal default has a
    configured override, (None, "") when the destination cannot be followed within MAX_RESOLVE identifiers."""
    seeds: list[str] = []
    for rel, s, e in parse_citation(at or ""):
        f = _resolve_path(root, rel)
        if f is not None:
            lines = f.read_text(errors="replace").splitlines()
            span = "\n".join(lines[s - 1:e])
            for ln in span.split("\n"):
                seeds += _destination_seeds(ln)
            # a literal or a configuration read on the cited line counts only in the address position
            for ln in span.split("\n"):
                m = CALL_SHAPE.search(ln)
                if m:
                    head = ln[m.end():].split(",", 1)[0]
                    u = URL_LITERAL.search(head)
                    if u:
                        return "literal", u.group(1)
                    c = CONFIG_READ.search(head)
                    if c:
                        return "configured", c.group(0)
    if not seeds and token and re.fullmatch(r"[\w.#$]+", token.strip()):
        seeds = [w for w in IDENT.findall(token) if w not in NOISE]   # the constant the model named
    seen: set[str] = set()
    queue = list(dict.fromkeys(seeds))
    literal, configured = "", ""
    while queue and len(seen) < MAX_RESOLVE:
        ident = queue.pop(0)
        if ident in seen:
            continue
        seen.add(ident)
        for _rel, _line, block in _definition_blocks(root, ident):
            part = _followable(block)
            u = URL_LITERAL.search(part)
            if u and not literal:
                literal = u.group(1)
            c = CONFIG_READ.search(part)
            if c and not configured:
                configured = c.group(0)
            queue += [w for w in dict.fromkeys(IDENT.findall(part)) if w not in NOISE and w not in seen]
    if literal and configured:
        return "literal", f"{literal} (configuration override: {configured})"
    if literal:
        return "literal", literal
    if configured:
        return "configured", configured
    return None, ""


def in_tree_code(root: Path, token: str) -> bool:
    """Whether `token` (a host, a constant, a configuration variable) occurs in a code file of the tree."""
    token = (token or "").strip()
    return len(token) >= 4 and any(token in t for t in _tree(root).values())


def _name_tokens(root: Path, tool_name: str) -> list[str]:
    """The tool's name, and, when that name occurs in no code file of the tree, the name without its first
    segment: a server that mounts sub-servers registers `add_comment` and advertises `jira_add_comment`."""
    try:
        tree = _tree(root)
    except Exception:  # noqa: BLE001 - an unreadable tree changes nothing about the name
        return [tool_name]
    rx = re.compile(r"(?<!\w)" + re.escape(tool_name) + r"(?!\w)")
    if any(rx.search(t) for t in tree.values()):
        return [tool_name]
    m = re.match(r"[A-Za-z0-9]+[_\-.](.{6,})$", tool_name)
    return [tool_name, m.group(1)] if m else [tool_name]


HANDLER_LINES = 200


def handler_block(root: Path, rel: str, name: str) -> tuple[int, int] | None:
    """The block of `rel` that registers or defines the tool `name`, fixed from the code: the line that states the
    name in a defining shape (select.DEF_MARK), widened up to the nearest enclosing head with less indentation and
    down to the end of that block (at most HANDLER_LINES lines)."""
    from src.summarize.select import DEF_MARK
    f = _resolve_path(root, rel)
    if f is None:
        return None
    text = f.read_text(errors="replace")
    m = re.search(DEF_MARK.format(name=re.escape(name)), text)
    if m is None:
        return None
    lines = text.split("\n")
    at = text.count("\n", 0, m.start()) + 1
    indent = lambda ln: len(ln) - len(ln.lstrip(" \t"))
    head, base = at, indent(lines[at - 1])
    for i in range(at - 1, max(0, at - 40), -1):
        if lines[i - 1].strip() and indent(lines[i - 1]) < base:
            head = i
            break
    return head, max(min(_block_end(lines, head), head + HANDLER_LINES - 1, len(lines)), at)


def _inside_handler(root: Path, citation: str, tool_name: str) -> set[str]:
    """Evidence classes of the references in `citation` that fall inside the tool's own handler block: a span the
    registration point fixes verifies without naming the tool, since the block is the tool's by construction."""
    classes: set[str] = set()
    for rel, start, end in parse_citation(citation):
        if classify_evidence(rel) == "doc":
            continue
        hb = handler_block(root, rel, tool_name)
        if hb and hb[0] <= start and end <= hb[1]:
            classes.add("code")
    return classes


def check_tool(root: Path, effect: ToolEffect, tool_name: str) -> tuple[bool, str, str]:
    """Re-check `effect`'s evidence. Returns (verified, reason, evidence_class):
    evidence_class is "code" if a verifying citation is a code file, "doc" if only
    documentation citations verify, "none" if nothing verifies."""
    citations = [c for c in effect.evidence if parse_citation(c)]
    if not citations:
        return False, "no citation with a file:line reference", "none"
    tokens = _name_tokens(root, tool_name) + re.findall(r"`([^`]+)`", effect.rationale or "")
    classes: set[str] = set()
    for c in citations:
        classes |= _verifying_classes(root, c, tokens) or _inside_handler(root, c, tool_name)
    if not classes:
        return False, "no cited span contains the tool name or a backticked identifier, or lies inside the tool's handler block", "none"
    return True, "ok", "code" if "code" in classes else "doc"
