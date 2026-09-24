#!/usr/bin/env python3
"""How much of the text-versus-code disagreement survives the model's own run-to-run noise.

Inputs are repeated judgments of the same 433 tools: description-only runs (the rubric
over name, description, schema and annotations; directories written by
description_vs_code.py) and code runs (profile directories written by src/cli.py). Every
comparison is made on the four labels with HOSTEXEC expanded, per tool, exactly as
description_vs_code.py compares them.

Reported:
  - exact per-tool agreement text-text, code-code, and text-code (mean over run pairs);
  - the stable subset: tools on which every text run agrees with every other text run
    and every code run with every other code run; on it, the text-versus-code split into
    agree / text hides an effect / text claims one the code lacks / both. A disagreement
    there cannot be the model disagreeing with itself.

  python3 experiments/self_agreement.py \\
      --text experiments/description_vs_code-r2/gpt-5.5 experiments/description_vs_code-r3/gpt-5.5 \\
      --code profiles-gpt-5.5 profiles-gpt-5.5-r2 --out experiments/self_agreement.gpt-5.5.json
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from itertools import combinations, product
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from src.profile import Profile, expand  # noqa: E402

ALL = ("SECRET", "UNTRUSTED", "SINK", "HOSTEXEC")


def slug(p: Profile) -> str:
    return f"{p.kind}_{p.package.replace('/', '_').lstrip('@')}_{p.version}"


def checker_verified(rationale: str) -> bool:
    return "[unverified:" not in rationale and "[evidence: documentation only]" not in rationale


def load_code(d: Path) -> dict[tuple[str, str], tuple[frozenset, bool]]:
    out = {}
    for f in sorted(d.glob("*.json")):
        if "manifest" in f.name:
            continue
        p = Profile.from_json(f.read_text())
        for name, e in p.tools.items():
            out[(slug(p), name)] = (frozenset(expand(set(e.labels))), checker_verified(e.rationale))
    return out


def load_text(d: Path, keys: set[str]) -> dict[tuple[str, str], frozenset]:
    out = {}
    for s in keys:
        f = d / f"{s}.json"
        if not f.exists():
            continue
        raw = json.loads(f.read_text())
        for name, j in raw["tools"].items():
            out[(s, name)] = frozenset(expand(set(j.get("labels", []))))
    return out


def split(code: frozenset, text: frozenset) -> str:
    if code == text:
        return "agree"
    if text < code:
        return "hides"
    if code < text:
        return "overstates"
    return "both"


def rate(pairs: list[tuple[frozenset, frozenset]]) -> float:
    return round(sum(a == b for a, b in pairs) / len(pairs), 4) if pairs else float("nan")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--text", nargs="+", required=True, help="description-only judgment directories")
    ap.add_argument("--code", nargs="+", required=True, help="code profile directories")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    codes = [load_code(ROOT / d) for d in a.code]
    slugs = {s for c in codes for s, _ in c}
    texts = [load_text(ROOT / d, slugs) for d in a.text]
    keys = set(codes[0])
    for c in codes[1:]:
        keys &= set(c)
    for t in texts:
        keys &= set(t)
    keys = sorted(keys)

    tt = [rate([(ta[k], tb[k]) for k in keys]) for ta, tb in combinations(texts, 2)]
    cc = [rate([(ca[k][0], cb[k][0]) for k in keys]) for ca, cb in combinations(codes, 2)]
    tc = [rate([(t[k], c[k][0]) for k in keys]) for t, c in product(texts, codes)]

    def stable(k):
        return all(t[k] == texts[0][k] for t in texts) and all(c[k][0] == codes[0][k][0] for c in codes)

    def breakdown(sel):
        n = len(sel)
        cnt = Counter(split(codes[0][k][0], texts[0][k]) for k in sel)
        hidden = Counter(lab for k in sel for lab in (codes[0][k][0] - texts[0][k]))
        over = Counter(lab for k in sel for lab in (texts[0][k] - codes[0][k][0]))
        return {"tools": n, **{c: cnt[c] for c in ("agree", "hides", "overstates", "both")},
                **{f"{c}_pct": round(100 * cnt[c] / n, 1) if n else None for c in ("agree", "hides", "overstates", "both")},
                "hidden_by_label": dict(hidden), "overstated_by_label": dict(over)}

    st = [k for k in keys if stable(k)]
    st_verified = [k for k in st if all(c[k][1] for c in codes)]
    per_run_pair = []
    for (i, t), (j, c) in product(enumerate(texts), enumerate(codes)):
        cnt = Counter(split(c[k][0], t[k]) for k in keys)
        per_run_pair.append({"text_run": a.text[i], "code_run": a.code[j],
                             **{x: round(100 * cnt[x] / len(keys), 1) for x in ("agree", "hides", "overstates", "both")}})
    out = {"tools_compared": len(keys), "text_runs": a.text, "code_runs": a.code,
           "agreement_text_text": tt, "agreement_code_code": cc, "agreement_text_code": tc,
           "text_code_by_run_pair": per_run_pair,
           "stable": breakdown(st), "stable_and_code_verified_in_every_run": breakdown(st_verified),
           "stable_hides_examples": [f"{s}:{n} code={sorted(codes[0][(s, n)][0])} text={sorted(texts[0][(s, n)])}"
                                     for s, n in st if split(codes[0][(s, n)][0], texts[0][(s, n)]) == "hides"][:40]}
    (ROOT / a.out).write_text(json.dumps(out, indent=1))
    print(json.dumps({k: v for k, v in out.items() if k != "stable_hides_examples"}, indent=1))


if __name__ == "__main__":
    main()
