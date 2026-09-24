#!/usr/bin/env python3
"""Profile every package of a list with one model, into one output directory.

The batch subcommand of src/cli.py is Anthropic-only (the Batches API); this driver runs
`profile` per package instead, so that the same 34 servers can be summarized with another
model, at the same versions, with the same rubric and checker. Packages already present in
the output directory are skipped, so an interrupted run resumes.

  baselines/agentdojo/.venv/bin/python experiments/profile_all.py --model gpt-5.5 --out profiles-gpt-5.5
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "profiles/batch-manifest.json"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--workers", type=int, default=3)
    a = ap.parse_args()
    out = ROOT / a.out
    out.mkdir(parents=True, exist_ok=True)
    packages = json.loads(MANIFEST.read_text())["packages"]

    def run(p: dict) -> tuple[str, int, str]:
        slug = f"{p['kind']}_{p['package'].replace('/', '_').lstrip('@')}_{p['version']}"
        if (out / f"{slug}.json").exists():
            return p["package"], 0, "cached"
        cmd = [sys.executable, "-m", "src.cli", "profile", f"{p['kind']}:{p['package']}", "--version", p["version"],
               "--model", a.model, "--out", str(out)]
        r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
        (out / f"{slug}.log").write_text(r.stdout + r.stderr)
        return p["package"], r.returncode, (r.stdout.strip().splitlines() or [""])[-1][:200] if r.returncode == 0 else (r.stderr.strip().splitlines() or [""])[-1][:200]

    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        for pkg, code, line in ex.map(run, packages):
            print(f"{pkg}: exit {code}: {line}", flush=True)


if __name__ == "__main__":
    main()
