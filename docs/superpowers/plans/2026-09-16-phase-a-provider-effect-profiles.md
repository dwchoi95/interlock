# Phase A: Provider Effect Profiles Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce evidence-checked, per-tool effect profiles for MCP servers at scale, and measure how often model-produced claims survive deterministic verification, so Gate A can decide whether the technique phases proceed.

**Architecture:** A profile is built per `(package, version)`. `source.py` fetches the package's source without executing it, `surface.py` supplies the advertised tool list, `adjudicate.py` asks Claude for a per-tool effect judgement that must cite `file:line` evidence, `evidence.py` deterministically re-checks every citation against the fetched source, and `pipeline.py` demotes unverified claims to `undetermined`, applies the least-restrictive default, and writes `profile.json`. Everything is file-in, file-out, so each stage is testable alone.

**Tech Stack:** Python 3.13, `anthropic` SDK (Claude Opus 5, structured outputs via `output_config`, Message Batches for bulk), pytest, Docker (already used by the spikes for `tools/list`), stdlib `dataclasses`/`json` for the schema.

**Spec:** `docs/superpowers/specs/2026-09-16-interlock-design.md` (Phase A and Gate A in §7; trust ranking in §5).

## Global Constraints

- Never execute fetched server code. `npm pack`, `pip download`, `git clone` only; no `npm install`, no running the server outside the existing Docker harness.
- Effect labels are exactly `SECRET`, `UNTRUSTED`, `SINK`, `HOSTEXEC`, as defined in `spikes/p0/PREREG_P1.md`. `HOSTEXEC` implies the other three.
- Free-text tool descriptions may only raise an effect, never remove one.
- Every label claim carries evidence as `path:line` or `path:start-end`; a claim whose evidence fails verification becomes `undetermined: true` and takes the least-restrictive default (the label stays).
- Model: `claude-opus-5`. Omit the `thinking` parameter (Opus 5 runs adaptive by default). Structured output via `output_config={"format": {"type": "json_schema", "schema": ...}}`.
- No secrets in any stored artifact: profiles hold code-derived facts only.
- Reuse, do not re-derive: `spikes/p0/data/surfaces.jsonl` already holds recovered tool surfaces for 35 packages, and `spikes/p0/adjudication/out/*.json` holds 17 hand-run adjudications used here as regression fixtures.

---

### Task 1: Profile schema

**Files:**
- Create: `interlock/__init__.py`, `interlock/profile.py`
- Create: `tests/test_profile.py`
- Create: `pyproject.toml`

**Interfaces:**
- Produces: `LABELS: frozenset[str]`; `expand(labels: set[str]) -> set[str]`; `ToolEffect(labels: list[str], evidence: list[str], rationale: str, default_enabled: bool = True, undetermined: bool = False)`; `Profile(package: str, version: str, kind: str, source: str, tools: dict[str, ToolEffect], value_conditions: list[dict], notes: list[str])`; `Profile.union(enabled_only: bool = True) -> set[str]`; `Profile.to_json() -> str`; `Profile.from_json(text: str) -> Profile`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_profile.py
import pytest
from interlock.profile import LABELS, Profile, ToolEffect, expand

def test_hostexec_expands_to_all_three():
    assert expand({"HOSTEXEC"}) == {"HOSTEXEC", "SECRET", "UNTRUSTED", "SINK"}
    assert expand({"SECRET"}) == {"SECRET"}

def test_union_skips_disabled_tools():
    p = Profile(package="x", version="1", kind="npm", source="npm pack", tools={
        "read": ToolEffect(labels=["SECRET"], evidence=["a.js:1"], rationale="reads files"),
        "run": ToolEffect(labels=["HOSTEXEC"], evidence=["a.js:2"], rationale="spawns", default_enabled=False),
    }, value_conditions=[], notes=[])
    assert p.union() == {"SECRET"}
    assert p.union(enabled_only=False) == {"SECRET", "UNTRUSTED", "SINK", "HOSTEXEC"}

def test_round_trip_json():
    p = Profile(package="x", version="1", kind="npm", source="npm pack", tools={
        "read": ToolEffect(labels=["SECRET"], evidence=["a.js:1"], rationale="reads files")}, value_conditions=[], notes=[])
    assert Profile.from_json(p.to_json()) == p

def test_rejects_unknown_label():
    with pytest.raises(ValueError):
        ToolEffect(labels=["NETWORK"], evidence=["a.js:1"], rationale="x").validate()

def test_labels_frozen():
    assert LABELS == frozenset({"SECRET", "UNTRUSTED", "SINK", "HOSTEXEC"})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_profile.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'interlock'`

- [ ] **Step 3: Write minimal implementation**

```python
# pyproject.toml
[project]
name = "interlock"
version = "0.1.0"
requires-python = ">=3.13"
dependencies = ["anthropic"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

```python
# interlock/profile.py
"""Provider effect profiles: what each advertised tool can reach, and why we believe it."""
from __future__ import annotations
import json
from dataclasses import dataclass, field, asdict

LABELS = frozenset({"SECRET", "UNTRUSTED", "SINK", "HOSTEXEC"})
ALL3 = frozenset({"SECRET", "UNTRUSTED", "SINK"})

def expand(labels: set[str]) -> set[str]:
    """HOSTEXEC reaches everything a host process can reach."""
    return set(labels) | ALL3 if "HOSTEXEC" in labels else set(labels)

@dataclass(eq=True)
class ToolEffect:
    labels: list[str]
    evidence: list[str]
    rationale: str
    default_enabled: bool = True
    undetermined: bool = False

    def validate(self) -> "ToolEffect":
        unknown = set(self.labels) - LABELS
        if unknown:
            raise ValueError(f"unknown labels: {sorted(unknown)}")
        return self

@dataclass(eq=True)
class Profile:
    package: str
    version: str
    kind: str
    source: str
    tools: dict[str, ToolEffect]
    value_conditions: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def union(self, enabled_only: bool = True) -> set[str]:
        out: set[str] = set()
        for t in self.tools.values():
            if enabled_only and not t.default_enabled:
                continue
            out |= expand(set(t.labels))
        return out

    def to_json(self) -> str:
        d = asdict(self)
        d["tools"] = {k: asdict(v) for k, v in self.tools.items()}
        return json.dumps(d, indent=1, sort_keys=True)

    @classmethod
    def from_json(cls, text: str) -> "Profile":
        d = json.loads(text)
        tools = {k: ToolEffect(**v).validate() for k, v in d.pop("tools").items()}
        return cls(tools=tools, **d)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_profile.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml interlock/__init__.py interlock/profile.py tests/test_profile.py
git commit -m "feat(profile): effect profile schema with HOSTEXEC expansion"
```

---

### Task 2: Source acquisition without execution

**Files:**
- Create: `interlock/source.py`
- Create: `tests/test_source.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `fetch_source(kind: str, package: str, version: str, cache_dir: Path, run=subprocess.run) -> Path` returning the directory holding the unpacked source; `source_digest(path: Path) -> str` (sha256 over sorted relative paths and file bytes).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_source.py
import tarfile, io, json
from pathlib import Path
import pytest
from interlock.source import fetch_source, source_digest

def fake_npm_runner(tmp_path):
    def run(cmd, **kw):
        assert "install" not in cmd, "must never install"
        if cmd[:2] == ["npm", "pack"]:
            tgz = Path(kw["cwd"]) / "pkg.tgz"
            with tarfile.open(tgz, "w:gz") as t:
                data = b"const x = 1;\n"
                info = tarfile.TarInfo("package/index.js"); info.size = len(data)
                t.addfile(info, io.BytesIO(data))
            class R: returncode = 0; stdout = "pkg.tgz\n"; stderr = ""
            return R()
        raise AssertionError(f"unexpected command {cmd}")
    return run

def test_fetch_npm_unpacks_without_installing(tmp_path):
    out = fetch_source("npm", "left-pad", "1.0.0", tmp_path, run=fake_npm_runner(tmp_path))
    assert (out / "index.js").read_text() == "const x = 1;\n"

def test_fetch_is_cached(tmp_path):
    calls = []
    runner = fake_npm_runner(tmp_path)
    def counting(cmd, **kw):
        calls.append(cmd); return runner(cmd, **kw)
    fetch_source("npm", "left-pad", "1.0.0", tmp_path, run=counting)
    fetch_source("npm", "left-pad", "1.0.0", tmp_path, run=counting)
    assert len(calls) == 1

def test_digest_is_content_addressed(tmp_path):
    a = tmp_path / "a"; a.mkdir(); (a / "f.js").write_text("x")
    b = tmp_path / "b"; b.mkdir(); (b / "f.js").write_text("x")
    c = tmp_path / "c"; c.mkdir(); (c / "f.js").write_text("y")
    assert source_digest(a) == source_digest(b) != source_digest(c)

def test_unknown_kind_raises(tmp_path):
    with pytest.raises(ValueError):
        fetch_source("cargo", "serde", "1.0", tmp_path)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_source.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'interlock.source'`

- [ ] **Step 3: Write minimal implementation**

```python
# interlock/source.py
"""Fetch provider source for reading only. Nothing here executes provider code."""
from __future__ import annotations
import hashlib, shutil, subprocess, tarfile, zipfile
from pathlib import Path

def _unpack(archive: Path, dest: Path) -> None:
    if archive.suffix in (".tgz", ".gz"):
        with tarfile.open(archive) as t:
            t.extractall(dest, filter="data")
    else:
        with zipfile.ZipFile(archive) as z:
            z.extractall(dest)

def fetch_source(kind: str, package: str, version: str, cache_dir: Path, run=subprocess.run) -> Path:
    """Return a directory holding the unpacked sources of package@version."""
    cache_dir = Path(cache_dir)
    slug = f"{kind}_{package.replace('/', '_').lstrip('@')}_{version}"
    dest = cache_dir / slug
    if dest.exists():
        return dest
    work = cache_dir / f".work_{slug}"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    if kind == "npm":
        run(["npm", "pack", f"{package}@{version}", "--silent"], cwd=str(work), capture_output=True, text=True, check=True)
    elif kind == "pypi":
        run(["pip", "download", "--no-deps", "--no-build-isolation", f"{package}=={version}", "-d", str(work)],
            capture_output=True, text=True, check=True)
    elif kind == "git":
        run(["git", "clone", "--depth", "1", "--branch", version, package, str(work / "repo")],
            capture_output=True, text=True, check=True)
    else:
        raise ValueError(f"unknown source kind: {kind}")
    unpacked = work / "unpacked"
    unpacked.mkdir()
    for archive in list(work.glob("*.tgz")) + list(work.glob("*.whl")) + list(work.glob("*.tar.gz")):
        _unpack(archive, unpacked)
    roots = [p for p in unpacked.iterdir()] if unpacked.exists() else []
    root = roots[0] if len(roots) == 1 and roots[0].is_dir() else (work / "repo" if (work / "repo").exists() else unpacked)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(root), str(dest))
    shutil.rmtree(work, ignore_errors=True)
    return dest

def source_digest(path: Path) -> str:
    h = hashlib.sha256()
    for f in sorted(Path(path).rglob("*")):
        if f.is_file():
            h.update(str(f.relative_to(path)).encode())
            h.update(f.read_bytes())
    return h.hexdigest()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_source.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add interlock/source.py tests/test_source.py
git commit -m "feat(source): fetch npm/pypi/git sources for reading, never installing"
```

---

### Task 3: Deterministic evidence checker

**Files:**
- Create: `interlock/evidence.py`
- Create: `tests/test_evidence.py`

**Interfaces:**
- Consumes: source trees produced by `fetch_source`.
- Produces: `parse_citation(text: str) -> list[tuple[str, int, int]]`; `check_citation(root: Path, citation: str, must_contain: list[str]) -> bool`; `check_tool(root: Path, effect: ToolEffect, tool_name: str) -> tuple[bool, str]` returning `(verified, reason)`.

Rule: a citation verifies when the file exists under `root`, the line span exists, and at least one token from `must_contain` appears within the span widened by two lines. The tokens are the tool name and the identifiers named in the rationale's backticks; if neither is present the claim is unverified.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_evidence.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_evidence.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'interlock.evidence'`

- [ ] **Step 3: Write minimal implementation**

```python
# interlock/evidence.py
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
    lo, hi = max(0, start - 1 - WIDEN), min(len(lines), end + WIDEN)
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_evidence.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add interlock/evidence.py tests/test_evidence.py
git commit -m "feat(evidence): deterministic re-check of file:line claims"
```

---

### Task 4: Advertised tool surfaces

**Files:**
- Create: `interlock/surface.py`
- Create: `tests/test_surface.py`
- Read (do not modify): `spikes/p0/data/surfaces.jsonl`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `load_surface(path: Path, package: str, version: str | None = None) -> dict` returning `{"package", "version", "kind", "tools": [ {name, description, inputSchema, annotations} ]}`; `latest_version(path: Path, package: str) -> str`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_surface.py
import json
import pytest
from interlock.surface import load_surface, latest_version

def fixture(tmp_path):
    p = tmp_path / "surfaces.jsonl"
    rows = [
        {"kind": "npm", "pkg": "a", "version": "1.0.0", "published": "2025-05-01T00:00:00Z", "ok": True,
         "tools": [{"name": "t1", "description": "d", "inputSchema": {"type": "object"}, "annotations": None}]},
        {"kind": "npm", "pkg": "a", "version": "2.0.0", "published": "2026-01-01T00:00:00Z", "ok": True,
         "tools": [{"name": "t2", "description": "d", "inputSchema": {"type": "object"}, "annotations": None}]},
        {"kind": "npm", "pkg": "a", "version": "3.0.0", "published": "2026-02-01T00:00:00Z", "ok": False, "tools": []},
    ]
    p.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return p

def test_latest_version_skips_failed_runs(tmp_path):
    assert latest_version(fixture(tmp_path), "a") == "2.0.0"

def test_load_specific_version(tmp_path):
    s = load_surface(fixture(tmp_path), "a", "1.0.0")
    assert [t["name"] for t in s["tools"]] == ["t1"] and s["kind"] == "npm"

def test_missing_package_raises(tmp_path):
    with pytest.raises(KeyError):
        load_surface(fixture(tmp_path), "nope")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_surface.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'interlock.surface'`

- [ ] **Step 3: Write minimal implementation**

```python
# interlock/surface.py
"""Advertised tool surfaces, as recovered by the Docker tools/list harness in spikes/p0."""
from __future__ import annotations
import json
from pathlib import Path

def _rows(path: Path, package: str) -> list[dict]:
    rows = [json.loads(l) for l in Path(path).open()]
    good = [r for r in rows if r["pkg"] == package and r.get("ok")]
    if not good:
        raise KeyError(f"no recovered surface for {package}")
    return sorted(good, key=lambda r: r["published"])

def latest_version(path: Path, package: str) -> str:
    return _rows(path, package)[-1]["version"]

def load_surface(path: Path, package: str, version: str | None = None) -> dict:
    rows = _rows(path, package)
    row = rows[-1] if version is None else next((r for r in rows if r["version"] == version), None)
    if row is None:
        raise KeyError(f"no recovered surface for {package}@{version}")
    return {"package": package, "version": row["version"], "kind": row["kind"], "tools": row["tools"]}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_surface.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add interlock/surface.py tests/test_surface.py
git commit -m "feat(surface): load recovered tool surfaces by package and version"
```

---

### Task 5: Adjudication request and client

**Files:**
- Create: `interlock/adjudicate.py`
- Create: `tests/test_adjudicate.py`
- Read (do not modify): `spikes/p0/PREREG_P1.md`

**Interfaces:**
- Consumes: `load_surface` (Task 4), `fetch_source` (Task 2).
- Produces: `RUBRIC: str`; `EFFECT_SCHEMA: dict`; `build_messages(surface: dict, source_files: list[tuple[str, str]]) -> list[dict]`; `adjudicate(surface, source_files, client, model="claude-opus-5") -> dict` returning the parsed JSON object `{"tools": {...}, "value_conditions": [...], "notes": [...]}`; `batch_request(custom_id, surface, source_files, model) -> Request`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_adjudicate.py
import json
from interlock.adjudicate import RUBRIC, EFFECT_SCHEMA, build_messages, adjudicate

SURFACE = {"package": "a", "version": "1", "kind": "npm",
           "tools": [{"name": "read_file", "description": "d", "inputSchema": {"type": "object"}, "annotations": None}]}
FILES = [("src/server.js", "fs.readFileSync(p)\n")]

def test_rubric_carries_label_definitions():
    for label in ("SECRET", "UNTRUSTED", "SINK", "HOSTEXEC"):
        assert label in RUBRIC
    assert "file:line" in RUBRIC

def test_schema_requires_evidence_per_tool():
    tool = EFFECT_SCHEMA["properties"]["tools"]["additionalProperties"]
    assert set(tool["required"]) >= {"labels", "evidence", "rationale", "default_enabled"}
    assert tool["additionalProperties"] is False

def test_build_messages_caches_stable_prefix_and_lists_every_tool():
    msgs = build_messages(SURFACE, FILES)
    blocks = msgs[0]["content"]
    assert blocks[0]["cache_control"] == {"type": "ephemeral"}      # source context is cached per package
    body = json.dumps(msgs)
    assert "read_file" in body and "src/server.js" in body

def test_adjudicate_parses_structured_output():
    class FakeClient:
        class messages:
            @staticmethod
            def create(**kw):
                assert kw["model"] == "claude-opus-5"
                assert kw["output_config"]["format"]["type"] == "json_schema"
                assert "thinking" not in kw
                class Block: type = "text"; text = json.dumps(
                    {"tools": {"read_file": {"labels": ["SECRET"], "evidence": ["src/server.js:1"],
                                             "rationale": "calls `readFileSync`", "default_enabled": True}},
                     "value_conditions": [], "notes": []})
                class R: content = [Block()]
                return R()
    out = adjudicate(SURFACE, FILES, client=FakeClient())
    assert out["tools"]["read_file"]["labels"] == ["SECRET"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_adjudicate.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'interlock.adjudicate'`

- [ ] **Step 3: Write minimal implementation**

```python
# interlock/adjudicate.py
"""Ask Claude to judge each tool's effects, with mandatory file:line evidence."""
from __future__ import annotations
import json
from pathlib import Path

RUBRIC = """You judge what an MCP server's advertised tools can reach, by reading its source code.

Threat model. The agent follows injected instructions and may pass any tool's output into any tool's input.
An attacker controls arbitrary web hosts and content, and content in multi-writer or third-party sources
(public issues and comments, community documentation or registries, search results). An attacker does not
control the user's local files or the data in the user's own accounts. An attacker can read hosts they
control and anything published publicly.

Labels.
SECRET: the tool returns non-public data into the agent's context - host files, databases or SaaS data
reached with the user's credentials, or content of file://, localhost or intranet URLs it can reach.
UNTRUSTED: the tool returns attacker-controllable content.
SINK: the tool can deliver agent-supplied data to an attacker-readable destination - a request to an
agent-chosen URL or host, a write to a public repository, issue, comment or gist, or a message to an
agent-chosen recipient. A query sent to a fixed vendor endpoint is not a SINK.
HOSTEXEC: arbitrary code execution on the host, including escapable sandboxes. Implies the other three.
Code confined to a browser page or a remote vendor sandbox is not HOSTEXEC; label its actual reach.

Rules.
Judge only from code. Every label must cite evidence as file:line or file:start-end, pointing at the line
that shows the behaviour. Cite identifiers you rely on in the rationale inside backticks.
A tool's free-text description may raise a label but never removes one.
If the code does not settle a question, set undetermined true, keep the least restrictive reading (that is,
keep the label), and say why in the rationale.
default_enabled is false when a tool is only advertised behind a flag, capability or toolset that is off by default.
value_conditions list the flags, environment variables, URL paths or headers that remove tools or labels,
with exact syntax, the default behaviour, and evidence."""

EFFECT_SCHEMA = {
    "type": "object",
    "properties": {
        "tools": {
            "type": "object",
            "additionalProperties": {
                "type": "object",
                "properties": {
                    "labels": {"type": "array", "items": {"type": "string", "enum": ["SECRET", "UNTRUSTED", "SINK", "HOSTEXEC"]}},
                    "evidence": {"type": "array", "items": {"type": "string"}},
                    "rationale": {"type": "string"},
                    "default_enabled": {"type": "boolean"},
                    "undetermined": {"type": "boolean"},
                },
                "required": ["labels", "evidence", "rationale", "default_enabled", "undetermined"],
                "additionalProperties": False,
            },
        },
        "value_conditions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"syntax": {"type": "string"}, "kind": {"type": "string"},
                               "effect": {"type": "string"}, "default": {"type": "string"}, "evidence": {"type": "string"}},
                "required": ["syntax", "kind", "effect", "default", "evidence"],
                "additionalProperties": False,
            },
        },
        "notes": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["tools", "value_conditions", "notes"],
    "additionalProperties": False,
}

def build_messages(surface: dict, source_files: list[tuple[str, str]]) -> list[dict]:
    code = "\n\n".join(f"===== {rel} =====\n{text}" for rel, text in source_files)
    tools = json.dumps(surface["tools"], indent=1)[:200_000]
    return [{"role": "user", "content": [
        {"type": "text",
         "text": f"Server {surface['package']}@{surface['version']} ({surface['kind']}). Source follows.\n\n{code}",
         "cache_control": {"type": "ephemeral"}},
        {"type": "text",
         "text": f"Advertised tools (judge every one of them):\n{tools}"},
    ]}]

def _params(surface, source_files, model):
    return dict(
        model=model,
        max_tokens=16000,
        system=[{"type": "text", "text": RUBRIC, "cache_control": {"type": "ephemeral"}}],
        messages=build_messages(surface, source_files),
        output_config={"format": {"type": "json_schema", "schema": EFFECT_SCHEMA}},
    )

def adjudicate(surface: dict, source_files: list[tuple[str, str]], client, model: str = "claude-opus-5") -> dict:
    response = client.messages.create(**_params(surface, source_files, model))
    text = next(b.text for b in response.content if b.type == "text")
    return json.loads(text)

def batch_request(custom_id: str, surface: dict, source_files: list[tuple[str, str]], model: str = "claude-opus-5"):
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request
    return Request(custom_id=custom_id, params=MessageCreateParamsNonStreaming(**_params(surface, source_files, model)))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_adjudicate.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add interlock/adjudicate.py tests/test_adjudicate.py
git commit -m "feat(adjudicate): evidence-carrying effect judgements via structured output"
```

---

### Task 6: Source selection for the prompt

**Files:**
- Create: `interlock/select.py`
- Create: `tests/test_select.py`

**Interfaces:**
- Consumes: source trees from `fetch_source`.
- Produces: `select_files(root: Path, tool_names: list[str], budget_bytes: int = 400_000) -> list[tuple[str, str]]` returning `(relative path, text)` pairs, ranked by how many tool names a file mentions, skipping vendored, minified, test and non-source files, truncating any single file to 120,000 characters.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_select.py
from pathlib import Path
from interlock.select import select_files

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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_select.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'interlock.select'`

- [ ] **Step 3: Write minimal implementation**

```python
# interlock/select.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_select.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add interlock/select.py tests/test_select.py
git commit -m "feat(select): rank source files by tool-name mentions under a byte budget"
```

---

### Task 7: Pipeline that verifies and writes profiles

**Files:**
- Create: `interlock/pipeline.py`
- Create: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `Profile`, `ToolEffect` (Task 1); `check_tool` (Task 3); `load_surface` (Task 4); `adjudicate` (Task 5); `select_files` (Task 6); `fetch_source` (Task 2).
- Produces: `verify(raw: dict, surface: dict, root: Path) -> tuple[Profile, dict]` returning the profile plus stats `{"tools": int, "claims": int, "verified": int, "demoted": int, "missing_tools": list[str]}`; `build_profile(package, version, kind, surfaces_path, cache_dir, client) -> tuple[Profile, dict]`.

Verification policy: a tool whose evidence fails the check keeps its labels but is marked `undetermined: true`; a tool in the surface that the model did not judge is added with `labels: ["HOSTEXEC"]`, `undetermined: true` (least restrictive reading, the one that cannot understate the effect).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_pipeline.py
from pathlib import Path
from interlock.pipeline import verify

SURFACE = {"package": "a", "version": "1", "kind": "npm", "tools": [
    {"name": "read_file", "description": "", "inputSchema": {}, "annotations": None},
    {"name": "post_note", "description": "", "inputSchema": {}, "annotations": None},
    {"name": "forgotten", "description": "", "inputSchema": {}, "annotations": None}]}

RAW = {"tools": {
    "read_file": {"labels": ["SECRET"], "evidence": ["src/s.js:1"], "rationale": "calls `readFileSync`",
                  "default_enabled": True, "undetermined": False},
    "post_note": {"labels": ["SINK"], "evidence": ["src/s.js:9"], "rationale": "posts to `fetch`",
                  "default_enabled": True, "undetermined": False}},
    "value_conditions": [], "notes": []}

def tree(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "s.js").write_text("fs.readFileSync(p)\n")
    return tmp_path

def test_verified_claim_stays_determined(tmp_path):
    profile, stats = verify(RAW, SURFACE, tree(tmp_path))
    assert profile.tools["read_file"].undetermined is False
    assert stats["verified"] == 1 and stats["claims"] == 2

def test_unverifiable_claim_is_demoted_but_keeps_labels(tmp_path):
    profile, stats = verify(RAW, SURFACE, tree(tmp_path))
    assert profile.tools["post_note"].undetermined is True
    assert profile.tools["post_note"].labels == ["SINK"]
    assert stats["demoted"] == 1

def test_unjudged_tool_takes_the_least_restrictive_reading(tmp_path):
    profile, stats = verify(RAW, SURFACE, tree(tmp_path))
    assert profile.tools["forgotten"].labels == ["HOSTEXEC"]
    assert profile.tools["forgotten"].undetermined is True
    assert stats["missing_tools"] == ["forgotten"]

def test_profile_union_reflects_demotions(tmp_path):
    profile, _ = verify(RAW, SURFACE, tree(tmp_path))
    assert {"SECRET", "SINK", "UNTRUSTED", "HOSTEXEC"} == profile.union()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_pipeline.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'interlock.pipeline'`

- [ ] **Step 3: Write minimal implementation**

```python
# interlock/pipeline.py
"""Turn a model judgement into a profile only the checked parts of which are trusted."""
from __future__ import annotations
from pathlib import Path
from interlock.adjudicate import adjudicate
from interlock.evidence import check_tool
from interlock.profile import Profile, ToolEffect
from interlock.select import select_files
from interlock.source import fetch_source
from interlock.surface import load_surface

def verify(raw: dict, surface: dict, root: Path) -> tuple[Profile, dict]:
    tools: dict[str, ToolEffect] = {}
    stats = {"tools": len(surface["tools"]), "claims": 0, "verified": 0, "demoted": 0, "missing_tools": []}
    judged = raw.get("tools", {})
    for t in surface["tools"]:
        name = t["name"]
        j = judged.get(name)
        if j is None:
            stats["missing_tools"].append(name)
            tools[name] = ToolEffect(labels=["HOSTEXEC"], evidence=[], rationale="not judged; least restrictive reading",
                                     default_enabled=True, undetermined=True).validate()
            continue
        effect = ToolEffect(labels=j["labels"], evidence=j["evidence"], rationale=j["rationale"],
                            default_enabled=j.get("default_enabled", True),
                            undetermined=bool(j.get("undetermined"))).validate()
        if effect.labels:
            stats["claims"] += 1
            ok, reason = check_tool(root, effect, name)
            if ok:
                stats["verified"] += 1
            else:
                stats["demoted"] += 1
                effect.undetermined = True
                effect.rationale = f"{effect.rationale} [unverified: {reason}]"
        tools[name] = effect
    profile = Profile(package=surface["package"], version=surface["version"], kind=surface["kind"],
                      source=str(root), tools=tools, value_conditions=raw.get("value_conditions", []),
                      notes=raw.get("notes", []))
    return profile, stats

def build_profile(package: str, version: str | None, kind: str, surfaces_path: Path, cache_dir: Path, client,
                  source_ref: str | None = None) -> tuple[Profile, dict]:
    surface = load_surface(surfaces_path, package, version)
    root = fetch_source(kind, source_ref or package, surface["version"], cache_dir)
    files = select_files(root, [t["name"] for t in surface["tools"]])
    raw = adjudicate(surface, files, client=client)
    return verify(raw, surface, root)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_pipeline.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add interlock/pipeline.py tests/test_pipeline.py
git commit -m "feat(pipeline): demote unverified claims and fill unjudged tools conservatively"
```

---

### Task 8: Regression against the hand-run adjudications

**Files:**
- Create: `tests/test_regression_p1.py`
- Read (do not modify): `spikes/p0/adjudication/out/*.json`, `spikes/p0/data/surfaces.jsonl`

**Interfaces:**
- Consumes: `Profile.from_json`-shaped data in the P1 outputs, `verify` (Task 7).

This task pins the behaviour the P1 hand run established, so later prompt or checker changes cannot silently drift: the pipeline's verdict for a package must keep the label union that P1 recorded for the packages whose union is not in dispute.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_regression_p1.py
import json
from pathlib import Path
import pytest
from interlock.profile import expand

P1 = Path("spikes/p0/adjudication/out")
EXPECTED_UNION = {
    "playwright_mcp": {"HOSTEXEC", "SECRET", "UNTRUSTED", "SINK"},
    "modelcontextprotocol_server-filesystem": {"SECRET"},
    "modelcontextprotocol_server-sequential-thinking": set(),
    "mcp-server-fetch": {"SECRET", "UNTRUSTED", "SINK"},
    "modelcontextprotocol_server-brave-search": {"UNTRUSTED"},
}

@pytest.mark.parametrize("name,expected", sorted(EXPECTED_UNION.items()))
def test_p1_union_is_pinned(name, expected):
    d = json.loads((P1 / f"{name}.json").read_text())
    union = set()
    for t in d["tools"].values():
        if t.get("default_enabled", True):
            union |= expand(set(t["labels"]))
    assert union == expected
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_regression_p1.py -v`
Expected: FAIL only if a P1 file is missing or its union changed; run it now to confirm the fixtures are present and the expectations match.

- [ ] **Step 3: Fix whichever side is wrong**

If a fixture is missing, correct the file name. If the union differs, do not edit the fixture: record the difference in `docs/results/` and raise it before continuing, because the P1 numbers in the paper depend on it.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_regression_p1.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add tests/test_regression_p1.py
git commit -m "test: pin the P1 label unions as a regression fixture"
```

---

### Task 9: CLI

**Files:**
- Create: `interlock/cli.py`
- Create: `tests/test_cli.py`

**Interfaces:**
- Consumes: `build_profile` (Task 7).
- Produces: `main(argv: list[str]) -> int` with subcommands `profile` (one package) and `batch` (many packages, via the Message Batches API), writing `profiles/<kind>_<package>_<version>.json` and appending one row per package to `profiles/stats.jsonl`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cli.py
import json
from pathlib import Path
from interlock.cli import main

def test_profile_subcommand_writes_profile_and_stats(tmp_path, monkeypatch):
    surfaces = tmp_path / "surfaces.jsonl"
    surfaces.write_text(json.dumps({"kind": "npm", "pkg": "a", "version": "1.0.0", "published": "2026-01-01T00:00:00Z",
                                    "ok": True, "tools": [{"name": "read_file", "description": "", "inputSchema": {}, "annotations": None}]}) + "\n")
    src = tmp_path / "cache" / "npm_a_1.0.0" / "src"
    src.mkdir(parents=True)
    (src / "s.js").write_text("fs.readFileSync(p)\n")

    class FakeClient:
        class messages:
            @staticmethod
            def create(**kw):
                class Block: type = "text"; text = json.dumps({"tools": {"read_file": {
                    "labels": ["SECRET"], "evidence": ["src/s.js:1"], "rationale": "calls `readFileSync`",
                    "default_enabled": True, "undetermined": False}}, "value_conditions": [], "notes": []})
                class R: content = [Block()]
                return R()
    monkeypatch.setattr("interlock.cli.make_client", lambda: FakeClient())

    rc = main(["profile", "npm:a", "--surfaces", str(surfaces), "--cache", str(tmp_path / "cache"), "--out", str(tmp_path / "profiles")])
    assert rc == 0
    written = json.loads((tmp_path / "profiles" / "npm_a_1.0.0.json").read_text())
    assert written["tools"]["read_file"]["labels"] == ["SECRET"]
    stats = [json.loads(l) for l in (tmp_path / "profiles" / "stats.jsonl").open()]
    assert stats[0]["verified"] == 1 and stats[0]["package"] == "a"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_cli.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'interlock.cli'`

- [ ] **Step 3: Write minimal implementation**

```python
# interlock/cli.py
"""interlock profile npm:@scope/pkg  |  interlock batch packages.txt"""
from __future__ import annotations
import argparse, json, sys, time
from pathlib import Path
from interlock.adjudicate import batch_request
from interlock.pipeline import build_profile, verify
from interlock.select import select_files
from interlock.source import fetch_source
from interlock.surface import load_surface

def make_client():
    import anthropic
    return anthropic.Anthropic()

def _split(spec: str) -> tuple[str, str]:
    kind, _, package = spec.partition(":")
    return kind, package

def _write(out_dir: Path, profile, stats) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    slug = f"{profile.kind}_{profile.package.replace('/', '_').lstrip('@')}_{profile.version}"
    (out_dir / f"{slug}.json").write_text(profile.to_json())
    with (out_dir / "stats.jsonl").open("a") as f:
        f.write(json.dumps({"package": profile.package, "version": profile.version, **stats}) + "\n")

def cmd_profile(args) -> int:
    kind, package = _split(args.package)
    profile, stats = build_profile(package, args.version, kind, Path(args.surfaces), Path(args.cache),
                                   client=make_client(), source_ref=args.source_ref)
    _write(Path(args.out), profile, stats)
    print(f"{package}@{profile.version}: {stats['verified']}/{stats['claims']} claims verified, "
          f"{stats['demoted']} demoted, {len(stats['missing_tools'])} tools unjudged")
    return 0

def cmd_batch(args) -> int:
    client = make_client()
    specs = [l.strip() for l in Path(args.packages).read_text().splitlines() if l.strip() and not l.startswith("#")]
    prepared = {}
    requests = []
    for spec in specs:
        kind, package = _split(spec)
        surface = load_surface(Path(args.surfaces), package)
        root = fetch_source(kind, package, surface["version"], Path(args.cache))
        files = select_files(root, [t["name"] for t in surface["tools"]])
        cid = f"{kind}_{package.replace('/', '_').lstrip('@')}"[:64]
        prepared[cid] = (surface, root)
        requests.append(batch_request(cid, surface, files))
    batch = client.messages.batches.create(requests=requests)
    print(f"batch {batch.id} with {len(requests)} requests")
    while client.messages.batches.retrieve(batch.id).processing_status != "ended":
        time.sleep(30)
    for result in client.messages.batches.results(batch.id):
        surface, root = prepared[result.custom_id]
        if result.result.type != "succeeded":
            print(f"{result.custom_id}: {result.result.type}", file=sys.stderr)
            continue
        text = next(b.text for b in result.result.message.content if b.type == "text")
        profile, stats = verify(json.loads(text), surface, root)
        _write(Path(args.out), profile, stats)
        print(f"{profile.package}@{profile.version}: {stats['verified']}/{stats['claims']} verified")
    return 0

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="interlock")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("profile", "batch"):
        s = sub.add_parser(name)
        s.add_argument("package" if name == "profile" else "packages")
        s.add_argument("--surfaces", default="spikes/p0/data/surfaces.jsonl")
        s.add_argument("--cache", default=".cache/sources")
        s.add_argument("--out", default="profiles")
        if name == "profile":
            s.add_argument("--version", default=None)
            s.add_argument("--source-ref", default=None, help="git URL when kind is git")
    args = p.parse_args(argv)
    return cmd_profile(args) if args.cmd == "profile" else cmd_batch(args)

if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_cli.py -v`
Expected: PASS (1 test)

- [ ] **Step 5: Commit**

```bash
git add interlock/cli.py tests/test_cli.py
git commit -m "feat(cli): profile one package or a batch of them"
```

---

### Task 10: Pre-register Gate A

**Files:**
- Create: `docs/prereg/2026-09-16-PREREG_A.md`

This task runs **before** any at-scale measurement. It fixes the numbers that decide the branch, so the result cannot be read backwards into the criteria.

- [ ] **Step 1: Write the pre-registration**

```markdown
# Gate A pre-registration (fixed before any at-scale profiling run)

## Measurement
Profile the packages listed in `docs/prereg/2026-09-16-gate-a-packages.txt` (the servers needed for the
Phase D evaluation set, drawn from the corpus by configuration frequency). For each package record, from
`profiles/stats.jsonl`: tools, claims, verified, demoted, missing_tools, wall-clock time and API cost.

## Definitions
- verified-claim rate = verified / claims, pooled over all packages.
- unjudged-tool rate = sum(len(missing_tools)) / sum(tools).
- A package is *usable* when its verified-claim rate is >= 0.70 and its unjudged-tool rate is <= 0.05.

## Criteria (fixed now)
- A1 continue as planned: pooled verified-claim rate >= 0.80 and >= 80% of packages usable.
- A2 narrow: pooled verified-claim rate in [0.60, 0.80) or 50-80% of packages usable. Restrict Phase B-E to the
  usable packages and shrink the evaluation set accordingly; report the restriction as a threat to validity.
- A3 drop the technique: pooled verified-claim rate < 0.60, or fewer than half the packages usable, or the
  measured cost exceeds USD 300 for the listed packages. The paper becomes measurement-only (claims C1 and C2)
  and Phases C-E are cancelled.

## Recording
Write `docs/results/<date>-gate-a.md` with the numbers, the branch taken, and the rewritten plan for the next
phase. Do not carry this plan forward unchanged if the branch is A2 or A3.
```

- [ ] **Step 2: Commit**

```bash
git add docs/prereg/2026-09-16-PREREG_A.md
git commit -m "docs: pre-register Gate A criteria"
```

---

### Task 11: Run Phase A and record the gate

**Files:**
- Create: `docs/prereg/2026-09-16-gate-a-packages.txt`
- Create: `docs/results/<date>-gate-a.md`

- [ ] **Step 1: Build the package list from the corpus**

```bash
python3 - <<'EOF' > docs/prereg/2026-09-16-gate-a-packages.txt
import json, collections
cs = [json.loads(l) for l in open("spikes/p0/data/configs.jsonl")]
cs = [c for c in cs if not c.get("fork")]
freq = collections.Counter((s["kind"], s["id"]) for c in cs
                           for s in {(x["kind"], x["id"]): x for x in c["servers"]}.values()
                           if s["kind"] in ("npm", "pypi"))
have = {json.loads(l)["pkg"] for l in open("spikes/p0/data/surfaces.jsonl") if json.loads(l)["ok"]}
for (kind, pkg), _ in freq.most_common(200):
    if pkg in have:
        print(f"{kind}:{pkg}")
EOF
wc -l docs/prereg/2026-09-16-gate-a-packages.txt
```

- [ ] **Step 2: Dry-run one package end to end**

Run: `python -m interlock.cli profile npm:@modelcontextprotocol/server-filesystem`
Expected: a `profiles/npm_modelcontextprotocol_server-filesystem_*.json` file, and a printed line reporting verified/claims. Compare its label union with the P1 fixture for the same package; investigate any difference before continuing.

- [ ] **Step 3: Run the batch**

Run: `python -m interlock.cli batch docs/prereg/2026-09-16-gate-a-packages.txt`
Expected: one profile per package and one row per package in `profiles/stats.jsonl`.

- [ ] **Step 4: Compute the gate numbers**

```bash
python3 - <<'EOF'
import json
rows = [json.loads(l) for l in open("profiles/stats.jsonl")]
claims = sum(r["claims"] for r in rows); verified = sum(r["verified"] for r in rows)
tools = sum(r["tools"] for r in rows); missing = sum(len(r["missing_tools"]) for r in rows)
usable = [r for r in rows if r["claims"] and r["verified"] / r["claims"] >= 0.70
          and len(r["missing_tools"]) / max(1, r["tools"]) <= 0.05]
print(f"packages {len(rows)}; pooled verified-claim rate {verified/max(1,claims):.3f}; "
      f"unjudged-tool rate {missing/max(1,tools):.3f}; usable {len(usable)}/{len(rows)}")
EOF
```

- [ ] **Step 5: Record the gate and rewrite the plan**

Write `docs/results/<date>-gate-a.md` with the numbers, the branch (A1, A2 or A3), the measured API cost, and the next phase's plan as revised by that branch. Then commit:

```bash
git add docs/prereg/2026-09-16-gate-a-packages.txt docs/results/ profiles/
git commit -m "docs: Phase A results and Gate A decision"
```

---

## Self-Review

- **Spec coverage.** Phase A of the spec asks for adjudication at scale (Tasks 5-9, 11), the evidence checker (Task 3), and the measurement of failed verification (Tasks 7, 10, 11). The trust-ranking rule from spec §5 is carried by the rubric in Task 5 and by the demotion policy in Task 7. Gate A's three branches are pre-registered in Task 10. Phases B-E are out of scope here by design.
- **Placeholders.** None: every step names a file, shows the code, and gives the command with its expected result. The only deliberately deferred content is the package list, which Task 11 Step 1 generates from the corpus, and the results file, whose numbers cannot exist before the run.
- **Type consistency.** `ToolEffect` and `Profile` field names are used identically in Tasks 1, 3, 7, 8 and 9; `verify` returns `(Profile, stats)` in Task 7 and is consumed that way in Tasks 9 and 11; `load_surface` returns the same dict shape in Tasks 4, 5 and 7; `select_files` returns `(rel, text)` pairs in Task 6 and is consumed that way in Tasks 5 and 7.
