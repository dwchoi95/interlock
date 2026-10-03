"""Figure: tools with a hidden effect per MCPWild server and AgentDojo suite (replaces Table 1).

Counts come from the E1 audit sheets (experiments/summarize/mcpwild/e1-v67{,-k8s}): a flagged tool is a
candidate; it is confirmed by the consensus sheet when the sheet lists it, otherwise when both annotators said yes.
Run from the repo root: python3 paper/figures/hidden_effects.py
"""
import csv, json
from pathlib import Path

import matplotlib
matplotlib.use("pgf")
import matplotlib.pyplot as plt
from matplotlib.colors import PowerNorm

ROOT = Path(__file__).resolve().parents[2]
RUNS = [ROOT / "experiments/summarize/mcpwild" / r for r in ("e1-v67", "e1-v67-k8s")]
LABELS = ["SECRET", "UNTRUSTED", "SINK", "HOSTEXEC"]
AGENTDOJO_TOOLS = {"workspace": 24, "slack": 11, "travel": 28, "banking": 11}  # AgentDojo v1 suites


def counts():
    tools = {}
    for line in (ROOT / "benchmark/mcpwild/surfaces.jsonl").open():
        r = json.loads(line)
        if r.get("ok"):
            tools[r["pkg"]] = len(r["tools"])
    tools.update({f"agentdojo/{s}": n for s, n in AGENTDOJO_TOOLS.items()})
    rows = {}
    for run in RUNS:
        a, b = (list(csv.DictReader((run / f"e1_audit_annotator{k}.csv").open(encoding="utf-8-sig"))) for k in (1, 2))
        cons = {(c["Server"], c["Tool"]): c["Consensus"].strip().lower()
                for c in csv.DictReader((run / "e1_audit_consensus.csv").open(encoding="utf-8-sig"))}
        for x, y in zip(a, b):
            v1, v2 = x["Hidden Effect"].strip().lower(), y["Hidden Effect"].strip().lower()
            # the consensus sheet decides every row it lists (disagreements and later source reviews), as in e1_audit
            ok = cons.get((x["Server"], x["Tool"]), "yes" if v1 == v2 == "yes" else "no") == "yes"
            rows[(x["Benchmark"], x["Server"].rsplit("@", 1)[0] if x["Benchmark"] == "MCPWild" else x["Server"], x["Tool"])] = (x["Hidden Labels"].split(), ok)
    srv = {k: {"bench": "AgentDojo" if k.startswith("agentdojo/") else "MCPWild", "tools": n, "cand": 0, "all": 0,
               **dict.fromkeys(LABELS, 0)} for k, n in tools.items()}
    for (_, s, _), (labels, ok) in rows.items():
        d = srv[s]
        d["cand"] += 1
        if ok:
            d["all"] += 1
            for l in labels:
                d[l] += 1
    return srv


def short(name):
    return name.split("/", 1)[1] if name.startswith(("agentdojo/", "@modelcontextprotocol/", "@antv/", "@agent-infra/",
                                                      "@arizeai/", "@wonderwhy-er/")) else name.lstrip("@")


def main():
    srv = counts()
    wild = sorted(((k, d) for k, d in srv.items() if d["bench"] == "MCPWild" and d["all"]),
                  key=lambda kv: (-kv[1]["all"], -kv[1]["cand"], kv[0]))
    dojo = [(f"agentdojo/{s}", srv[f"agentdojo/{s}"]) for s in AGENTDOJO_TOOLS]
    tot = {k: sum(d[k] for d in srv.values() if d["bench"] == "MCPWild") for k in ("tools", "cand", "all", *LABELS)}
    assert (tot["tools"], tot["cand"], tot["all"], *(tot[l] for l in LABELS)) == (1353, 538, 111, 69, 33, 40, 2), tot
    assert len(wild) == 21 and not any(d["all"] for _, d in dojo)

    plt.rcParams.update({
        "pgf.texsystem": "pdflatex", "pgf.rcfonts": False, "font.family": "serif",
        "pgf.preamble": r"\usepackage[T1]{fontenc}\usepackage{libertine}",
        "font.size": 7.5, "axes.linewidth": 0.4, "xtick.major.width": 0.4, "ytick.major.width": 0,
        "xtick.major.size": 2, "ytick.major.size": 0, "xtick.major.pad": 1.5, "ytick.major.pad": 3,
    })
    fig, axes = plt.subplots(2, 2, figsize=(395.8 / 72, 3.1), sharex="col", sharey="row",
                             gridspec_kw={"height_ratios": [len(wild), len(dojo)], "width_ratios": [1.0, 1.45],
                                          "hspace": 0.06, "wspace": 0.03})
    fig.subplots_adjust(left=0.272, right=0.992, top=0.915, bottom=0.115)
    shades = {"tools": "0.88", "cand": "0.62", "all": "0.12"}
    names = {"tools": "Tools", "cand": "Candidates", "all": "Hidden effect"}
    cols = ["all", *LABELS]
    norm = PowerNorm(0.5, vmin=0, vmax=max(d["all"] for _, d in wild))
    for row, group, bench in ((0, wild, "MCPWild"), (1, dojo, "AgentDojo")):
        y = range(len(group))
        bars, heat = axes[row]
        for k in ("tools", "cand", "all"):
            bars.barh(list(y), [d[k] for _, d in group], height=0.72, color=shades[k], label=names[k], linewidth=0)
        bars.set_yticks(list(y), [short(s) for s, _ in group], family="monospace", fontsize=6.3)
        bars.set_ylim(len(group) - 0.5, -0.5)
        bars.grid(axis="x", linewidth=0.3, color="0.85")
        bars.set_axisbelow(True)
        for side in ("top", "right"):
            bars.spines[side].set_visible(False)
        for side in ("top", "right", "bottom", "left"):
            heat.spines[side].set_visible(False)
        m = [[d[c] for c in cols] for _, d in group]
        heat.imshow(m, cmap="Greys", norm=norm, aspect="auto", extent=(-0.5, len(cols) - 0.5, len(group) - 0.5, -0.5))
        for i, r in enumerate(m):
            for j, v in enumerate(r):
                heat.text(j, i, v, ha="center", va="center", fontsize=6.6,
                          color="0.6" if v == 0 else ("white" if norm(v) > 0.55 else "black"))
        heat.tick_params(axis="x", length=0)
        heat.set_xticks([])
        for j in range(len(cols) - 1):  # surface gaps between cells, wider after the "all" column
            heat.axvline(j + 0.5, color="white", linewidth=2.2 if j == 0 else 0.8)
        for i in range(len(group) - 1):
            heat.axhline(i + 0.5, color="white", linewidth=0.8)
        box = bars.get_position()
        fig.text(0.002, (box.y0 + box.y1) / 2, bench, rotation=90, ha="left", va="center", fontsize=7.5)
    axes[1, 0].set_xlabel("Number of tools", labelpad=1.5)
    axes[0, 0].legend(loc="lower right", bbox_to_anchor=(1.0, 1.0), ncol=3, frameon=False, fontsize=7,
                      handlelength=1.0, handletextpad=0.4, columnspacing=1.0, borderaxespad=0.2)
    top = axes[0, 1]
    for j, c in enumerate(cols):
        top.text(j, -0.75, c, ha="center", va="bottom", fontsize=6.2)
    top.text((len(cols) - 1) / 2, -2.0, "Hidden effect", ha="center", va="bottom", fontsize=7.5)
    out = Path(__file__).with_suffix(".pdf")
    fig.savefig(out)
    print(out, {k: v for k, v in tot.items()})


if __name__ == "__main__":
    main()
