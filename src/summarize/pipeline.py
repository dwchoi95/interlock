"""Turn a model judgement into a profile only the checked parts of which are trusted."""
from __future__ import annotations
import hashlib, json, os, re, shutil, subprocess
from pathlib import Path
from src.summarize.adjudicate import (EFFECT_SCHEMA, INVENTORY_KEYS, SEED, adjudicate, apply_derivation, classify_statements,
                                      complete_destinations, derive, parts_of, rubric, vendor_write)
from src.summarize.evidence import (_resolve_path, candidate_elements, check_tool, classify_evidence, exec_where, guarded_blocks,
                                    handler_block, method_chain, parse_citation, quote_blocks, resolve_destination)
from src.summarize.profile import ENFORCEMENT_FIELDS, Profile, ToolEffect
from src.summarize.select import is_candidate, select_files
from src.summarize.source import cache_path, fetch_dependencies, fetch_source, slugify
from src.summarize.surface import load_surface


def check_enforcement_fields(effect: ToolEffect, advertised: dict, stats: dict) -> None:
    """Deterministic checks on the enforcement fields, against the advertised surface. An
    argument name the input schema does not declare cannot be a destination or a value of
    this tool, so it is dropped and counted rather than passed on to a guard that would
    then look for an argument that never arrives; an attacker-named field has to be one of
    the identifier fields. Nothing here reads the code: that is the citation check's job."""
    declared = set(((advertised.get("inputSchema") or {}).get("properties") or {}))
    # The argument the delivery question names is a destination argument by definition (the party the data goes
    # to); it completes destination_args when the model left it out or filed it as a value.
    d = effect.delivers_to_chosen_party or {}
    arg = str(d.get("argument", "")).strip()
    if d.get("value") is True and arg in declared:
        if arg not in effect.destination_args:
            effect.destination_args = list(effect.destination_args) + [arg]
            stats["destination_completed"] = stats.get("destination_completed", 0) + 1
        effect.value_args = [v for v in effect.value_args if v != arg]
    for f in ("destination_args", "value_args"):
        names = list(getattr(effect, f))
        bad = [a for a in names if a not in declared]
        if bad:
            stats["arg_name_errors"] += len(bad)
            setattr(effect, f, [a for a in names if a in declared])
            effect.rationale = f"{effect.rationale} [dropped {f} not in the input schema: {', '.join(bad)}]"
    idents = set(effect.identifier_output_fields)
    bad = [x for x in effect.attacker_named_fields if x not in idents]
    if bad:
        stats["named_field_errors"] += len(bad)
        effect.attacker_named_fields = [x for x in effect.attacker_named_fields if x in idents]
    if effect.kind == "WRITE":
        stats["kind_write"] += 1
    if effect.destination_args:
        stats["with_destination_args"] += 1


def verify(raw: dict, surface: dict, root: Path) -> tuple[Profile, dict]:
    root = Path(root)
    if not root.is_dir():
        # A missing root (a stale batch manifest, a cache wiped mid-run, a resume from
        # elsewhere) must never be silently read as "nothing verifies" - that would demote
        # every claim and mark every clear unverified with no error anywhere in the record.
        raise ValueError(f"{surface['package']}@{surface['version']}: source root does not exist: {root}")
    tools: dict[str, ToolEffect] = {}
    stats = {"tools": len(surface["tools"]), "claims": 0, "verified": 0, "verified_doc": 0, "demoted": 0,
             "cleared_verified": 0, "cleared_unverified": 0, "missing_tools": [], "unknown_tools": [],
             "kind_write": 0, "with_destination_args": 0, "arg_name_errors": 0, "named_field_errors": 0}
    judged = raw.get("tools", {})
    surface_names = {t["name"] for t in surface["tools"]}
    stats["unknown_tools"] = sorted(set(judged) - surface_names)
    for t in surface["tools"]:
        name = t["name"]
        j = judged.get(name)
        if j is None:
            stats["missing_tools"].append(name)
            # Most conservative summary: a WRITE whose every argument is a destination and whose whole
            # return an outside party may have written, so Narrow gates it and Guard trusts nothing it returns.
            tools[name] = ToolEffect(labels=["HOSTEXEC"], evidence=[], rationale="not judged; every effect assumed",
                                     default_enabled=True, undetermined=True, kind="WRITE",
                                     destination_args=sorted((t.get("inputSchema") or {}).get("properties") or {}),
                                     injectable_output_fields=["return"])
            continue
        try:
            # Copy labels/evidence: they must not alias the caller's raw lists.
            effect = ToolEffect(labels=list(j["labels"]), evidence=list(j["evidence"]), rationale=j["rationale"],
                                default_enabled=j.get("default_enabled", True),
                                undetermined=bool(j.get("undetermined")), kind=j.get("kind"),
                                result_contents=[dict(x) for x in (j.get("result_contents") or []) if isinstance(x, dict)],
                                outbound_calls=[dict(x) for x in (j.get("outbound_calls") or []) if isinstance(x, dict)],
                                runs_supplied_code=dict(j.get("runs_supplied_code") or {}),
                                coverage_gaps=list(j.get("coverage_gaps") or []),
                                delivers_to_chosen_party=dict(j.get("delivers_to_chosen_party") or {}),
                                **{f: list(j.get(f) or []) for f in ENFORCEMENT_FIELDS})
        except ValueError as e:
            raise ValueError(f"{surface['package']}@{surface['version']}: tool {name!r}: {e}") from e
        check_enforcement_fields(effect, t, stats)
        check_outbound(root, effect, stats)
        judged_kind = {"kind": effect.kind, "outbound_calls": effect.outbound_calls, "rationale": effect.rationale}
        if vendor_write(judged_kind):   # with the destinations resolved from the code
            effect.kind, effect.rationale = judged_kind["kind"], judged_kind["rationale"]
            stats["kind_vendor_write"] = stats.get("kind_vendor_write", 0) + 1
        check_exec(root, effect, stats)
        if effect.coverage_gaps:
            stats["coverage_gaps"] = stats.get("coverage_gaps", 0) + len(effect.coverage_gaps)
            effect.undetermined = True
            effect.rationale = f"{effect.rationale} [coverage: {len(effect.coverage_gaps)} candidate(s) unanswered]"
        # Evidence is checked whether the model asserted labels or cleared the tool outright:
        # a clearance is itself a judgement, and an unverifiable one must not pass silently.
        # Documentation may raise an effect but never lower one, so a citation that only
        # verifies against a README/CHANGELOG/etc counts separately from verified code,
        # and can never justify clearing a tool.
        ok, reason, evidence_class = check_tool(root, effect, name)
        if effect.labels:
            stats["claims"] += 1
            if ok and evidence_class == "code":
                stats["verified"] += 1
            elif ok and evidence_class == "doc":
                stats["verified_doc"] += 1
                effect.undetermined = True
                effect.rationale = f"{effect.rationale} [evidence: documentation only]"
            else:
                stats["demoted"] += 1
                effect.undetermined = True
                effect.rationale = f"{effect.rationale} [unverified: {reason}]"
        else:
            if ok and evidence_class == "code":
                stats["cleared_verified"] += 1
            else:
                stats["cleared_unverified"] += 1
                effect.undetermined = True
                clear_reason = reason if not ok else "documentation cannot verify a clearance"
                effect.rationale = f"{effect.rationale} [unverified: {clear_reason}]"
        tools[name] = effect
    profile = Profile(package=surface["package"], version=surface["version"], kind=surface["kind"],
                      source=str(root), tools=tools, value_conditions=raw.get("value_conditions", []),
                      notes=raw.get("notes", []))
    return profile, stats


# A statement that calls a method on the response/result/context object. Nothing here names a server, tool,
# method or field; the object names are the conventional ones of MCP servers.
RESPONSE_CALL = re.compile(r"\b(?:response|res|resp|result|reply|ctx|context)\.(\w+)\(")
RESPONSE_CALL_ARGS = re.compile(r"\b(?:response|res|resp|result|reply|ctx|context)\.(\w+)\(([^)]*)\)")
FALSY = {"false", "False", "0", "null", "None", "undefined", "''", '""', "``"}


def receiver_methods(root: Path, files: list[tuple[str, str]]) -> dict[str, dict]:
    """method -> {defined_at, file, blocks, quotes, candidates} for every method the shown code files call on
    their response objects that a shown file defines: the definition line, the blocks guarded by the field it
    sets (evidence.method_chain, guarded_blocks) and the candidate elements inside them."""
    shown = {rel for rel, _ in files}
    called: set[str] = set()
    for rel, text in files:
        if classify_evidence(rel) != "doc":
            called |= set(RESPONSE_CALL.findall(text))
    out: dict[str, dict] = {}
    for m in sorted(called):
        chain = method_chain(root, m)
        if chain is None:
            continue
        drel, dl, reads = chain
        if drel not in shown:
            continue
        blocks = guarded_blocks(root, drel, reads)
        out[m] = {"defined_at": f"{drel}:{dl}", "blocks": [f"{drel}:{a}-{b}" for a, b in blocks],
                  "quotes": quote_blocks(root, drel, blocks), "file": drel, "candidates": candidate_elements(root, drel, blocks)}
    return out


def judge_methods(root: Path, files, surface: dict, client, model: str, usage_out, temperature,
                  log: list | None = None) -> dict[str, dict]:
    """The shared response methods, judged once per server: method -> {parts, gaps}; {} when no shown file
    defines any. A method without candidates writes nothing."""
    spec = receiver_methods(root, files)
    out: dict[str, dict] = {}
    for m, v in spec.items():
        if not v["candidates"]:
            out[m] = {"parts": [], "gaps": [], "spec": {k: v[k] for k in ("defined_at", "blocks")}}
            continue
        judged, gaps = classify_statements(surface["package"], f"response method `{m}`, defined at {v['defined_at']}; the "
                                           f"blocks guarded by the field it sets are shown",
                                           "This is the class that formats what the server returns to the agent. The objects it "
                                           "reads (`this`, `context`, the pages, tabs, files, records or sessions they hold) are the "
                                           "state the server keeps for the user: a page the user has open, a file the user has, a record "
                                           "in the user's account, so their names, titles, URLs and metadata have the user's scope "
                                           "(user_local for the machine and the browser the server drives, user_account for a service), "
                                           "while the text a site or another party wrote inside them is outside_party.",
                                           v["quotes"], v["candidates"], client, model, usage_out, temperature, log)
        out[m] = {"parts": parts_of(judged), "gaps": gaps, "spec": {k: v[k] for k in ("defined_at", "blocks")}}
    return out


HANDLER_QUOTE_LINES = 150
BLOCK_EXPAND = 80


def _merge_ranges(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for a, b in sorted(ranges):
        if out and a <= out[-1][1] + 1:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def _expanded(root: Path, rel: str, s: int, e: int) -> tuple[int, int]:
    """A citation of one or two lines that open a block (a handler's head, a function's first line) is read as
    that block, at most BLOCK_EXPAND lines, so that the judge's habit of citing the head line alone does not
    leave the statements of the handler out."""
    if e - s >= 2:
        return s, e
    f = _resolve_path(root, rel)
    if f is None:
        return s, e
    lines = f.read_text(errors="replace").splitlines()
    if s > len(lines):
        return s, e
    head = lines[s - 1].rstrip()
    if head.endswith(("{", "(", "=>", ":", "[", ",")) or re.search(r"\b(?:def|function|async|const|handler|=>)\b", head):
        from src.summarize.evidence import _block_end
        return s, max(e, min(_block_end(lines, s), s + BLOCK_EXPAND - 1, len(lines)))
    return s, e


def handler_span(root: Path, rel: str, name: str) -> tuple[int, int] | None:
    """The tool's handler block, fixed from the code (evidence.handler_block)."""
    return handler_block(root, rel, name)


def _tool_spans(raw: dict, surface: dict, root: Path) -> dict[str, list[tuple[str, int, int]]]:
    """Per tool, the code spans to read: the handler span fixed from the tool's defining file (the file whose
    citation names the tool in a defining shape, or any cited file that does) and the model's citations,
    head-line citations expanded to their block."""
    from src.summarize.select import _defines
    spans: dict[str, list[tuple[str, int, int]]] = {}
    for name, t in raw.get("tools", {}).items():
        if not any(k in t for k in INVENTORY_KEYS):
            continue   # a judgment without inventories (an older shape) keeps its labels
        own: list[tuple[str, int, int]] = []
        for c in t.get("evidence") or []:
            for rel, s, e in parse_citation(c):
                if _resolve_path(root, rel) is None or classify_evidence(rel) == "doc":
                    continue
                s, e = _expanded(root, rel, s, e)
                own.append((rel, s, e))
        for rel in dict.fromkeys(r for r, _s, _e in own):
            f = _resolve_path(root, rel)
            if f is not None and _defines(f.read_text(errors="replace"), name):
                hs = handler_span(root, rel, name)
                if hs:
                    own.append((rel, hs[0], hs[1]))
                    cite = f"{rel}:{hs[0]}-{hs[1]}"
                    if cite not in (t.get("evidence") or []):
                        t.setdefault("evidence", []).append(cite)   # the pipeline cites the handler block it read
                break
        spans[name] = own
    return spans


def classify_handlers(raw: dict, surface: dict, root: Path, client, model: str, usage_out, temperature,
                      log: list | None = None) -> dict:
    """Each tool's own result parts, re-read candidate by candidate. Per file, the union of the spans every tool
    reads is cut into pieces of at most HANDLER_QUOTE_LINES lines; the candidate elements inside each piece are
    classified by id, once per server; a tool receives the judgments of the ids inside its spans. The model's own
    parts on a line are replaced only when that line's candidates were judged; an id the model did not answer is
    recorded in the tool's coverage_gaps, not treated as an absence."""
    desc = {t["name"]: str(t.get("description") or "")[:300] for t in surface.get("tools", [])}
    spans_of = _tool_spans(raw, surface, root)
    by_file: dict[str, list[tuple[int, int]]] = {}
    citing: dict[str, set[str]] = {}
    for name, spans in spans_of.items():
        for rel, s, e in spans:
            by_file.setdefault(rel, []).append((s, e)); citing.setdefault(rel, set()).add(name)
    judged_all: dict[str, dict] = {}
    gaps_all: set[str] = set()
    for rel, ranges in by_file.items():
        pieces: list[tuple[int, int]] = []
        for a, b in _merge_ranges(ranges):
            while b - a + 1 > HANDLER_QUOTE_LINES:   # a long range is cut, never truncated
                pieces.append((a, a + HANDLER_QUOTE_LINES - 1)); a += HANDLER_QUOTE_LINES
            pieces.append((a, b))
        names = sorted(citing[rel])
        about = "Tools that this code serves: " + "; ".join(f"{n}: {desc[n]}" if desc.get(n) else n for n in names[:4])
        for a, b in pieces:
            cands = candidate_elements(root, rel, [(a, b)])
            if not cands:
                continue
            quotes = quote_blocks(root, rel, [(a, b)], cap=HANDLER_QUOTE_LINES)
            judged, gaps = classify_statements(surface["package"], f"handler lines {rel}:{a}-{b}", about, quotes, cands,
                                               client, model, usage_out, temperature, log)
            judged_all.update(judged); gaps_all.update(gaps)
    for name, spans in spans_of.items():
        t = raw["tools"][name]
        if not spans:
            continue
        inside = lambda pid, rel, s, e: pid.startswith(rel + ":") and s <= int(pid.rsplit(":", 1)[1].split("#")[0]) <= e
        mine = [j for pid, j in judged_all.items() if any(inside(pid, rel, s, e) for rel, s, e in spans)]
        gaps = sorted(g for g in gaps_all if any(inside(g, rel, s, e) for rel, s, e in spans))
        # The classifier's statement parts are added to the batch judge's own parts, not put in their place: the
        # batch judge read the handler with the description and the threat model in view (it knows a mailbox
        # holds other people's mail), the classifier read the statements (it knows which line writes what), and
        # the labels are the union of what either reading found, the least restrictive reading.
        own = [x for x in (t.get("result_contents") or []) if isinstance(x, dict)]
        seen = {(str(x.get("produced_at")), str(x.get("part")), str(x.get("scope")), str(x.get("control"))) for x in own}
        added = []
        for j in mine:
            if j.get("origin") == "not_output":
                continue
            key = (j["produced_at"], j["part"], j["scope"], j["control"])
            if key not in seen:
                seen.add(key); added.append({k: v for k, v in j.items() if k not in ("gate", "value_from_argument")})
        t["result_contents"] = own + added
        t["coverage_gaps"] = gaps
    return raw


def _call_condition(arg_text: str) -> str | None:
    """"off" when the call passes a falsy literal, "on" for a truthy literal or no argument, otherwise the
    argument expression (a flag the caller passes), preserved as the condition of the inherited effect."""
    a = arg_text.strip()
    if not a or a in ("true", "True", "1"):
        return "on"
    if a in FALSY:
        return "off"
    return a[:60]


def join_methods(raw: dict, methods: dict[str, dict], root: Path) -> dict:
    """Each tool inherits, from every response method its handler span calls, the parts whose text does not come
    from the call's argument; a gated part is inherited when the call turns the gate on (a truthy literal) and
    kept with its condition when the call passes an expression, not when it passes a falsy literal."""
    if not methods:
        return raw
    spans_of = _tool_spans(raw, {"tools": []}, root)
    for name, t in raw.get("tools", {}).items():
        calls: list[tuple[str, str]] = []
        for rel, s, e in spans_of.get(name, []):
            f = _resolve_path(root, rel)
            if f is None:
                continue
            lines = f.read_text(errors="replace").splitlines()
            calls += RESPONSE_CALL_ARGS.findall("\n".join(lines[s - 1:e]))
        have = {str(x.get("produced_at")) + "|" + str(x.get("part")) for x in (t.get("result_contents") or []) if isinstance(x, dict)}
        for m, args in calls:
            cond = _call_condition(args)
            for part in (methods.get(m) or {}).get("parts", []):
                if part.get("value_from_argument"):
                    continue
                if part.get("gate") and cond == "off":
                    continue
                key = str(part.get("produced_at")) + "|" + str(part.get("part"))
                if key in have:
                    continue
                have.add(key)
                inherited = {k: v for k, v in part.items() if k not in ("gate", "value_from_argument", "id")}
                inherited["inherited_from"] = m
                if part.get("gate") and cond not in ("on", "off"):
                    inherited["condition"] = cond
                t.setdefault("result_contents", []).append(inherited)
            gaps = (methods.get(m) or {}).get("gaps", [])
            if gaps:
                t["coverage_gaps"] = sorted(set(t.get("coverage_gaps", [])) | set(gaps))
    return raw


def finish(raw: dict, methods: dict[str, dict], root: Path | None, surface: dict | None = None) -> dict:
    """The complete judgment: inherited parts joined in, labels and injectable fields derived."""
    if root is not None:
        join_methods(raw, methods, Path(root))
    return apply_derivation(raw, surface)


def read_source(raw: dict, surface: dict, root: Path, files, client, model: str, usage_out, temperature,
                raw_out: dict | None = None) -> dict:
    """Everything after the batch judgment: the response methods, the handlers' candidates, the join and the
    derivation; `raw_out` receives each stage's inputs and the model's answers."""
    import copy
    log: list = []
    batch = copy.deepcopy(raw)
    methods = judge_methods(root, files, surface, client, model, usage_out, temperature, log)
    classify_handlers(raw, surface, root, client, model, usage_out, temperature, log)
    declared = {t["name"]: set(((t.get("inputSchema") or {}).get("properties") or {})) for t in surface.get("tools", [])}
    described = {t["name"]: str(t.get("description") or "") for t in surface.get("tools", [])}
    for name, t in raw.get("tools", {}).items():
        if any(k in t for k in INVENTORY_KEYS) and name in declared:
            if t.get("kind") == "READ":
                cite = python_state_change(root, t, name)
                if cite:
                    t["kind"] = "WRITE"
                    t.setdefault("evidence", []).append(cite)
                    t["rationale"] = f"{t.get('rationale', '')} [kind: the handler assigns to its argument's state at {cite}; WRITE]"
            added = complete_destinations(t, declared[name], name, described.get(name, ""))
            if added:
                t["rationale"] = f"{t.get('rationale', '')} [destination_args completed: {', '.join(added)}]"
    finish(raw, methods, root, surface)
    if raw_out is not None:
        raw_out.update({"batch": batch, "methods": methods, "statement_calls": log, "final": raw})
    return raw


def python_state_change(root: Path, t: dict, name: str) -> str | None:
    """A Python handler that assigns to an attribute or item of one of its own parameters (reservation.title = ...,
    account.balance -= ...) changes the state it was handed: the file:line of the first such assignment, from a
    cited file that defines the tool as a function of its name. None when no cited Python file shows one."""
    import ast
    seen = set()
    for c in t.get("evidence") or []:
        for rel, _s, _e in parse_citation(c):
            if rel in seen or not rel.endswith(".py"):
                continue
            seen.add(rel)
            f = _resolve_path(root, rel)
            if f is None:
                continue
            try:
                tree = ast.parse(f.read_text(errors="replace"))
            except SyntaxError:
                continue
            for fn in ast.walk(tree):
                if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) or fn.name != name:
                    continue
                params = {a.arg for a in fn.args.args + fn.args.kwonlyargs} - {"self", "cls"}
                for node in ast.walk(fn):
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AugAssign) else []
                    for tg in targets:
                        base = tg
                        while isinstance(base, (ast.Attribute, ast.Subscript)):
                            base = base.value
                        if tg is not base and isinstance(base, ast.Name) and base.id in params:
                            return f"{rel}:{node.lineno}"
    return None


def check_exec(root: Path, effect: ToolEffect, stats: dict) -> None:
    """A claim that supplied code runs on the host must cite a line that calls a known host execution API. A line
    that hands the code to a known browser-automation API runs it in the page: the claim is corrected to
    browser_page and HOSTEXEC removed. Any other line (an assignment, an unknown helper) keeps the claim but
    leaves the tool undetermined."""
    rs = effect.runs_supplied_code or {}
    if rs.get("where") != "host":
        return
    where = exec_where(root, str(rs.get("at", "")))
    if where == "host":
        return
    if where == "browser_page":
        stats["exec_corrected"] = stats.get("exec_corrected", 0) + 1
        effect.runs_supplied_code = {**rs, "where": "browser_page", "corrected_from": "host"}
        effect.labels = [l for l in effect.labels if l != "HOSTEXEC"]
        effect.rationale = f"{effect.rationale} [runs_supplied_code: the cited line hands the code to a browser page API; browser_page]"
        return
    stats["exec_unverified"] = stats.get("exec_unverified", 0) + 1
    effect.undetermined = True
    effect.rationale = f"{effect.rationale} [unverified exec: the cited line calls no known host execution API]"


def check_outbound(root: Path, effect: ToolEffect, stats: dict) -> None:
    """Each outbound call the model classed as a literal host or as the user's own service is resolved from the
    code through its destination alone (evidence.resolve_destination). A "literal host" that resolves to a
    configuration read is reclassified as the user's own service and the labels re-derived; one that resolves to
    nothing keeps its label but leaves the tool undetermined, unless its cited line hands something to a browser
    page API and names no host (then it is local); an "own service" that resolves to a literal host keeps its
    class but leaves the tool undetermined. An argument-named destination is never demoted by the HTTP method."""
    changed = False
    for c in effect.outbound_calls:
        cls = c.get("destination_class")
        if cls not in ("literal_host_in_code", "configured_own_service"):
            continue
        kind, found = resolve_destination(root, str(c.get("at", "")), str(c.get("host_or_config", "")))
        c["resolved"] = f"{kind}:{found}" if kind else "unresolved"
        if cls == "literal_host_in_code" and kind == "configured":
            stats["outbound_reclassified"] = stats.get("outbound_reclassified", 0) + 1
            c["destination_class"], c["reclassified_from"] = "configured_own_service", cls
            changed = True
        elif cls == "literal_host_in_code" and kind is None:
            if exec_where(root, str(c.get("at", ""))) == "browser_page":
                stats["outbound_reclassified"] = stats.get("outbound_reclassified", 0) + 1
                c["destination_class"], c["reclassified_from"] = "local_file_or_process", cls
                changed = True
                continue
            stats["outbound_unverified"] = stats.get("outbound_unverified", 0) + 1
            effect.undetermined = True
            effect.rationale = f"{effect.rationale} [unverified outbound: {c.get('host_or_config')!r} resolves to no host or configuration]"
        elif cls == "configured_own_service" and kind == "literal":
            stats["outbound_unverified"] = stats.get("outbound_unverified", 0) + 1
            effect.undetermined = True
            effect.rationale = f"{effect.rationale} [own-service call resolves to the literal host {found}; class kept, undetermined]"
    if changed:
        effect.labels, _inj = derive({"result_contents": effect.result_contents, "outbound_calls": effect.outbound_calls,
                                      "runs_supplied_code": effect.runs_supplied_code, "destination_args": effect.destination_args,
                                      "delivers_to_chosen_party": effect.delivers_to_chosen_party})
        effect.rationale = f"{effect.rationale} [outbound reclassified from the code's destination]"


def _own_code_missing_names(root: Path, tool_names: list[str]) -> list[str]:
    """Tool names that occur in none of root's own non-documentation source files that
    select_files would ever consider (excludes root/.deps: a dependency copied into a
    cached tree by a version of this code from before per-run views doesn't count as
    "own code"). Uses select_files's own is_candidate rule plus classify_evidence's
    documentation rule, so a name that appears only in a vendored, test or non-allowlisted
    file never counts as "found" when select_files would never surface that file anyway."""
    found: set[str] = set()
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root)
        if rel.parts[0] == ".deps" or not is_candidate(p, root) or classify_evidence(str(rel)) == "doc":
            continue
        try:
            text = p.read_text(errors="strict")
        except (UnicodeDecodeError, ValueError):
            continue
        found |= {name for name in tool_names if name not in found and name in text}
    return sorted(name for name in tool_names if name not in found)


def _copy_tree(src: Path, dst: Path) -> None:
    """Copy src to dst, materialising no symlink (one in an untrusted tree could point
    anywhere) and leaving out src's own top-level .deps (a stale copy-in, see above)."""
    def ignore(d, names):
        return [n for n in names if os.path.islink(os.path.join(d, n)) or (n == ".deps" and Path(d) == Path(src))]
    shutil.copytree(src, dst, ignore=ignore)


def prepare_sources(kind: str, package: str, version: str, cache_dir: Path, tool_names: list[str],
                    source_ref: str | None = None, run=subprocess.run,
                    source_version: str | None = None) -> tuple[Path, list[tuple[str, str]], list[str]]:
    """Fetch a package's source and return (view, selected files, notes), where view is a
    per-run copy of the cached tree at cache_dir/.views/<slug>, rebuilt from scratch every
    call. For npm, when a tool name the model must judge appears nowhere in the package's
    own code (a thin wrapper package typically implements its tools in a dependency), each
    direct dependency kept is copied into the view under .deps/<sanitised name>/. Cached
    trees are never modified, so nothing kept by an earlier run can reach this one."""
    cached = fetch_source(kind, source_ref or package, source_version or version, cache_dir, run=run)
    kept, skipped = [], []
    if kind == "npm":
        missing = _own_code_missing_names(cached, tool_names)
        if missing:
            kept, skipped = fetch_dependencies(cached, missing, cache_dir, run=run)
    view = cache_path(cache_dir, f".views/{cached.name}")
    if view.exists():
        shutil.rmtree(view)
    view.parent.mkdir(parents=True, exist_ok=True)
    _copy_tree(cached, view)
    included = []
    for name, resolved, dep_root in kept:
        dest = view / ".deps" / slugify(name)
        if dest.exists():
            skipped.append((name, "its directory name collides with another dependency's"))
            continue
        _copy_tree(dep_root, dest)
        included.append(f"{name}@{resolved}")
    notes = [f"dependency sources included: {', '.join(included)}"] if included else []
    notes += [f"dependency skipped: {name!r:.120} ({reason})" for name, reason in skipped]
    return view, select_files(view, tool_names), notes


def evidence_kind_counts(files: list[tuple[str, str]]) -> tuple[int, int]:
    """(code_files_shown, doc_files_shown): how many of the files selected for the model
    are implementation vs documentation, by evidence.classify_evidence. Recorded, never
    gated on - a package the model could only judge from a README (a thin wrapper whose
    implementation lives elsewhere, or one with no reachable code at all) still runs; this
    is what let the @notionhq/notion-mcp-server (0 files shown) and @azure/mcp (doc-only)
    gaps be seen after the fact instead of passing unremarked."""
    code = sum(1 for rel, _ in files if classify_evidence(rel) == "code")
    return code, len(files) - code


def build_profile(package: str, version: str | None, kind: str, surfaces_path: Path, cache_dir: Path, client,
                  source_ref: str | None = None, usage_out: dict | None = None,
                  model: str = "claude-opus-5", threat_model: str = "default",
                  source_version: str | None = None, temperature: float | None = None,
                  raw_out: dict | None = None) -> tuple[Profile, dict]:
    surface = load_surface(surfaces_path, package, version)
    source_ref = source_ref or surface.get("source_ref")  # a git package names its repository in the surface
    source_version = source_version or surface.get("source_version")
    root, files, notes = prepare_sources(kind, package, surface["version"], cache_dir,
                                         [t["name"] for t in surface["tools"]], source_ref=source_ref,
                                         source_version=source_version)
    budget = 400_000
    while True:
        try:
            raw = adjudicate(surface, files, client=client, usage_out=usage_out, model=model, rubric=rubric(threat_model),
                             temperature=temperature)
            raw = read_source(raw, surface, root, files, client, model, usage_out, temperature, raw_out)
            break
        except Exception as e:
            # ponytail: shrink the source budget until the prompt fits the model's context (gpt-4o: 128K tokens);
            # counting tokens up front would avoid the failed calls, which cost nothing.
            if "context_length_exceeded" not in str(e) or budget <= 50_000:
                raise
            budget = int(budget * 0.75)
            files = select_files(root, [t["name"] for t in surface["tools"]], budget)
            notes = notes + [f"source budget lowered to {budget} bytes to fit the model's context"]
    profile, stats = verify(raw, surface, root)
    profile.notes = list(profile.notes) + notes
    stats["source_budget_bytes"] = budget
    stats["temperature"] = temperature
    stats["seed"] = SEED
    stats["rubric_sha"] = hashlib.sha256(rubric(threat_model).encode()).hexdigest()[:16]
    stats["schema_sha"] = hashlib.sha256(json.dumps(EFFECT_SCHEMA, sort_keys=True).encode()).hexdigest()[:16]
    stats["source_sha"] = hashlib.sha256("".join(t for _, t in files).encode()).hexdigest()[:16]
    if raw_out is not None:
        raw_out["hashes"] = {k: stats[k] for k in ("rubric_sha", "schema_sha", "source_sha")}
        raw_out["run"] = {"model": model, "temperature": temperature, "seed": SEED, "files": [rel for rel, _ in files]}
    stats["code_files_shown"], stats["doc_files_shown"] = evidence_kind_counts(files)
    return profile, stats
