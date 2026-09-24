#!/usr/bin/env python3
"""Where two label sets part ways in enforcement, case by case.

Replays the undefended trajectories under a reference label file and a candidate label
file with one guard rule, and lists every attack the reference stops and the candidate
lets through, and every solved benign task the candidate blocks and the reference does
not. For each such case it names the call and, for each destination value, the result
fields it was found in and how each label set classified them - which is what decides
the outcome. Also classifies the candidate's misses of attacker-writable fields against
the reference entries: a record the agent or the user wrote (an echo of the agent's own
argument, a sent email, a draft), a representation difference ('*' against 'return' or
'return.values'), or a
field an outside party authors.

  baselines/agentdojo/.venv/bin/python experiments/label_gap.py \\
      experiments/effects/agentdojo.v4.json experiments/effects/agentdojo.summarize-gpt-5.5.json
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import offline_eval as oe  # noqa: E402
from guard import ANAMED, IDENT, NAMED, PROSE, UNTRUSTED, Effects, _candidates, _contains, _field, _norm  # noqa: E402

import yaml  # noqa: E402


def keyed_leaves(text: str, inj: set, idn: set, nmd: set) -> list[tuple[str, str, str]]:
    """(field, class, text) for every scalar of a tool result, classified as guard.result_leaves does."""
    if "return" in inj and "return" not in idn:
        return [("return", UNTRUSTED, text)]
    try:
        parsed = yaml.safe_load(text)
    except yaml.YAMLError:
        parsed = None
    out: list = []

    def walk(obj, key):
        every = "*" in inj
        if isinstance(obj, dict):
            for k, v in obj.items():
                out.append((f"{key}.<key>" if key else "<key>", IDENT, str(k)))
                walk(v, str(k))
        elif isinstance(obj, list):
            for v in obj:
                walk(v, key)
        elif obj is not None:
            i = every or key in inj or (key is None and "return" in inj)
            d = key in idn or (key is None and "return" in idn)
            n = key in nmd or (key is None and "return" in nmd)
            cls = ANAMED if n else NAMED if i and d else UNTRUSTED if i else IDENT if d else PROSE
            out.append((key or "return", cls, str(obj)))
    if isinstance(parsed, (dict, list)):
        walk(parsed, None)
        return out
    if "return" in idn:
        return [("return", ANAMED if "return" in nmd else NAMED if "return" in inj else IDENT, text)]
    return [("return", UNTRUSTED if inj else PROSE, text)]


def where(rec: dict, value, fx: Effects) -> list[str]:
    """The result fields a value (in any of the guard's stripped forms) was found in."""
    forms = _candidates(_norm(value))
    hits = []
    for m in rec["messages"]:
        if m["role"] != "tool":
            continue
        t = m["tool_call"]["function"]
        for key, cls, s in keyed_leaves(oe.text(m["content"]), fx.injectable_fields(t), fx.ident_fields(t), fx.named_fields(t)):
            if any(v == _norm(s) or (len(v) >= 3 and _contains(s.lower(), v)) for v in forms):
                hits.append(f"{t}.{key}={cls}")
    return sorted(set(hits))


def case_path(run: dict) -> Path:
    if run["iid"] is None:
        return oe.LOGS / run["suite"] / run["uid"] / "none" / "none.json"
    return oe.LOGS / run["suite"] / run["uid"] / "important_instructions" / f"{run['iid']}.json"


def explain(run_ref, run_cand, rule, fx_ref, fx_cand) -> list[dict]:
    rec = json.loads(case_path(run_ref).read_text())
    out = []
    for c_ref, c_cand in zip(run_ref["calls"], run_cand["calls"]):
        pass  # calls may differ in number when steerability differs; match by tool and order below
    ref_blocked = oe.blocked(run_ref, rule)
    cand_blocked = oe.blocked(run_cand, rule)
    for c in ref_blocked + cand_blocked:
        item = {"tool": c["tool"], "blocked_by": "reference" if c in ref_blocked else "candidate",
                "steerable_ref": fx_ref.steerable(c["tool"]), "steerable_cand": fx_cand.steerable(c["tool"]),
                "in_ground_truth": c["tool"] in run_ref["gt"], "destinations": []}
        for a, v, srcs in c["dests"]:
            item["destinations"].append({"arg": a, "value": str(v)[:60], "sources": sorted(srcs["default"]),
                                         "found_ref": where(rec, v, fx_ref), "found_cand": where(rec, v, fx_cand)})
        out.append(item)
    return out


def field_misses(ref_path: Path, cand_path: Path) -> dict:
    ref, cand = json.loads(ref_path.read_text()), json.loads(cand_path.read_text())
    cats, rows = Counter(), []
    for suite, v in ref.items():
        got = {t["name"]: t for t in cand[suite]["tools"]}
        for t in v["tools"]:
            g = got.get(t["name"])
            if g is None:
                continue
            raw = {_field(e): e for e in t.get("injectable_output_fields", [])}
            have = {_field(e) for e in g.get("injectable_output_fields", [])}
            for f, entry in raw.items():
                if f in have:
                    continue
                low = entry.lower()
                if any(k in low for k in ("echo", "previously sent", "sent emails", "attached by")) or t["name"] in ("get_sent_emails", "get_draft_emails"):
                    # the record is one the agent or the user wrote: its text is attacker text only
                    # if the agent copied some in, which is taint carried through a write
                    cat = "carried through the agent's or user's own writes"
                elif f == "*" and have & {"return", "values"}:  # _field("return.values") is "values"
                    # the mapping's values are marked, under a name the guard does not read as '*'
                    cat = "representation ('*' against 'return' or 'return.values')"
                else:
                    cat = "field an outside party authors"
                cats[cat] += 1
                rows.append({"suite": suite, "tool": t["name"], "field": f, "category": cat, "reference_entry": entry[:160]})
    return {"missed_by_category": dict(cats), "missed": rows}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("reference")
    ap.add_argument("candidate")
    ap.add_argument("--rule", default="T-strict+W-oracle")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    ref_p, cand_p = Path(a.reference).resolve(), Path(a.candidate).resolve()
    b_ref, at_ref = oe.load_runs(ref_p)
    b_cand, at_cand = oe.load_runs(cand_p)
    idx = lambda runs: {(r["suite"], r["uid"], r["iid"]): r for r in runs}
    ar, ac, br, bc = idx(at_ref), idx(at_cand), idx(b_ref), idx(b_cand)
    fx = {s: (Effects(ref_p, s), Effects(cand_p, s)) for s in oe.SUITES}
    admitted, stopped, lost, regained = [], [], [], []
    for k, r in ar.items():
        if not r["security"]:
            continue
        rb, cb = bool(oe.blocked(r, a.rule)), bool(oe.blocked(ac[k], a.rule))
        if rb and not cb:
            admitted.append({"case": "/".join(k), "calls": explain(r, ac[k], a.rule, *fx[k[0]])})
        if cb and not rb:
            stopped.append({"case": "/".join(k), "calls": explain(r, ac[k], a.rule, *fx[k[0]])})
    for k, r in br.items():
        if not r["utility"]:
            continue
        rb, cb = bool(oe.blocked(r, a.rule)), bool(oe.blocked(bc[k], a.rule))
        if cb and not rb:
            lost.append({"case": "/".join(str(x) for x in k), "calls": explain(r, bc[k], a.rule, *fx[k[0]])})
        if rb and not cb:
            regained.append({"case": "/".join(str(x) for x in k), "calls": explain(r, bc[k], a.rule, *fx[k[0]])})
    out = {"reference": str(ref_p.relative_to(oe.ROOT)), "candidate": str(cand_p.relative_to(oe.ROOT)), "rule": a.rule,
           "attacks_admitted_by_candidate": admitted, "attacks_stopped_only_by_candidate": stopped,
           "benign_lost_by_candidate": lost, "benign_regained_by_candidate": regained,
           "field_misses": field_misses(ref_p, cand_p)}
    dest = Path(a.out) if a.out else oe.ROOT / f"experiments/label_gap.{cand_p.stem}.json"
    dest.write_text(json.dumps(out, indent=1))
    print(f"{a.rule}: attacks admitted {len(admitted)}, stopped only by candidate {len(stopped)}; "
          f"benign lost {len(lost)}, regained {len(regained)}")
    print("attacker-writable fields missed, by category:", out["field_misses"]["missed_by_category"])
    for x in admitted:
        print(" admitted", x["case"])
        for c in x["calls"]:
            for d in c["destinations"]:
                print(f"    {c['tool']}({d['arg']}={d['value']!r}) by={c['blocked_by']} steer ref/cand={c['steerable_ref']}/{c['steerable_cand']} "
                      f"ref:{d['found_ref'][:3]} cand:{d['found_cand'][:3]}")
            if not c["destinations"]:
                print(f"    {c['tool']}() by={c['blocked_by']} steer ref/cand={c['steerable_ref']}/{c['steerable_cand']} in_gt={c['in_ground_truth']}")
    print(f"-> {dest}")


if __name__ == "__main__":
    main()
