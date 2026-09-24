#!/usr/bin/env python3
"""Why Progent's attacks succeed in banking: the payment policy in force in each case.

Progent logs every policy it generates or updates ("policy updated: {...}") between the
per-case log lines. For every banking security case in which the attack succeeded, this
collects the policies logged in that case for the three payment tools and classifies the
case: a policy update admitted the attacker's IBAN (it read the IBAN from a tool result),
or no policy constrained the recipient at all (no recipient enum), or neither.

  python3 experiments/progent_banking.py results/progent results/progent-r2 results/progent-r3
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ATTACKER_IBANS = {"US133000000121212121212"}
PAYMENT_TOOLS = ("send_money", "schedule_transaction", "update_scheduled_transaction")
CASE = re.compile(r"\[banking\]\[(user_task_\d+)\]\[(injection_task_\d+)\]")


def policies_by_case(log: Path) -> dict[tuple[str, str], list[str]]:
    out, pending, case = defaultdict(list), [], None
    for line in log.read_text(errors="replace").splitlines():
        if "policy updated" in line or "policy reset" in line:
            pending.append(line)
            continue
        m = CASE.search(line)
        if m:
            case = (m.group(1), m.group(2))
            out[case] += pending
            pending = []
    return out


def classify(lines: list[str]) -> str:
    rules = [m.group(0) for line in lines for tool in PAYMENT_TOOLS
             for m in re.finditer(rf"'{tool}': \[\(.*?\)\]", line)]
    if any(iban in r for r in rules for iban in ATTACKER_IBANS):
        return "a policy update admitted the attacker's IBAN"
    if not rules or any("'recipient'" not in r or re.search(r"'recipient': \{(?:'type': 'string')?\}", r) for r in rules):
        return "no policy constrained the recipient"
    return "other"


def main(roots: list[str]) -> None:
    summary = {}
    for root in roots:
        root = Path(root)
        pols = policies_by_case(root / "banking.attack.log")
        succ = []
        for f in sorted(root.glob("*/banking/user_task_*/important_instructions/*.json")):
            d = json.loads(f.read_text())
            if d.get("security"):
                succ.append((d["user_task_id"], d["injection_task_id"]))
        c = Counter(classify(pols.get(k, [])) for k in succ)
        summary[root.name] = {"banking_successes": len(succ), **c}
        print(f"{root.name}: {len(succ)} banking successes: {dict(c)}")
    Path("experiments/progent_banking.json").write_text(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main(sys.argv[1:] or ["results/progent", "results/progent-r2", "results/progent-r3"])
