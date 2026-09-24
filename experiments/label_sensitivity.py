#!/usr/bin/env python3
"""What a wrong label costs: the offline replay under one mislabeled tool at a time.

A provider description that understates a tool turns into a wrong label for a policy
that trusts descriptions. Two understatements are replayed for every tool the guard
gates: the tool's destination arguments are dropped (the text did not say where the data
goes), and a WRITE is described as a READ (the text did not say it changes anything).
Each perturbation is replayed through the guard's rules over the undefended trajectories,
and the change in ASR and BU is reported, so that the value of getting a label right can
be read tool by tool.

  baselines/agentdojo/.venv/bin/python experiments/label_sensitivity.py [effects.json]
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import offline_eval as oe  # noqa: E402

RULES = ["T-strict", "T-strict+W-oracle"]


def run(effects_path: Path) -> dict[str, dict]:
    benign, attack = oe.load_runs(effects_path)
    return {r["name"]: r for r in oe.evaluate(benign, attack, RULES)}


def main() -> None:
    path = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else oe.ROOT / "experiments/effects/agentdojo.v4.json"
    base = json.loads(path.read_text())
    ref = run(path)
    print("baseline:", {k: (v["est_BU_pct"], v["est_ASR_pct"]) for k, v in ref.items()})
    results = []
    with tempfile.TemporaryDirectory() as td:
        for suite, v in base.items():
            for t in v["tools"]:
                perturbations = []
                if t.get("destination_args"):
                    perturbations.append(("no-destination", {"destination_args": []}))
                if t.get("kind") == "WRITE" and (t.get("destination_args") or t.get("value_args")):
                    perturbations.append(("as-read", {"kind": "READ"}))
                for pname, change in perturbations:
                    mod = json.loads(json.dumps(base))
                    next(x for x in mod[suite]["tools"] if x["name"] == t["name"]).update(change)
                    p = Path(td) / f"{suite}-{t['name']}-{pname}.json"
                    p.write_text(json.dumps(mod))
                    rows = run(p)
                    row = {"suite": suite, "tool": t["name"], "perturbation": pname}
                    for rule in RULES:
                        row[f"{rule}/ASR"] = rows[rule]["est_ASR_pct"]
                        row[f"{rule}/BU"] = rows[rule]["est_BU_pct"]
                        row[f"{rule}/dASR"] = round(rows[rule]["est_ASR_pct"] - ref[rule]["est_ASR_pct"], 2)
                    results.append(row)
                    print(f"{suite:10s} {t['name']:32s} {pname:15s} " +
                          " ".join(f"{rule}: ASR {row[f'{rule}/ASR']:6.2f} ({row[f'{rule}/dASR']:+.2f})" for rule in RULES), flush=True)
    results.sort(key=lambda r: -r["T-strict+W-oracle/dASR"])
    out = {"effects": str(path.relative_to(oe.ROOT)) if path.is_relative_to(oe.ROOT) else str(path), "baseline": {k: {"BU": v["est_BU_pct"], "ASR": v["est_ASR_pct"]} for k, v in ref.items()},
           "perturbations": results}
    (oe.ROOT / "experiments/label_sensitivity.json").write_text(json.dumps(out, indent=1))
    print(f"\n{len(results)} perturbations -> experiments/label_sensitivity.json")


if __name__ == "__main__":
    main()
