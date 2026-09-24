"""Does Summarize's reading of banking read_file follow the threat model, and not the examples in it?

Runs Summarize (GPT-5.5, the tool code only) on AgentDojo's banking suite under three rubrics:
the default threat model, the received-documents variant, and that variant without its list of
example documents (bills, invoices, notices, ...), which names the kinds of files AgentDojo plants
its injections in. Records whether read_file's result is marked attacker-writable in each run.

  baselines/agentdojo/.venv/bin/python experiments/threat_model_check.py <default|with-examples|no-examples> <runs>
"""
import json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "experiments"))
from summarize_agentdojo import make_client, source_files, surfaces  # noqa: E402
from src.adjudicate import adjudicate, rubric  # noqa: E402

OUT = ROOT / "experiments/threat_model_check.json"
EXAMPLES = " (bills, invoices, notices, shared or\ndownloaded files, attachments)"
VARIANTS = {"default": rubric(), "with-examples": rubric("received-documents"),
            "no-examples": rubric("received-documents").replace(EXAMPLES, "")}
assert EXAMPLES in VARIANTS["with-examples"] and EXAMPLES not in VARIANTS["no-examples"]


def main(which: str, runs: int) -> None:
    client, surface, files = make_client("gpt-5.5"), surfaces()["banking"], source_files()
    record = json.loads(OUT.read_text()) if OUT.exists() else {}
    for _ in range(runs):
        raw = adjudicate(surface, files, client=client, model="gpt-5.5", rubric=VARIANTS[which])
        rf = raw["tools"]["read_file"]
        record.setdefault(which, []).append({"injectable_output_fields": rf.get("injectable_output_fields", []),
                                             "rationale": rf.get("rationale", "")})
        print(which, rf.get("injectable_output_fields", []), flush=True)
    OUT.write_text(json.dumps(record, indent=1) + "\n")
    for k, v in record.items():
        print(f"{k}: read_file attacker-writable in {sum(bool(r['injectable_output_fields']) for r in v)} of {len(v)} runs")


if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]))
