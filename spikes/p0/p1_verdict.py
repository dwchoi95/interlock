# P1 throwaway: precise verdicts from code-adjudicated summaries + value-level config settings (PREREG_P1.md).
import json, re, sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).parent
OUT = HERE / "adjudication" / "out"
ALL3 = {"SECRET", "UNTRUSTED", "SINK"}
FILE = {"@upstash/context7-mcp": "upstash_context7-mcp", "@playwright/mcp": "playwright_mcp", "github-mcp-server": "github-mcp-server",
        "@modelcontextprotocol/server-filesystem": "modelcontextprotocol_server-filesystem",
        "@modelcontextprotocol/server-sequential-thinking": "modelcontextprotocol_server-sequential-thinking",
        "@modelcontextprotocol/server-github": "modelcontextprotocol_server-github", "@modelcontextprotocol/server-memory": "modelcontextprotocol_server-memory",
        "chrome-devtools-mcp": "chrome-devtools-mcp", "@supabase/mcp-server-supabase": "supabase_mcp-server-supabase", "shadcn": "shadcn",
        "@modelcontextprotocol/server-postgres": "modelcontextprotocol_server-postgres", "next-devtools-mcp": "next-devtools-mcp", "serena": "serena",
        "mcp-server-fetch": "mcp-server-fetch", "@modelcontextprotocol/server-brave-search": "modelcontextprotocol_server-brave-search",
        "@modelcontextprotocol/server-puppeteer": "modelcontextprotocol_server-puppeteer", "learn.microsoft.com": "learn.microsoft.com"}
SERENA_NO_SHELL_CONTEXTS = {"claude-code", "ide", "vscode", "codex", "grok", "codebuddy", "junie", "copilot-cli", "antigravity",
                            "jb-ai-assistant", "jb-copilot-plugin", "ide-assistant"}
MANUAL = Counter()  # configs whose settings touch a condition this script does not model

def summaries():
    return {ident: json.load((OUT / f"{fn}.json").open()) for ident, fn in FILE.items()}

def enabled_tools(ident, adj, s):
    tools = {n: t for n, t in adj["tools"].items() if t.get("default_enabled", True)}
    flags, vals = set(s.get("flags", [])), s.get("vals", {})
    if ident == "serena":
        ctx = vals.get("context")
        if ctx in SERENA_NO_SHELL_CONTEXTS or vals.get("mode") in ("planning", "onboarding"):
            tools.pop("execute_shell_command", None)
    if ident == "chrome-devtools-mcp" and any(f.startswith(("no-category", "category", "allowedurlpattern", "allowed-url-pattern", "slim", "no-javascript-evaluation")) for f in flags):
        MANUAL[ident] += 1
    if ident == "@playwright/mcp" and ("allowed-origins" in flags or "PLAYWRIGHT_MCP_ALLOWED_ORIGINS" in s.get("env_vals", {})):
        MANUAL[ident] += 1  # removes UNTRUSTED/SINK from page tools only; browser_run_code_unsafe (HOSTEXEC) remains, so union unchanged
    tools = github_conditions(ident, tools, s)
    tools = supabase_conditions(ident, tools, s)
    return tools

GH_TOOLSETS = json.load((HERE / "adjudication" / "github_toolsets.json").open())
GH_DEFAULT = {"context", "repos", "issues", "pull_requests", "users", "copilot"}
GH_READONLY = {t["name"] for t in json.load((HERE / "adjudication" / "input" / "github-mcp-server.json").open())["tools"]
               if (t.get("annotations") or {}).get("readOnlyHint") is True}
SB_FEATURES = json.load((HERE / "adjudication" / "supabase_features.json").open())
TRUE = ("1", "true", "yes", "on")

def github_conditions(ident, tools, s):
    if ident != "github-mcp-server": return tools
    flags, vals, env, hdr = set(s.get("flags", [])), s.get("vals", {}), s.get("env_vals", {}), {k.lower(): v for k, v in s.get("headers", {}).items()}
    path = s.get("url_path", "")
    ro = "read-only" in flags or env.get("GITHUB_READ_ONLY", "").lower() in TRUE or hdr.get("x-mcp-readonly", "").lower() in TRUE or "/readonly" in path
    ts = vals.get("toolsets") or env.get("GITHUB_TOOLSETS") or hdr.get("x-mcp-toolsets")
    m = re.search(r"/x/([a-z_]+)", path)
    if m: ts = m.group(1)
    if {"tools", "exclude-tools"} & flags or {"x-mcp-tools", "x-mcp-exclude-tools"} & set(hdr): MANUAL[ident] += 1
    if ts:
        want = {x.strip() for x in ts.split(",") if x.strip()}
        if "default" in want: want |= GH_DEFAULT
        allon = "all" in want
        adj = summaries_cache["github-mcp-server"]["tools"]
        tools = {n: t for n, t in adj.items()
                 if (allon or GH_TOOLSETS.get(n) in want) and (t.get("default_enabled") or GH_TOOLSETS.get(n) not in GH_DEFAULT)}
    if ro:
        tools = {n: t for n, t in tools.items() if n in GH_READONLY}
    return tools

def supabase_conditions(ident, tools, s):
    if ident != "@supabase/mcp-server-supabase": return tools
    flags, vals, q = set(s.get("flags", [])), s.get("vals", {}), s.get("query", {})
    feats = vals.get("features") or (q.get("features") if q.get("features") != "present" else None)
    if feats:
        want = {x.strip() for x in feats.split(",")}
        tools = {n: t for n, t in tools.items() if SB_FEATURES.get(n) in want}
    if "read-only" in flags or str(q.get("read_only", "")).lower() in TRUE:
        tools = {n: dict(t, labels=[x for x in t["labels"] if x != "SINK"]) for n, t in tools.items()}
    return tools

summaries_cache = {}

def labels(ident, adj, s, secondary=False):
    L = set()
    for t in enabled_tools(ident, adj, s).values():
        L |= set(t["labels"]) | (set(t.get("secondary_extra", [])) if secondary else set())
    if ident == "@upstash/context7-mcp" and "--visible-credential-only" in sys.argv:
        cred = "api-key" in s.get("flags", []) or "CONTEXT7_API_KEY" in s.get("env_names", []) or s.get("url_path", "").startswith("/mcp/oauth") \
               or any(k.lower() in ("authorization", "context7_api_key", "x-context7-api-key", "context7-api-key", "x-api-key", "x_api_key") for k in s.get("headers", {}))
        if not cred: L.discard("SECRET")
    return ALL3 | {"HOSTEXEC"} if "HOSTEXEC" in L else L

def v0_labels(ident):
    import analyze
    inp = HERE / "adjudication" / "input" / f"{FILE[ident]}.json"
    if inp.exists():
        tools = json.load(inp.open())["tools"]
    else:  # serena: no advertised schemas captured; v0 sees names only
        tools = [{"name": n} for n in summaries_cache[ident]["tools"]]
    return analyze.expand(set().union(*[analyze.label_tool(t) for t in tools]))

def main():
    adj = summaries()
    summaries_cache.update(adj)
    configs = [json.loads(l) for l in (HERE / "data" / "configs_p1.jsonl").open()]
    und = {i: sum(t.get("undetermined", False) for t in a["tools"].values()) / max(1, len(a["tools"])) for i, a in adj.items()}
    for secondary in (False, True):
        unsafe = single = 0
        drivers = Counter()
        for c in configs:
            per = {s["ident"]: labels(s["ident"], adj[s["ident"]], s, secondary) for s in c["servers"]}
            if ALL3 <= set().union(*per.values()):
                unsafe += 1
                alone = [i for i, L in per.items() if ALL3 <= L]
                if alone: single += 1; drivers.update(alone)
        n = len(configs)
        tag = "SECONDARY" if secondary else "PRIMARY"
        print(f"{tag}: precise UNSAFE {unsafe}/{n} = {unsafe/n:.1%}; of UNSAFE, already UNSAFE by a single server: {single}/{unsafe} = {single/max(1,unsafe):.1%}")
        if not secondary:
            print("  single-server drivers:", drivers.most_common(10))
            print("  composition-only UNSAFE configs:", unsafe - single, f"({(unsafe-single)/n:.1%} of N)")
    v0 = {i: v0_labels(i) for i in adj}
    v0_unsafe = sum(1 for c in configs if ALL3 <= set().union(*[v0[s["ident"]] for s in c["servers"]]))
    print(f"v0 (strict) on same configs/versions: UNSAFE {v0_unsafe}/{len(configs)} = {v0_unsafe/len(configs):.1%}")
    agree = Counter()
    for c in configs:
        a = ALL3 <= set().union(*[v0[s["ident"]] for s in c["servers"]])
        b = ALL3 <= set().union(*[labels(s["ident"], adj[s["ident"]], s) for s in c["servers"]])
        agree[(a, b)] += 1
    print("  (v0 UNSAFE, precise UNSAFE) counts:", dict(agree))
    print("  per-package union v0 vs precise(default):", {i: (sorted(v0[i] - {"EXEC"}), sorted(labels(i, adj[i], {}))) for i in adj if (v0[i] - {"EXEC"}) != labels(i, adj[i], {}) - {"HOSTEXEC"}})
    print("undetermined share > 20%:", {i: round(v, 2) for i, v in und.items() if v > 0.2})
    print("configs touching unmodelled conditions:", dict(MANUAL))

if __name__ == "__main__":
    main()
