#!/usr/bin/env python3
"""Judge again the tools a profile left unjudged, with the source chosen for those tools alone.

The first call chooses files by every tool name of the server; on a large server the budget fills with the files
that mention most tools (docs, registries) and the handlers of the rest never reach the model, which then skips
them. Here the surface is cut to the unjudged tools, files are chosen by their names (plus the name after its
first underscore when the full name occurs nowhere in the source, as for mcp-atlassian's "jira_" prefix), and
Markdown files are left out, since they
name every tool and would fill the budget. The verified judgements replace the fallback in the profile. The stats line of the package is updated in place.

  python -m src.summarize.rejudge results/summarize/mcpwild/gpt-4o-2024-08-06 mcp-grafana workspace-mcp mcp-atlassian
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from src.summarize.adjudicate import adjudicate, rubric
from src.summarize.cli import estimate_cost_usd, make_client
from src.summarize.pipeline import read_source, verify
from src.summarize.profile import Profile
from src.summarize.select import is_candidate, select_files

MODEL = "gpt-4o-2024-08-06"
SURFACES = Path("benchmark/mcpwild/surfaces.jsonl")
ADDITIVE = ["claims", "verified", "verified_doc", "demoted", "cleared_verified", "cleared_unverified",
            "kind_write", "with_destination_args", "arg_name_errors", "named_field_errors"]


def selection_names(root: Path, names: list[str]) -> list[str]:
    text = "\n".join(p.read_text(errors="ignore") for p in root.rglob("*") if p.is_file() and is_candidate(p, root))
    return names + [n.split("_", 1)[1] for n in names if n not in text and "_" in n]


def rejudge(out_dir: Path, package: str, client, group: int = 60) -> str:
    lines = [json.loads(l) for l in (out_dir / "stats.jsonl").open()]
    k = max(i for i, s in enumerate(lines) if s["package"] == package)
    stats = lines[k]
    missing = stats["missing_tools"]
    path = next(p for p in out_dir.glob("*.json") if json.loads(p.read_text()).get("package") == package)
    profile = Profile.from_json(path.read_text())
    root = Path(profile.source)
    surface = next(json.loads(l) for l in SURFACES.open() if json.loads(l)["pkg"] == package)
    total, usage, runs = {f: 0 for f in ADDITIVE}, {}, []
    still: list[str] = []
    for i in range(0, len(missing), group):  # each group gets the files of its own tools
        part = missing[i:i + group]
        sub = {"package": package, "version": profile.version, "kind": profile.kind,
               "tools": [t for t in surface["tools"] if t["name"] in part]}
        names, budget = selection_names(root, part), 400_000
        while True:
            # docs name every tool and fill the budget first (mcp-grafana); a clearance never rests on them anyway
            files = [f for f in select_files(root, names, budget * 2) if not f[0].endswith(".md")]
            files = files[:next((j for j in range(len(files)) if sum(len(t) for _, t in files[:j + 1]) > budget),
                                len(files))] or files[:1]
            try:
                raw = adjudicate(sub, files, client=client, model=MODEL, usage_out=usage, rubric=rubric())
                raw = read_source(raw, sub, root, files, client, MODEL, usage, None)
                break
            except Exception as e:  # same shrink rule as build_profile
                if "context_length_exceeded" not in str(e) or budget <= 50_000:
                    raise
                budget = int(budget * 0.75)
        new, st = verify(raw, sub, root)
        profile.tools.update({n: e for n, e in new.tools.items() if n not in st["missing_tools"]})
        still += st["missing_tools"]
        for f in ADDITIVE:
            total[f] += st[f]
        runs.append({"tools": part, "files": [r for r, _ in files], "budget": budget})
    profile.notes = list(profile.notes) + [f"re-judged {len(missing)} unjudged tools in groups of {group} with source "
                                           f"chosen for each group; {len(still)} still unjudged"]
    path.write_text(profile.to_json())
    for f in ADDITIVE:
        stats[f] = stats.get(f, 0) + total[f]
    stats["missing_tools"] = still
    cost = estimate_cost_usd(usage, model=MODEL, batch=False)
    stats.setdefault("rejudges", []).append({"group": group, "runs": runs, "usage": usage, "cost_usd": cost})
    stats["cost_usd"] = stats.get("cost_usd", 0) + cost
    lines[k] = stats
    (out_dir / "stats.jsonl").write_text("".join(json.dumps(s) + "\n" for s in lines))
    return (f"{package}: {len(missing)} re-judged, {len(still)} still unjudged, "
            f"{total['verified']}/{total['claims']} claims verified, ${cost:.4f}")


if __name__ == "__main__":
    client = make_client(MODEL)
    for pkg in sys.argv[2:]:
        print(rejudge(Path(sys.argv[1]), pkg, client, group=10), flush=True)
