"""Verdict per candidate repository and statistics of the accepted servers, from criteria.py and tools_list.jsonl.
Writes result.json (accepted servers, and the criterion each rejected repository fails), stats.json (Table tab:bench)
and surfaces.jsonl (the tool definitions and source location Summarize reads per server).

Run: python run.py benchmark report"""
import json, re, statistics as st
from collections import Counter
from pathlib import Path
from criteria import D, FAIL

HERE = Path(__file__).resolve().parent
REG = {r["pkg"]: r for rs in json.load(open(HERE / "registry.json")).values() for r in rs}
META = json.load(open(HERE / "candidates/github_meta.json"))
TOOLS = {r["pkg"]: r for r in map(json.loads, open(HERE / "tools_list.jsonl")) if r["ok"]}
# URLs of the 2026-03-26 list that GitHub now redirects to the repository criteria.D uses.
ALIAS = {"ahujasid/blender-mcp": "ahujasid/mcp-for-blender", "googleapis/genai-toolbox": "googleapis/mcp-toolbox",
         "sonnylazuardi/cursor-talk-to-figma-mcp": "grab/cursor-talk-to-figma-mcp", "firebase/genkit": "genkit-ai/genkit",
         "supabase-community/mcp-supabase": "supabase/mcp", "supabase-community/supabase-mcp": "supabase/mcp",
         "luminati-io/brightdata-mcp": "brightdata/brightdata-mcp", "ppl-ai/modelcontextprotocol": "perplexityai/modelcontextprotocol",
         "aaronjmars/opendia": "aeonfun/opendia", "manusa/kubernetes-mcp-server": "containers/kubernetes-mcp-server"}
CHANNEL = {"npm": "npm", "pypi": "PyPI", "docker": "container image", "binary": "release binary", "go": "Go module"}

stars = {}
for e in json.load(open(HERE / "candidates/hub_0326.json")):
    repo = "/".join(e["url"].split("github.com/")[1].split("#")[0].split("/")[:2]).lower()
    repo = ALIAS.get(repo, repo)
    stars[repo] = max(stars.get(repo, 0), int(re.sub(r"\D", "", e["stars"])))


def server(repo, kind, pkg):
    reg = REG.get(pkg, {})
    # a compiled binary inside the package; semgrep's wheels carry its engine, but its MCP server is the Python CLI
    native = bool(reg.get("platform_binaries")) or (bool(reg.get("platform_wheels")) and pkg != "semgrep")
    compiled = native or kind in ("docker", "binary")
    if kind in ("docker", "binary", "go") or native: lang = "C#" if pkg == "@azure/mcp" else "Go"
    else: lang = "Python" if kind == "pypi" else "JavaScript/TypeScript"
    return {"repo": repo, "pkg": pkg, "version": TOOLS[pkg]["version"], "channel": CHANNEL[kind], "language": lang,
            "source_in_artifact": not compiled, "n_tools": TOOLS[pkg]["n_tools"], "owner_type": META[repo]["owner_type"],
            "stars_2026_03_26": stars[repo.lower()]}


servers, failed = [], {}
for repo, v in D.items():
    ok = [server(repo, k, p) for k, p in v if p in TOOLS] if isinstance(v, list) else []
    if ok: servers += ok
    else: failed[repo] = {"criterion": FAIL[repo], "note": v if isinstance(v, str) else [p for _, p in v]}
assert set(failed) == set(FAIL), set(failed) ^ set(FAIL)
repos = {s["repo"]: s for s in servers}

T = [t for s in servers for t in TOOLS[s["pkg"]]["tools"]]
ann = [t.get("annotations") or {} for t in T]
n = [s["n_tools"] for s in servers]
q = st.quantiles(n, n=4)
stats = {
    "candidates": len(D), "rejected_by_criterion": dict(sorted(Counter(f["criterion"] for f in failed.values()).items())),
    "repositories": len(repos), "servers": len(servers), "tools": len(T),
    "tools_per_server": {"median": st.median(n), "q1": q[0], "q3": q[2], "min": min(n), "max": max(n)},
    "channel": Counter(s["channel"] for s in servers), "language": Counter(s["language"] for s in servers),
    "source_in_artifact": sum(s["source_in_artifact"] for s in servers),
    "owner_type_by_repo": Counter(s["owner_type"] for s in repos.values()),
    "stars_by_repo": {"median": st.median(s["stars_2026_03_26"] for s in repos.values()),
                      "min": min(s["stars_2026_03_26"] for s in repos.values()), "max": max(s["stars_2026_03_26"] for s in repos.values())},
    "description_words_median": st.median(len((t.get("description") or "").split()) for t in T),
    "input_params_median": st.median(len((t.get("inputSchema") or {}).get("properties") or {}) for t in T),
    "annotations": {"any": sum(bool(a) for a in ann), "readOnlyHint": sum(a.get("readOnlyHint") is True for a in ann),
                    "destructiveHint": sum(a.get("destructiveHint") is True for a in ann)},
}
# The git tag holding the source of each release whose package carries no source (a compiled binary, an image, a
# wrapper) or comes from a channel the fetcher does not read (a Go module); git ls-remote, 2026-09-29.
SOURCE_TAG = {"daytona": "v0.190.0", "github-mcp-server": "v1.12.2", "safeline-mcp": "v9.4.2",
              "@toolbox-sdk/server": "v1.13.1", "@azure/mcp": "Azure.Mcp.Server-3.0.0-beta.47", "mcp-grafana": "v1.6.1",
              "mcp-language-server": "v0.1.1", "slack-mcp-server": "v1.3.0", "kubernetes-mcp-server": "v0.0.67",
              "terraform-mcp-server": "v1.3.0"}
assert {s["pkg"] for s in servers if not s["source_in_artifact"] or s["channel"] == "Go module"} == set(SOURCE_TAG)
# surfaces.jsonl: what Summarize reads per server (tool definitions, and where the source is).
with open(HERE / "surfaces.jsonl", "w") as f:
    for s in servers:
        git = s["pkg"] in SOURCE_TAG
        f.write(json.dumps({"pkg": s["pkg"], "version": s["version"], "kind": "git" if git else s["channel"].lower(),
                            "published": REG.get(s["pkg"], {}).get("published", ""), "ok": True,
                            "source_ref": f"https://github.com/{s['repo']}" if git else None,
                            "source_version": SOURCE_TAG.get(s["pkg"]), "tools": TOOLS[s["pkg"]]["tools"]}) + "\n")
json.dump({"accepted": servers, "rejected": failed}, open(HERE / "result.json", "w"), indent=1)
json.dump(stats, open(HERE / "stats.json", "w"), indent=1)
print(json.dumps(stats, indent=1))
