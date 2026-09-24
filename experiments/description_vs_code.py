#!/usr/bin/env python3
"""What a policy generator that reads only the advertised text concludes, against what the code shows.

For every tool of the profiled servers the rubric is run with the source withheld: the
model sees the tool's name, description, input schema and annotations, as Progent's
policy generator does, and nothing else. Its labels are compared with the code-derived,
re-checked labels in profiles/. A tool is *under-described* when the code shows an effect
the text did not reveal, *over-described* when the text claims one the code does not
show. The provider annotations (readOnlyHint, openWorldHint) are compared with the code
labels deterministically as well.

  baselines/agentdojo/.venv/bin/python experiments/description_vs_code.py --model gpt-5.5
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from src.adjudicate import RUBRIC_DESCRIPTION_ONLY, adjudicate, is_openai_model  # noqa: E402
from src.profile import Profile, expand  # noqa: E402
from src.surface import load_surface  # noqa: E402

SURFACES = ROOT / "spikes/p0/data/surfaces.jsonl"
PROFILES = ROOT / "profiles"


def make_client(model: str):
    if is_openai_model(model):
        import openai
        return openai.OpenAI()
    import anthropic
    return anthropic.Anthropic()


def annotation_contradictions(surface: dict, profile: Profile) -> list[dict]:
    """Provider annotations that understate what the code shows."""
    out = []
    for t in surface["tools"]:
        a = t.get("annotations") or {}
        e = profile.tools.get(t["name"])
        if e is None:
            continue
        labels = expand(set(e.labels))
        if a.get("readOnlyHint") is True and labels & {"SINK", "HOSTEXEC"}:
            out.append({"tool": t["name"], "annotation": "readOnlyHint=true", "code": sorted(e.labels), "undetermined": e.undetermined})
        if a.get("openWorldHint") is False and labels & {"SINK", "UNTRUSTED"}:
            out.append({"tool": t["name"], "annotation": "openWorldHint=false", "code": sorted(e.labels), "undetermined": e.undetermined})
    return out


def judge(surface: dict, client, model: str) -> tuple[dict, dict]:
    usage: dict = {}
    raw = adjudicate(surface, [], client=client, model=model, usage_out=usage, rubric=RUBRIC_DESCRIPTION_ONLY)
    return raw, usage


def compare(surface: dict, profile: Profile, raw: dict) -> list[dict]:
    rows = []
    for t in surface["tools"]:
        name = t["name"]
        e = profile.tools.get(name)
        j = raw["tools"].get(name)
        if e is None or j is None:
            continue
        code, desc = expand(set(e.labels)), expand(set(j.get("labels", [])))
        rows.append({"tool": name, "code": sorted(e.labels), "description": sorted(j.get("labels", [])),
                     "code_verified": not e.undetermined, "hidden": sorted(code - desc), "overstated": sorted(desc - code),
                     "kind_description": j.get("kind"), "destination_args_description": j.get("destination_args", []),
                     "annotations": t.get("annotations") or {}, "rationale_code": e.rationale[:300]})
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="gpt-5.5")
    ap.add_argument("--out", default=str(ROOT / "experiments/description_vs_code"))
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--profiles", default=str(PROFILES), help="code-derived profiles to compare against")
    ap.add_argument("--tag", default="", help="suffix for the compare_*/summary files (the description judgments are cached per model)")
    a = ap.parse_args()
    out_dir = Path(a.out) / a.model
    out_dir.mkdir(parents=True, exist_ok=True)
    client = make_client(a.model)
    profiles = [Profile.from_json(p.read_text()) for p in sorted(Path(a.profiles).glob("*.json")) if "manifest" not in p.name]
    jobs = []
    for p in profiles:
        surface = load_surface(SURFACES, p.package, p.version)
        jobs.append((p, surface))

    def run(job):
        p, surface = job
        cached = out_dir / f"{p.kind}_{p.package.replace('/', '_').lstrip('@')}_{p.version}.json"
        if cached.exists():
            return p, surface, json.loads(cached.read_text())
        raw, usage = judge(surface, client, a.model)
        raw["usage"] = usage
        cached.write_text(json.dumps(raw, indent=1))
        return p, surface, raw

    totals = Counter()
    per_label = defaultdict(Counter)
    examples = []
    contradictions = []
    usage = Counter()
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        for p, surface, raw in ex.map(run, jobs):
            usage.update(raw.get("usage", {}))
            rows = compare(surface, p, raw)
            for r in rows:
                for scope in ("all",) + (("verified",) if r["code_verified"] else ()):
                    totals[(scope, "tools")] += 1
                    if r["hidden"] and not r["overstated"]:
                        totals[(scope, "under")] += 1
                    elif r["overstated"] and not r["hidden"]:
                        totals[(scope, "over")] += 1
                    elif r["hidden"] and r["overstated"]:
                        totals[(scope, "mixed")] += 1
                    else:
                        totals[(scope, "agree")] += 1
                    for lab in r["hidden"]:
                        per_label[scope][f"hidden:{lab}"] += 1
                    for lab in r["overstated"]:
                        per_label[scope][f"overstated:{lab}"] += 1
                if r["hidden"] and r["code_verified"]:
                    examples.append({"package": p.package, **{k: r[k] for k in ("tool", "code", "description", "hidden", "rationale_code")}})
            for c in annotation_contradictions(surface, p):
                contradictions.append({"package": p.package, **c})
            (out_dir / f"compare{a.tag}_{p.kind}_{p.package.replace('/', '_').lstrip('@')}_{p.version}.json").write_text(json.dumps(rows, indent=1))
    summary = {"model": a.model, "totals": {f"{s}/{k}": v for (s, k), v in sorted(totals.items())},
               "per_label": {s: dict(c) for s, c in per_label.items()}, "usage": dict(usage),
               "annotation_contradictions": contradictions, "hidden_effect_examples": examples}
    summary["profiles"] = a.profiles
    (out_dir / f"summary{a.tag}.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps({k: v for k, v in summary.items() if k not in ("hidden_effect_examples",)}, indent=1))
    print(f"{len(examples)} verified tools with an effect the description hides -> {out_dir / f'summary{a.tag}.json'}")


if __name__ == "__main__":
    main()
