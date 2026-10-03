"""Recompute the paper's numbers from the frozen per-case results in this folder.

    python experiments/tables.py

Table 2 (BU, UUA, T-ASR per AgentDojo suite and overall), the exact McNemar tests of Section 5.2 and 5.4
(outcomes paired by user task or security case), Table 3 (leaks on the three real servers) and the
real-server ablations of Figure 4. No dependency beyond the standard library.
"""
import csv, json
from math import comb
from pathlib import Path

HERE = Path(__file__).resolve().parent
SUITES = ("workspace", "slack", "travel", "banking")
RQ1 = {"ND": "no-defense", "TF": "tool-filter", "PG": "progent-0.1.35", "CM": "camel", "IR": "interlock-v67c"}
RQ3 = {"w/o Narrow": "RQ3/agentdojo/no-narrow", "w/o Guard": "RQ3/agentdojo/no-guard"}


def load(stem: str, phase: str) -> dict:
    """(suite, user_task[, injection_task]) -> row, for phase 'benign' or 'attack'."""
    with open(HERE / f"{stem}.{phase}.csv", newline="") as f:
        return {(r["suite"], r["user_task"], r.get("injection_task", "")): r for r in csv.DictReader(f)}


def rate(rows: dict, field: str, suite: str | None = None) -> str:
    sel = [r[field] == "True" for k, r in rows.items() if suite in (None, k[0])]
    return f"{100 * sum(sel) / len(sel):6.2f}"


def mcnemar(a: dict, b: dict, field: str) -> tuple[int, int, float]:
    """Two-sided exact test over the discordant pairs: (only A, only B, p)."""
    keys = a.keys() & b.keys()
    x = sum(a[k][field] == "True" and b[k][field] != "True" for k in keys)
    y = sum(b[k][field] == "True" and a[k][field] != "True" for k in keys)
    n = x + y
    return x, y, min(1.0, 2 * sum(comb(n, i) for i in range(min(x, y) + 1)) / 2 ** n) if n else 1.0


def table2() -> None:
    runs = {k: (load(f"RQ1/{v}", "benign"), load(f"RQ1/{v}", "attack")) for k, v in RQ1.items()}
    print("Table 2: BU | UUA | T-ASR (%)", "  ".join(f"{k:>6s}" for k in RQ1))
    for suite in (*SUITES, None):
        for name, (i, field) in (("BU", (0, "utility")), ("UUA", (1, "utility")), ("T-ASR", (1, "security"))):
            print(f"{suite or 'Overall':10s} {name:5s}", " ".join(rate(runs[k][i], field, suite) for k in RQ1))
    print("\nExact McNemar, Interlock vs each baseline (only IR / only baseline / p)")
    for k in ("ND", "TF", "PG", "CM"):
        for name, (i, field) in (("BU", (0, "utility")), ("UUA", (1, "utility")), ("T-ASR", (1, "security"))):
            x, y, p = mcnemar(runs["IR"][i], runs[k][i], field)
            print(f"  IR vs {k} {name:5s} {x:4d} {y:4d}  p = {p:.3g}")
    print("\nRQ3 on AgentDojo, Interlock vs each variant")
    for k, v in RQ3.items():
        var = (load(v, "benign"), load(v, "attack"))
        for name, (i, field) in (("BU", (0, "utility")), ("UUA", (1, "utility")), ("T-ASR", (1, "security"))):
            x, y, p = mcnemar(runs["IR"][i], var[i], field)
            print(f"  {k:10s} {name:5s} {rate(var[i], field)}  (IR {rate(runs['IR'][i], field)})  p = {p:.3g}")


def episodes(root: Path) -> tuple[int, int, int]:
    """(leaks, user tasks solved under attack, benign runs solved) over inj1-3 and benign1-3."""
    inj = [json.loads((root / f"inj{i}.json").read_text()) for i in (1, 2, 3)]
    ben = [json.loads((root / f"benign{i}.json").read_text()) for i in (1, 2, 3)]
    return sum(bool(e["attack_success"]) for e in inj), sum(bool(e["utility"]) for e in inj), sum(bool(e["utility"]) for e in ben)


def table3() -> None:
    print("\nTable 3 and Figure 4 (real servers): leaks / UUA / BU out of 3 per server")
    for config in ("no-defense", "tool-filter", "interlock"):
        print(f"  {config:12s}", "  ".join(f"{s}: {episodes(HERE / 'RQ2' / s / config)}" for s in ("chrome", "browser", "grafana")))
    for config in ("no-summarize", "no-narrow", "no-guard"):
        print(f"  {config:12s}", "  ".join(f"{s}: {episodes(HERE / 'RQ3/realserver' / s / config)}" for s in ("chrome", "browser", "grafana")))


if __name__ == "__main__":
    table2()
    table3()
