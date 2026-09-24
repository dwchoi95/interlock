# P1 throwaway: re-fetch eligible configs and keep only allow-listed, non-secret value-level settings (PREREG_P1 data handling).
import json, re, urllib.parse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import mine_configs as mc

D = Path(__file__).parent / "data"
ALIAS = {"api.githubcopilot.com": "github-mcp-server", "ghcr.io/github/github-mcp-server": "github-mcp-server",
         "mcp.context7.com": "@upstash/context7-mcp", "mcp.supabase.com": "@supabase/mcp-server-supabase",
         "git+https://github.com/oraios/serena": "serena"}
S = {"@upstash/context7-mcp", "@playwright/mcp", "github-mcp-server", "@modelcontextprotocol/server-filesystem",
     "@modelcontextprotocol/server-sequential-thinking", "@modelcontextprotocol/server-github", "@modelcontextprotocol/server-memory",
     "chrome-devtools-mcp", "@supabase/mcp-server-supabase", "shadcn", "@modelcontextprotocol/server-postgres", "next-devtools-mcp",
     "serena", "mcp-server-fetch", "@modelcontextprotocol/server-brave-search", "@modelcontextprotocol/server-puppeteer", "learn.microsoft.com"}
VALUE_FLAGS = {"allowed-origins", "blocked-origins", "caps", "capabilities", "toolsets", "read-only", "readonly", "features",
               "browser", "isolated", "headless", "context", "mode", "extension", "allow-unrestricted-file-access",
               "ignore-robots-txt", "dynamic-toolsets", "lockdown-mode", "no-sandbox", "category-emulation", "category-performance",
               "category-network", "experimental-devtools", "chrome-arg", "channel", "slim", "experimental-vision", "save-session"}
CLASS_FLAGS = {"browserurl", "browser-url", "wsendpoint", "ws-endpoint", "cdp-endpoint", "autoconnect", "auto-connect",
               "user-data-dir", "userdatadir", "project-ref", "executable-path", "storage-state", "proxy-url", "proxy-server"}
ENV_VALUES = {"GITHUB_TOOLSETS", "GITHUB_READ_ONLY", "GITHUB_DYNAMIC_TOOLSETS", "GITHUB_LOCKDOWN_MODE", "READ_ONLY",
              "PLAYWRIGHT_MCP_ALLOWED_ORIGINS", "PLAYWRIGHT_MCP_BLOCKED_ORIGINS", "PLAYWRIGHT_MCP_CAPS", "PLAYWRIGHT_MCP_ISOLATED",
              "PLAYWRIGHT_MCP_BROWSER", "PLAYWRIGHT_MCP_EXTENSION", "PUPPETEER_LAUNCH_OPTIONS", "ALLOW_DANGEROUS"}
QUERY_VALUES = {"read_only", "readonly", "features", "toolsets"}
HEADER_VALUES = {"x-mcp-toolsets", "x-mcp-readonly", "x-mcp-lockdown", "x-mcp-tools"}

def host_class(v):
    h = urllib.parse.urlparse(v if "://" in v else "ws://" + v).hostname or ""
    return "local" if h in ("localhost", "127.0.0.1", "::1", "0.0.0.0", "host.docker.internal") else ("present" if h else "present")

def path_class(v):
    v = v.replace("\\", "/")
    if v in ("/", "C:/", "c:/"): return "system_root"
    if re.match(r"^(~|/Users/[^/]+/?$|/home/[^/]+/?$|[A-Za-z]:/Users/[^/]+/?$|\$\{?HOME|%USERPROFILE%)", v): return "home"
    if v.startswith(("/tmp", "/var/tmp")): return "tmp"
    if v in (".", "./") or v.startswith(("./", "${workspaceFolder}", "${workspaceRoot}", "${PWD}")) or not v.startswith(("/", "~")) and not re.match(r"^[A-Za-z]:", v): return "project_relative"
    return "other_absolute"

def split_flags(args):
    flags, vals, pos = [], {}, []
    i = 0
    while i < len(args):
        a = str(args[i])
        if a.startswith("-"):
            name, _, v = a.lstrip("-").partition("=")
            ln = name.lower(); flags.append(ln)
            takes = ln in VALUE_FLAGS | CLASS_FLAGS
            if not v and takes and i + 1 < len(args) and not str(args[i + 1]).startswith("-"):
                v = str(args[i + 1]); i += 1
            if v and ln in VALUE_FLAGS: vals[ln] = v
            elif ln in CLASS_FLAGS: vals[ln] = host_class(v) if ("url" in ln or "endpoint" in ln or "connect" in ln) and v else ("present" if v or True else None)
        else:
            pos.append(a)
        i += 1
    return flags, vals, pos

def env_part(env):
    env = env if isinstance(env, dict) else {}
    return sorted(env), {k: str(v) for k, v in env.items() if k in ENV_VALUES}

def summarize(s, entry):
    ident = ALIAS.get(s["id"], s["id"])
    out = {"ident": ident, "kind": s["kind"]}
    url = entry.get("url") or entry.get("serverUrl") or entry.get("httpUrl")
    if s["kind"] == "remote" and isinstance(url, str) and not s.get("via"):
        u = urllib.parse.urlparse(url)
        q = urllib.parse.parse_qs(u.query)
        out["url_path"] = u.path
        out["query"] = {k: (v[0] if k.lower() in QUERY_VALUES else "present") for k, v in q.items()}
        hdr = entry.get("headers") if isinstance(entry.get("headers"), dict) else {}
        out["headers"] = {k: (str(v) if k.lower() in HEADER_VALUES else "present") for k, v in hdr.items()}
        return out
    rest = [str(x) for x in s.get("_rest", [])]
    flags, vals, pos = split_flags(rest)
    out["flags"], out["vals"] = flags, vals
    out["env_names"], out["env_vals"] = env_part(entry.get("env"))
    if s["kind"] == "docker":
        dopts = s.get("_docker_opts", [])
        for j, a in enumerate(dopts):
            if a in ("-e", "--env") and j + 1 < len(dopts):
                k, _, v = dopts[j + 1].partition("=")
                out["env_names"].append(k)
                if v and k in ENV_VALUES: out["env_vals"][k] = v
    if ident == "@modelcontextprotocol/server-filesystem":
        out["roots"] = [path_class(p) for p in pos]
    if ident == "serena":
        out["positional"] = [p for p in pos if p in ("start-mcp-server",)]
    return out

def main():
    ident = lambda s: ALIAS.get(s["id"], s["id"])
    cs = [c for c in map(json.loads, (D / "configs.jsonl").open()) if not c.get("fork")]
    u = {}
    for c in cs: u.setdefault((c["repo"], tuple(sorted({(s["kind"], s["id"]) for s in c["servers"]}))), c)
    elig = [c for c in u.values() if len({ident(s) for s in c["servers"]}) >= 2 and all(ident(s) in S for s in c["servers"])]
    hits = {(h["repo"], h["path"]): h for h in map(json.loads, (D / "hits.jsonl").open()) if "repo" in h}
    print("eligible", len(elig))
    def work(c):
        h = hits.get((c["repo"], c["path"]))
        if not h: return c, None
        text = mc.raw({"url": h["url"], "repository": {"full_name": h["repo"]}, "path": h["path"]})
        return c, (mc.parse(c["file_kind"], text, keep=True) if text else None)
    n_ok = 0
    with (D / "configs_p1.jsonl").open("w") as fo, ThreadPoolExecutor(8) as pool:
        for c, servers in pool.map(work, elig):
            if not servers: continue
            ss = [summarize(s, s["_entry"]) for s in servers]
            if {x["ident"] for x in ss} != {ident(s) for s in c["servers"]}: continue  # file changed since P0 mining
            n_ok += 1
            fo.write(json.dumps({"repo": c["repo"], "path": c["path"], "file_kind": c["file_kind"], "servers": ss}) + "\n")
    print("written", n_ok)

if __name__ == "__main__":
    assert split_flags(["--allowed-origins", "https://a.com", "--api-key=SECRET", "--headless"]) == (["allowed-origins", "api-key", "headless"], {"allowed-origins": "https://a.com"}, [])
    assert split_flags(["--browserUrl", "http://127.0.0.1:9222"])[1] == {"browserurl": "local"}
    assert path_class("/Users/alice") == "home" and path_class("${workspaceFolder}") == "project_relative" and path_class("/") == "system_root"
    main()
