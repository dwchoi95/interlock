#!/usr/bin/env python3
"""E1 (paper Section 2.2): hidden effects, and the sheet two authors audit.

A tool has a hidden effect when its code-side labels (Summarize) hold one its definition-side labels (the same
rubric over the tool definition alone) lack, HOSTEXEC expanded. A tool Summarize left unjudged carries the
checker's HOSTEXEC fallback, not a claim of the model, so it is counted apart and never flagged.

Writes <out>/e1.json (counts per benchmark), <out>/e1_audit_annotator1.csv and <out>/e1_audit_annotator2.csv
(identical at first: one row per flagged tool with its definition, the cited code, both label sets and a blank
"Hidden Effect" cell) and <out>/e1_audit.md, the guide to filling them.

  python run.py analyze e1-audit   (or python -m src.analysis.e1_audit)
"""
from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
from src.summarize.profile import Profile, expand  # noqa: E402

MODEL = "gpt-4o-2024-08-06"
MAX_LINES = 40

GUIDE = """# E1 감사 안내

감사자마다 자기 파일 하나를 채운다: 감사자 1은 `e1_audit_annotator1.csv`, 감사자 2는 `e1_audit_annotator2.csv`.
두 파일은 처음에 똑같고, 서로의 답을 보지 않고 채운다. 파일마다 {n}행(MCPWild {m}, AgentDojo {a})이다.

## 무엇을 판단하나

행마다 **Hidden Effect** 칸 하나에 `yes` 또는 `no`를 적는다.

`yes`는 **Hidden Labels** 가운데 적어도 하나가 다음 두 조건을 모두 만족할 때다.
1. **코드에 있다**: Code(와 필요하면 소스)를 보면 도구가 정말 그 효과를 가진다.
2. **정의로는 알 수 없다**: Description(설명·인자·annotation)만 읽은 사람이 그 효과를 합리적으로 짐작할 수 없다.

둘 중 하나라도 아니면 `no`. 판단이 어려우면 `no`로 두지 말고 칸을 비운 채 메모를 남긴다(아래 참고).

## 칼럼

| 칼럼 | 내용 |
|---|---|
| Benchmark | `MCPWild` 또는 `AgentDojo` |
| Server | 서버 패키지@버전 (AgentDojo는 suite) |
| Tool | 도구 이름 |
| Description | 도구 정의: 설명, 인자와 그 설명, annotation. 정의만 읽은 판정이 본 전부다 |
| Code | 코드를 읽은 판정이 근거로 인용한 코드 줄(`경로:줄` 머리와 줄 번호 포함, 인용마다 앞 {lines}행까지). 첫 줄에 소스 폴더와 인용 확인 여부가 있다 |
| Definition Labels | 정의만 읽은 판정의 라벨 (비어 있으면 없음) |
| Code Labels | 코드를 읽은 판정의 라벨 |
| Hidden Labels | 코드 쪽에만 있는 라벨. 이 행이 감사 대상인 이유 |
| Hidden Effect | **감사자가 채움**: `yes` / `no` |

## 라벨 (위협 모델: 내용을 누가 썼는가로 가른다)

- **SECRET**: 사용자의 비공개 데이터를 읽는다.
- **UNTRUSTED**: 공격자가 쓸 수 있는 내용(웹, 다른 사람이 쓴 메일·이슈·파일 등)을 컨텍스트로 가져온다.
- **SINK**: 공격자가 읽을 수 있는 곳으로 데이터를 보낸다.
- **HOSTEXEC**: 호스트에서 코드를 실행한다(나머지 셋을 함의).

## 요령

- Code 첫 줄이 `citation: unverified`이면 인용된 줄이 도구의 핸들러가 아닐 수 있다. 그 행은 소스 폴더에서 도구 이름으로 핸들러를 찾아 판단한다.
  소스 폴더(`.cache/sources/...`)는 저장소에 없으므로 이 컴퓨터에서 연다.
- 예: `get_file_contents`(github-mcp-server)는 사용자 토큰으로 비공개 저장소를 읽는데 설명은 "Get the contents of a file"뿐이다 → `yes`.
  `playwright_get`은 응답 본문을 돌려주지만 "Perform an HTTP GET request"에서 짐작할 수 있다 → `no`.
- 메모가 필요하면 Hidden Effect 오른쪽에 칸을 하나 더 만들어 적는다. 집계는 Hidden Effect만 읽는다.
"""


def surfaces() -> dict[tuple[str, str], dict]:
    out = {}
    for line in (ROOT / "benchmark/mcpwild/surfaces.jsonl").open():
        r = json.loads(line)
        out.update({(r["pkg"], t["name"]): t for t in r["tools"]})
    try:
        from src.analysis.summarize_agentdojo import surfaces as ad
        for suite, s in ad().items():
            out.update({(f"agentdojo/{suite}", t["name"]): t for t in s["tools"]})
    except Exception:  # AgentDojo's runtime needs yaml: read the tool docstrings straight from its source
        import ast
        for f in (ROOT / "baselines/agentdojo/src/agentdojo/default_suites/v1/tools").glob("*.py"):
            for n in ast.walk(ast.parse(f.read_text())):
                if isinstance(n, ast.FunctionDef) and ast.get_docstring(n):
                    doc = ast.get_docstring(n)
                    args = dict(re.findall(r":param (\w+): (.+)", doc))
                    t = {"description": doc.split(":param")[0].strip(),
                         "inputSchema": {"properties": {k: {"description": v} for k, v in args.items()}}}
                    for suite in ("banking", "slack", "travel", "workspace"):
                        out.setdefault((f"agentdojo/{suite}", n.name), t)
    return out


def snippet(root: Path | None, evidence: str) -> str:
    parts = []
    for ev in filter(None, (e.strip() for e in evidence.split(";"))):
        ev = re.sub(r"^=+\s*(.+?)\s*=+", r"\1", ev)  # the model sometimes cites the "===== file =====" header
        m = re.match(r"(.+?):(\d+)(?:-(\d+))?", ev)
        if not (m and root and (root / m[1]).is_file()):
            parts.append(f"== {ev}: 파일을 찾지 못함")
            continue
        a, b = int(m[2]), int(m[3] or m[2])
        lines = (root / m[1]).read_text(errors="replace").splitlines()
        shown = range(max(a - 3, 1), min(b + 3, a + MAX_LINES, len(lines)) + 1)
        body = "\n".join(f"{i:5d}  {lines[i - 1]}" for i in shown)
        more = f"\n(인용 {a}-{b}행 중 앞 {MAX_LINES}행만 표시)" if b - a > MAX_LINES else ""
        parts.append(f"== {ev}\n{body}{more}")
    return ("\n\n".join(parts) or "(인용 없음)")[:30_000]  # a spreadsheet cell holds 32,767 characters


def definition(t: dict | None) -> str:
    if not t:
        return "(정의를 찾지 못함: Code의 docstring 참고)"
    props = (t.get("inputSchema") or t.get("input_schema") or t.get("parameters") or {}).get("properties", {})
    args = "\n".join(f"  {k}: {(v or {}).get('description', '')}" for k, v in props.items()) or "  (없음)"
    ann = t.get("annotations") or {}
    return (f"{t.get('description') or '(설명 없음)'}\n\n인자:\n{args}"
            + (f"\n\nannotation: {json.dumps(ann, ensure_ascii=False)}" if ann else ""))


COLUMNS = ["Benchmark", "Server", "Tool", "Description", "Code", "Definition Labels", "Code Labels",
           "Hidden Labels", "Hidden Effect"]




def slug(p: Profile) -> str:
    return f"{p.kind}_{p.package.replace('/', '_').lstrip('@')}_{p.version}"


def unjudged(e) -> bool:
    return e.rationale.startswith("not judged")


def compare(pairs: list[tuple], defs: dict | None = None) -> tuple[dict, list[dict]]:
    """pairs: (benchmark, server, tool, code effect, definition labels, source root) -> counts, audit rows."""
    n, hidden_by, over_by, rows = Counter(), Counter(), Counter(), []
    defs = defs or {}
    for bench, server, tool, e, dlabels, root in pairs:
        n["tools"] += 1
        if unjudged(e):
            n["unjudged"] += 1
            continue
        c, d = expand(set(e.labels)), expand(set(dlabels))
        hidden, over = sorted(c - d), sorted(d - c)
        n["hidden" if hidden else ("overstated_only" if over else "agree")] += 1
        hidden_by.update(hidden)
        over_by.update(over)
        if hidden:
            n["hidden_code_verified"] += not e.undetermined
            pkg = server.rsplit("@", 1)[0] if bench == "MCPWild" else server
            head = (f"source: {Path(root).relative_to(ROOT) if root else '?'}\n"
                    f"citation: {'unverified' if e.undetermined else 'verified'}\n\n")
            rows.append({"Benchmark": bench, "Server": server, "Tool": tool,
                         "Description": definition(defs.get((pkg, tool))),
                         "Code": head + snippet(Path(root) if root else None, "; ".join(e.evidence)),
                         "Definition Labels": " ".join(sorted(dlabels)), "Code Labels": " ".join(sorted(e.labels)),
                         "Hidden Labels": " ".join(hidden), "Hidden Effect": ""})
    return {**n, "hidden_by_label": dict(hidden_by), "overstated_by_label": dict(over_by)}, rows


def mcpwild(code_dir: Path, def_dir: Path) -> list[tuple]:
    out = []
    for f in sorted(code_dir.glob("*.json")):
        if f.name.endswith(".raw.json") or "manifest" in f.name:
            continue   # the judge's raw answers and the batch manifest are not profiles
        p = Profile.from_json(f.read_text())
        judged = json.loads((def_dir / f"{slug(p)}.json").read_text())["tools"]
        for name, e in p.tools.items():
            d = judged.get(name, {}).get("labels", [])
            out.append(("MCPWild", f"{p.package}@{p.version}", name, e, set(d), ROOT / p.source))
    return out


def agentdojo(code_dir: Path, def_dir: Path) -> list[tuple]:
    out = []
    for f in sorted(code_dir.glob("*.profile.json")):
        p = Profile.from_json(f.read_text())
        d = Profile.from_json((def_dir / f.name).read_text())
        for name, e in p.tools.items():
            de = d.tools.get(name)
            out.append(("AgentDojo", p.package, name, e, set(de.labels) if de else set(), ROOT / p.source))
    return out


CONSENSUS_COLUMNS = ["Benchmark", "Server", "Tool", "Hidden Labels", "Annotator 1", "Annotator 2", "Consensus"]


def kappa(pairs: list[tuple[str, str]]) -> float:
    """Cohen's kappa of two annotators' yes/no answers."""
    n = len(pairs)
    po = sum(a == b for a, b in pairs) / n
    pa, pb = (sum(x[k] == "yes" for x in pairs) / n for k in (0, 1))
    pe = pa * pb + (1 - pa) * (1 - pb)
    return (po - pe) / (1 - pe) if pe < 1 else 1.0


def audit_summary(out: Path) -> dict | None:
    """Both annotators' answers -> confirmed counts (both yes), disagreements and kappa, or None if unfinished."""
    sheets = [list(csv.DictReader((out / f"e1_audit_annotator{k}.csv").open(encoding="utf-8-sig"))) for k in (1, 2)]
    if not all(r["Hidden Effect"].strip() for s in sheets for r in s):
        return None
    rows = [(a, a["Hidden Effect"].strip().lower(), b["Hidden Effect"].strip().lower()) for a, b in zip(*sheets)]

    def tally(sel):
        pairs = [(x, y) for r, x, y in rows if sel(r)]
        if not pairs:
            return {"flagged": 0}
        return {"flagged": len(pairs), "both_yes": sum(x == y == "yes" for x, y in pairs),
                "both_no": sum(x == y == "no" for x, y in pairs), "disagree": sum(x != y for x, y in pairs),
                "agreement": round(sum(x == y for x, y in pairs) / len(pairs), 3), "kappa": round(kappa(pairs), 3)}

    summary = {"all": tally(lambda r: True)}
    for bench in ("MCPWild", "AgentDojo"):
        summary[bench] = tally(lambda r, b=bench: r["Benchmark"] == b)
        summary[bench]["by_hidden_label"] = {lab: tally(lambda r, b=bench, l=lab: r["Benchmark"] == b
                                                        and l in r["Hidden Labels"].split())
                                             for lab in ("SECRET", "UNTRUSTED", "SINK", "HOSTEXEC")}
        summary[bench]["by_citation"] = {c: tally(lambda r, b=bench, c=c: r["Benchmark"] == b
                                                  and f"citation: {c}" in r["Code"]) for c in ("verified", "unverified")}
    summary["confirmed_by_server"] = dict(Counter(r["Server"] for r, x, y in rows if x == y == "yes").most_common())
    summary["disagreements"] = [{"server": r["Server"], "tool": r["Tool"], "hidden": r["Hidden Labels"],
                                 "annotator1": x, "annotator2": y} for r, x, y in rows if x != y]
    # Preserve the independent answers for kappa. Explicit consensus decisions also
    # cover later source reviews of rows where both annotators originally agreed.
    sheet = out / "e1_audit_consensus.csv"
    if not sheet.exists():
        with sheet.open("w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=CONSENSUS_COLUMNS)
            w.writeheader()
            w.writerows({"Benchmark": r["Benchmark"], "Server": r["Server"], "Tool": r["Tool"],
                         "Hidden Labels": r["Hidden Labels"], "Annotator 1": x, "Annotator 2": y, "Consensus": ""}
                        for r, x, y in rows if x != y)
    agreed = {(c["Server"], c["Tool"]): c["Consensus"].strip().lower()
              for c in csv.DictReader(sheet.open(encoding="utf-8-sig"))}
    if agreed and all(agreed.values()):
        final = [r for r, x, y in rows
                 if agreed.get((r["Server"], r["Tool"]), "yes" if x == y == "yes" else "no") == "yes"]
        summary["final"] = {b: sum(r["Benchmark"] == b for r in final) for b in ("MCPWild", "AgentDojo")}
        summary["final_by_server"] = dict(Counter(r["Server"] for r in final).most_common())
        hidden: dict[str, set[str]] = {}
        for r in final:
            hidden.setdefault(r["Server"], set()).update(r["Hidden Labels"].split())
        # HOSTEXEC implies the other three labels, so a server hiding it is counted under HOSTEXEC alone.
        summary["final_servers_by_label"] = dict(Counter(
            lab for h in hidden.values() for lab in ({"HOSTEXEC"} if "HOSTEXEC" in h else h)).most_common())
    return summary


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--out", default=str(ROOT / "results/summarize/mcpwild"))
    a = ap.parse_args()
    s = ROOT / "results/summarize"
    defs = surfaces()
    counts_m, rows_m = compare(mcpwild(s / f"mcpwild/{a.model}", s / f"mcpwild/description_vs_code/{a.model}"), defs)
    counts_a, rows_a = compare(agentdojo(s / f"agentdojo/{a.model}", s / f"agentdojo/{a.model}-description-only"), defs)
    out, rows = Path(a.out), rows_m + rows_a
    (out / "e1.json").write_text(json.dumps({"model": a.model, "MCPWild": counts_m, "AgentDojo": counts_a}, indent=1))
    for k in (1, 2):
        sheet = out / f"e1_audit_annotator{k}.csv"
        if sheet.exists() and any(r.get("Hidden Effect", "").strip()
                                  for r in csv.DictReader(sheet.open(encoding="utf-8-sig"))):
            print(f"kept {sheet.name}: it already holds an annotator's answers")  # never overwrite an audit
            continue
        with sheet.open("w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=COLUMNS)
            w.writeheader()
            w.writerows(rows)
    guide = out / "e1_audit.md"
    if not guide.exists():  # the guide may carry the annotators' own edits
        guide.write_text(GUIDE.format(n=len(rows), m=len(rows_m), a=len(rows_a), lines=MAX_LINES))
    print(json.dumps({"MCPWild": counts_m, "AgentDojo": counts_a}, indent=1))
    print(f"{len(rows)} flagged tools -> {out}/e1_audit_annotator{{1,2}}.csv, guide e1_audit.md")
    summary = audit_summary(out)
    if summary:
        (out / "e1_audit_summary.json").write_text(json.dumps(summary, indent=1))
        print(json.dumps({k: summary[k] for k in ("all", "confirmed_by_server")}, indent=1))


if __name__ == "__main__":
    main()
