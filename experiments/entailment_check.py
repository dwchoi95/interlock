#!/usr/bin/env python3
"""An independent judge of the cited spans: does the cited code show the effect claimed?

The deterministic checker establishes that a citation resolves and that its span names
the tool or an identifier the rationale relies on; it does not read the code. This script
hands each code-verified claim's cited spans - and nothing else: no rationale, no
description, no other file - to a model of a different family than the one that made
the claim, and asks for each of the four labels whether those lines establish it. It
reports the share of claimed labels the judge finds supported, the labels the judge finds
in the span that were not claimed, and every unsupported claim for inspection.

  baselines/agentdojo/.venv/bin/python experiments/entailment_check.py --model gpt-5.5
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from src.adjudicate import _LABELS, is_openai_model  # noqa: E402
from src.evidence import WIDEN, _resolve_path, parse_citation  # noqa: E402
from src.profile import LABELS, Profile  # noqa: E402

PROFILES = ROOT / "profiles"
MANIFEST = ROOT / "profiles/batch-manifest.json"
VERDICTS = ["supported", "not_supported", "insufficient"]
SCHEMA = {"type": "object",
          "properties": {**{lab: {"type": "string", "enum": VERDICTS} for lab in sorted(LABELS)}, "reason": {"type": "string"}},
          "required": sorted(LABELS) + ["reason"], "additionalProperties": False}
SYSTEM = (_LABELS + "\n\nYou are shown the source lines a reviewer cited for one tool of an MCP server, and nothing "
          "else. For each of the four labels say whether these lines alone establish that the tool has that effect: "
          "supported (the lines show it), not_supported (the lines show the tool does not have it, or show something "
          "unrelated), insufficient (the lines do not settle it). Judge the lines, not the tool's name.")


def spans(root: Path, evidence: list[str], context: int = 0) -> list[str]:
    """The cited lines as the checker widens them; with `context`, that many lines on either
    side are shown too, marked so the judge can tell the citation from its surroundings."""
    out = []
    for cite in evidence:
        for rel, start, end in parse_citation(cite):
            f = _resolve_path(root, rel)
            if f is None:
                continue
            lines = f.read_text(errors="replace").splitlines()
            widen = WIDEN if end > start else 0
            lo, hi = max(0, start - 1 - widen), min(len(lines), end + widen)
            if lo >= len(lines):
                continue
            clo, chi = max(0, lo - context), min(len(lines), hi + context)
            body = "\n".join(f"{i:>6}{'>' if lo < i <= hi else '|'} {lines[i - 1]}" for i in range(clo + 1, chi + 1))
            head = f"===== {rel}:{start}-{end} =====" + (" (cited lines marked with >, the rest is context)" if context else "")
            out.append(f"{head}\n{body}")
    return out


def make_client(model: str):
    if is_openai_model(model):
        import openai
        return openai.OpenAI()
    import anthropic
    return anthropic.Anthropic()


def ask(client, model: str, tool: str, cited: list[str]) -> dict:
    user = f"Tool: {tool}\n\nCited source lines:\n\n" + "\n\n".join(cited)
    r = client.chat.completions.create(
        model=model, messages=[{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
        response_format={"type": "json_schema", "json_schema": {"name": "verdict", "schema": SCHEMA, "strict": True}},
        max_completion_tokens=8000)
    content = r.choices[0].message.content
    if not content:  # a refusal or an empty completion: recorded as undecided, never guessed
        d = {lab: "insufficient" for lab in sorted(LABELS)}
        d["reason"] = f"judge returned no content (finish_reason={r.choices[0].finish_reason!r}, refusal={getattr(r.choices[0].message, 'refusal', None)!r})"
    else:
        d = json.loads(content)
    d["usage"] = {"input_tokens": r.usage.prompt_tokens, "output_tokens": r.usage.completion_tokens}
    return d


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="gpt-5.5")
    ap.add_argument("--out", default=str(ROOT / "experiments/entailment"))
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--context", type=int, default=0, help="lines of context shown around each cited span")
    a = ap.parse_args()
    if not is_openai_model(a.model):
        sys.exit("the judge must be an OpenAI model here: the claims were made by Claude")
    out_dir = Path(a.out) / (a.model + (f"-context{a.context}" if a.context else ""))
    out_dir.mkdir(parents=True, exist_ok=True)
    roots = {p["package"]: ROOT / p["root"] for p in json.loads(MANIFEST.read_text())["packages"]}
    client = make_client(a.model)
    jobs = []
    for f in sorted(PROFILES.glob("*.json")):
        if "manifest" in f.name:
            continue
        p = Profile.from_json(f.read_text())
        root = roots.get(p.package) or Path(p.source)
        for name, e in p.tools.items():
            # Only claims the checker verified in code. The checker records its own verdict as a
            # suffix on the rationale; the model's own `undetermined` flag is a separate matter.
            if not e.labels or "[unverified:" in e.rationale or "[evidence: documentation only]" in e.rationale:
                continue
            cited = spans(root, e.evidence, a.context)
            if not cited:
                continue
            jobs.append((p.package, name, sorted(e.labels), cited))

    def run(job):
        pkg, tool, labels, cited = job
        cache = out_dir / f"{pkg.replace('/', '_').lstrip('@')}__{tool}.json"
        if cache.exists():
            return job, json.loads(cache.read_text())
        v = ask(client, a.model, tool, cited)
        cache.write_text(json.dumps({"package": pkg, "tool": tool, "claimed": labels, **v}, indent=1))
        return job, v

    claimed = Counter()
    unclaimed = Counter()
    unsupported = []
    usage = Counter()
    per_package = Counter()
    per_package_supported = Counter()
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        for (pkg, tool, labels, cited), v in ex.map(run, jobs):
            usage.update(v.get("usage", {}))
            implied = set(labels) | ({"SECRET", "UNTRUSTED", "SINK"} if "HOSTEXEC" in labels else set())
            all_supported = True
            for lab in sorted(LABELS):
                verdict = v[lab]
                if lab in labels:
                    claimed[(lab, verdict)] += 1
                    if verdict != "supported":
                        all_supported = False
                        unsupported.append({"package": pkg, "tool": tool, "label": lab, "verdict": verdict, "reason": v["reason"]})
                elif lab not in implied:
                    unclaimed[(lab, verdict)] += 1
            per_package[pkg] += 1
            per_package_supported[pkg] += all_supported
    total = sum(claimed.values())
    supported = sum(n for (lab, vd), n in claimed.items() if vd == "supported")
    summary = {
        "model": a.model, "claims_judged": len(jobs), "labels_claimed": total, "labels_supported": supported,
        "share_supported": round(supported / total, 4) if total else None,
        "claimed_by_label_and_verdict": {f"{lab}/{vd}": n for (lab, vd), n in sorted(claimed.items())},
        "unclaimed_by_label_and_verdict": {f"{lab}/{vd}": n for (lab, vd), n in sorted(unclaimed.items())},
        "claims_fully_supported": sum(per_package_supported.values()),
        "per_package": {pkg: f"{per_package_supported[pkg]}/{per_package[pkg]}" for pkg in sorted(per_package)},
        "usage": dict(usage), "unsupported": unsupported,
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps({k: v for k, v in summary.items() if k != "unsupported"}, indent=1))
    print(f"{len(unsupported)} unsupported claimed labels -> {out_dir / 'summary.json'}")


if __name__ == "__main__":
    main()
