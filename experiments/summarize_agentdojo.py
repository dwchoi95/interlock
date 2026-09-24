#!/usr/bin/env python3
"""Run Summarize on AgentDojo's own tool code and compare it with the reference labels.

The guard's labels for AgentDojo (experiments/effects/agentdojo.v4.json) were classified
from the tool code and re-checked by hand. This script produces the same fields through
the Summarize stage itself: model judgment under the rubric with file:line evidence,
deterministic re-checking of every citation, argument names checked against the
advertised schema. The result is written in the guard's label format, so that the labels
the enforcement rules consume are the stage's own output, and it is measured against the
reference field by field.

  baselines/agentdojo/.venv/bin/python experiments/summarize_agentdojo.py --model gpt-5.5
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "experiments"))
from src.adjudicate import adjudicate, is_openai_model, rubric  # noqa: E402
from src.pipeline import verify  # noqa: E402
from src.profile import Profile  # noqa: E402
from guard import _field  # noqa: E402

AGENTDOJO = ROOT / "baselines/agentdojo/src/agentdojo"
TOOL_DIR = "default_suites/v1/tools"
SUITES = ["workspace", "slack", "travel", "banking"]
REFERENCE = ROOT / "experiments/effects/agentdojo.v4.json"
FIELD_PAIRS = [("destination_args", "destination_args"), ("value_args", "value_args"),
               ("injectable_output_fields", "injectable_output_fields"),
               ("structured_output_fields", "structured_output_fields")]


def surfaces() -> dict[str, dict]:
    """The advertised surface of each suite, in the shape Summarize reads: name, description,
    input schema per tool, taken from the benchmark's own Function objects."""
    from agentdojo.task_suite import get_suite
    out = {}
    for s in SUITES:
        suite = get_suite("v1.2", s)
        tools = []
        for f in suite.tools:
            schema = f.parameters.model_json_schema()
            tools.append({"name": f.name, "description": f.description,
                          "inputSchema": {"type": "object", "properties": schema.get("properties", {}),
                                          "required": schema.get("required", [])}})
        out[s] = {"package": f"agentdojo/{s}", "version": "v1.2", "kind": "python", "tools": tools}
    return out


def numbered(path: Path) -> str:
    return "\n".join(f"{i:>6}| {line}" for i, line in enumerate(path.read_text().splitlines(), start=1))


def source_files() -> list[tuple[str, str]]:
    """Every tool module of the v1 suites, with the paths the reference labels cite."""
    return [(f"{TOOL_DIR}/{p.name}", numbered(p))
            for p in sorted((AGENTDOJO / TOOL_DIR).glob("*.py")) if p.name != "__init__.py"]


def data_files(suite: str) -> list[tuple[str, str]]:
    """The records the suite's tools serve: environment.yaml and its includes, as a benign
    run sees them - every injection placeholder filled with the benchmark's own benign
    default, and injection_vectors.yaml (the list of attack points) never shown. This is
    the service's data model in the sense that matters for result fields: who wrote each
    record. Defaults hold no newline, so line numbers match the files on disk."""
    from agentdojo.task_suite import get_suite
    defaults = get_suite("v1.2", suite).get_injection_vector_defaults()
    assert not any("\n" in v for v in defaults.values())
    root = AGENTDOJO / "data/suites" / suite
    out = []
    for p in sorted(root.rglob("*.yaml")):
        if p.name == "injection_vectors.yaml":
            continue
        text = p.read_text()
        for name, value in defaults.items():
            text = text.replace("{" + name + "}", value)
        rel = f"data/suites/{suite}/{p.relative_to(root)}"
        out.append((rel, "\n".join(f"{i:>6}| {line}" for i, line in enumerate(text.splitlines(), start=1))))
    return out


def make_client(model: str):
    if is_openai_model(model):
        import openai
        return openai.OpenAI()
    import anthropic
    return anthropic.Anthropic()


def to_guard_format(profile: Profile, suite: str) -> dict:
    tools = []
    for name, e in profile.tools.items():
        tools.append({"name": name, "kind": e.kind or "WRITE", "destination_args": e.destination_args,
                      "value_args": e.value_args, "injectable_output_fields": e.injectable_output_fields,
                      "structured_output_fields": e.identifier_output_fields,
                      "attacker_named_fields": e.attacker_named_fields, "labels": e.labels,
                      "undetermined": e.undetermined, "evidence": "; ".join(e.evidence), "rationale": e.rationale})
    return {"suite": suite, "tools": tools}


def norm_fields(entries) -> set[str]:
    return {_field(x) for x in entries if x and x.strip()}


def compare(produced: dict, reference: dict) -> dict:
    agg = defaultdict(lambda: {"tp": 0, "fp": 0, "fn": 0})
    kind = {"agree": 0, "total": 0, "disagreements": []}
    diffs = []
    for suite in SUITES:
        ref = {t["name"]: t for t in reference[suite]["tools"]}
        got = {t["name"]: t for t in produced[suite]["tools"]}
        for name, r in ref.items():
            g = got.get(name)
            if g is None:
                continue
            kind["total"] += 1
            if g["kind"] == r["kind"]:
                kind["agree"] += 1
            else:
                kind["disagreements"].append(f"{suite}/{name}: produced {g['kind']}, reference {r['kind']}")
            for f, rf in FIELD_PAIRS:
                a, b = norm_fields(g.get(f, [])), norm_fields(r.get(rf, []))
                agg[f]["tp"] += len(a & b)
                agg[f]["fp"] += len(a - b)
                agg[f]["fn"] += len(b - a)
                if a != b:
                    diffs.append({"suite": suite, "tool": name, "field": f, "extra": sorted(a - b), "missing": sorted(b - a)})
    summary = {}
    for f, c in agg.items():
        p = c["tp"] / (c["tp"] + c["fp"]) if c["tp"] + c["fp"] else 1.0
        r = c["tp"] / (c["tp"] + c["fn"]) if c["tp"] + c["fn"] else 1.0
        summary[f] = {**c, "precision": round(p, 3), "recall": round(r, 3)}
    return {"kind": kind, "fields": summary, "differences": diffs}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="gpt-5.5")
    ap.add_argument("--description-only", action="store_true", help="withhold the code: what the advertised text alone yields")
    ap.add_argument("--threat-model", default="default", choices=["default", "received-documents"],
                    help="received-documents: documents the user received from others are attacker-authored")
    ap.add_argument("--with-data", action="store_true",
                    help="also show the records each suite's tools serve (benign defaults in place of injection placeholders)")
    ap.add_argument("--out", default=str(ROOT / "experiments/effects/summarize"))
    a = ap.parse_args()
    tag = (a.model + ("-description-only" if a.description_only else "") + ("-with-data" if a.with_data else "")
           + ("-received" if a.threat_model == "received-documents" else ""))
    out_dir = Path(a.out) / tag
    out_dir.mkdir(parents=True, exist_ok=True)
    client = make_client(a.model)
    files = [] if a.description_only else source_files()
    rub = rubric(a.threat_model, description_only=a.description_only)
    produced, all_stats = {}, {}
    for suite, surface in surfaces().items():
        usage: dict = {}
        shown = files + (data_files(suite) if a.with_data else [])
        raw = adjudicate(surface, shown, client=client, model=a.model, usage_out=usage, rubric=rub)
        if a.description_only:
            # No code to check against: keep the judgment as returned, with the schema check only.
            from src.pipeline import check_enforcement_fields
            from src.profile import ENFORCEMENT_FIELDS, ToolEffect
            tools, stats = {}, {"tools": len(surface["tools"]), "kind_write": 0, "with_destination_args": 0,
                                "arg_name_errors": 0, "named_field_errors": 0, "missing_tools": []}
            for t in surface["tools"]:
                j = raw["tools"].get(t["name"])
                if j is None:
                    stats["missing_tools"].append(t["name"])
                    continue
                e = ToolEffect(labels=list(j["labels"]), evidence=list(j["evidence"]), rationale=j["rationale"],
                               default_enabled=j.get("default_enabled", True), undetermined=bool(j.get("undetermined")),
                               kind=j.get("kind"), **{f: list(j.get(f) or []) for f in ENFORCEMENT_FIELDS})
                check_enforcement_fields(e, t, stats)
                tools[t["name"]] = e
            profile = Profile(package=surface["package"], version=surface["version"], kind=surface["kind"],
                              source="(description only)", tools=tools, notes=raw.get("notes", []))
        else:
            profile, stats = verify(raw, surface, AGENTDOJO)
            profile.source = str(AGENTDOJO.relative_to(ROOT))  # never a machine-specific path in the record
        stats["usage"] = usage
        all_stats[suite] = stats
        produced[suite] = to_guard_format(profile, suite)
        (out_dir / f"{suite}.profile.json").write_text(profile.to_json())
        print(f"{suite}: {stats}")
    effects_path = ROOT / f"experiments/effects/agentdojo.summarize-{tag}.json"
    effects_path.write_text(json.dumps(produced, indent=1))
    reference = json.loads(REFERENCE.read_text())
    cmp = compare(produced, reference)
    cmp["stats"] = all_stats
    cmp["effects"] = str(effects_path.relative_to(ROOT))
    (out_dir / "comparison.json").write_text(json.dumps(cmp, indent=1))
    print(f"\nkind agreement: {cmp['kind']['agree']}/{cmp['kind']['total']}")
    for f, c in cmp["fields"].items():
        print(f"{f:28s} P={c['precision']:.3f} R={c['recall']:.3f}  (tp {c['tp']}, fp {c['fp']}, fn {c['fn']})")
    print(f"{len(cmp['differences'])} field differences -> {out_dir / 'comparison.json'}; labels -> {effects_path}")


if __name__ == "__main__":
    main()
