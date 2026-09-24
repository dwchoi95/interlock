#!/usr/bin/env python3
"""Exact McNemar test between two arms over the same 909 security cases.

Reads per-case CSVs written by export_cases.py, pairs the cases by
(suite, user_task, injection_task), keeps the 909 with in_909 true, and tests
whether the two arms differ on `security` (attack success) and on `utility`
(task success under attack). The statistic is the exact two-sided binomial
test over the discordant pairs, which is what the paper reports.

Each side may list several runs of the same configuration, comma-separated;
every pair of runs is tested and the largest p-value is reported, so that a
single favourable pair cannot carry the result.

  python3 experiments/paired_tests.py \\
      results-summary/cases/progent.attack.csv,results-summary/cases/progent-r2.attack.csv \\
      results-summary/cases/guard4.attack.csv,results-summary/cases/guard4-r2.attack.csv
"""
import csv, sys
from itertools import product
from math import comb


def load(path: str) -> dict[tuple, tuple[bool, bool]]:
    out = {}
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            if r["in_909"] == "True":
                out[(r["suite"], r["user_task"], r["injection_task"])] = (r["utility"] == "True", r["security"] == "True")
    return out


def mcnemar_exact(b: int, c: int) -> float:
    """Two-sided exact p for discordant counts b (only A) and c (only B)."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    return min(1.0, 2 * sum(comb(n, i) for i in range(k + 1)) / 2 ** n)


def main(a_files: list[str], b_files: list[str]) -> None:
    print(f"{'run A':32s} {'run B':32s} {'field':9s} {'only A':>6s} {'only B':>6s} {'p':>10s}")
    worst = {"security": 0.0, "utility": 0.0}
    for fa, fb in product(a_files, b_files):
        a, b = load(fa), load(fb)
        keys = sorted(set(a) & set(b))
        if len(keys) != 909:
            print(f"warning: {len(keys)} paired cases between {fa} and {fb}", file=sys.stderr)
        for idx, field in ((1, "security"), (0, "utility")):
            only_a = sum(a[k][idx] and not b[k][idx] for k in keys)
            only_b = sum(b[k][idx] and not a[k][idx] for k in keys)
            p = mcnemar_exact(only_a, only_b)
            worst[field] = max(worst[field], p)
            print(f"{fa.split('/')[-1]:32s} {fb.split('/')[-1]:32s} {field:9s} {only_a:6d} {only_b:6d} {p:10.3g}")
    print(f"largest p over all pairs: security {worst['security']:.3g}, utility {worst['utility']:.3g}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1].split(","), sys.argv[2].split(","))
