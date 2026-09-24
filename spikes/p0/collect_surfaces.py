# P0 throwaway: recover per-version tool surfaces by launching each package version in a
# network-enabled but otherwise isolated container (no host mounts, capped cpu/mem) and calling tools/list.
import json, re, select, subprocess, sys, time, urllib.request, urllib.parse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

D = Path(__file__).parent / "data"
IMAGE = "p0-mcp-runner"
WINDOW_START = "2025-03-01"
MAX_VERSIONS = 40
DUMMY_ENV = ["GITHUB_PERSONAL_ACCESS_TOKEN", "GITHUB_TOKEN", "API_KEY", "OPENAI_API_KEY", "BRAVE_API_KEY",
             "FIRECRAWL_API_KEY", "TAVILY_API_KEY", "EXA_API_KEY", "SLACK_BOT_TOKEN", "SLACK_TEAM_ID",
             "NOTION_API_KEY", "LINEAR_API_KEY", "SUPABASE_ACCESS_TOKEN", "CONTEXT7_API_KEY", "PERPLEXITY_API_KEY",
             "GOOGLE_API_KEY", "FIGMA_API_KEY", "STRIPE_SECRET_KEY", "DATABASE_URL", "POSTGRES_URL"]

def npm_versions(pkg):
    with urllib.request.urlopen(f"https://registry.npmjs.org/{urllib.parse.quote(pkg, safe='@')}", timeout=30) as f:
        meta = json.load(f)
    t = meta.get("time", {})
    allv = sorted([(v, t[v]) for v in meta.get("versions", {}) if v in t and "-" not in v], key=lambda x: x[1])
    return [x for x in allv if x[1] >= WINDOW_START] or allv[-1:]  # no release in window: surface is constant

def pypi_versions(pkg):
    with urllib.request.urlopen(f"https://pypi.org/pypi/{pkg}/json", timeout=30) as f:
        meta = json.load(f)
    vs = []
    for v, files in meta.get("releases", {}).items():
        if files and not re.search(r"[a-zA-Z]", v):
            up = min(x["upload_time_iso_8601"] for x in files)
            vs.append((v, up))
    vs.sort(key=lambda x: x[1])
    return [x for x in vs if x[1] >= WINDOW_START] or vs[-1:]

def thin(vs):
    if len(vs) <= MAX_VERSIONS: return vs
    step = (len(vs) - 1) / (MAX_VERSIONS - 1)  # keep first and last, evenly spaced
    return [vs[round(i * step)] for i in range(MAX_VERSIONS)]

def rpc(proc, msg):
    proc.stdin.write((json.dumps(msg) + "\n").encode()); proc.stdin.flush()

def read_id(proc, want, deadline):
    buf = b""
    while time.time() < deadline:
        r, _, _ = select.select([proc.stdout], [], [], 1)
        if not r:
            if proc.poll() is not None: return None
            continue
        chunk = proc.stdout.read1(65536)
        if not chunk: return None
        buf += chunk
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            try: m = json.loads(line)
            except Exception: continue
            if isinstance(m, dict) and m.get("id") == want: return m
    return None

def as_of(published):
    # resolve dependencies as a user would have on publish day (+1 day margin), avoiding dependency drift
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.mktime(time.strptime(published[:19], "%Y-%m-%dT%H:%M:%S")) + 86400))

def tools_list(kind, pkg, ver, published, extra_args=(), timeout=240):
    cutoff = as_of(published)
    run = ["npx", "-y", f"{pkg}@{ver}"] if kind == "npm" else ["uvx", "--exclude-newer", cutoff, "--from", f"{pkg}=={ver}", pkg]
    cmd = ["docker", "run", "-i", "--rm", "--memory", "1g", "--cpus", "1",
           "-v", "p0-npm-cache:/root/.npm", "-v", "p0-uv-cache:/root/.cache/uv"]
    if kind == "npm": cmd += ["-e", f"npm_config_before={cutoff}"]
    for e in DUMMY_ENV: cmd += ["-e", f"{e}=dummy"]
    cmd += [IMAGE, *run, *extra_args]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    deadline = time.time() + timeout
    try:
        rpc(proc, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "p0", "version": "0"}}})
        if not read_id(proc, 1, deadline): return None, "no_initialize"
        rpc(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})
        tools, cursor, i = [], None, 2
        while True:
            rpc(proc, {"jsonrpc": "2.0", "id": i, "method": "tools/list", "params": {"cursor": cursor} if cursor else {}})
            m = read_id(proc, i, deadline)
            if not m or "result" not in m: return None, "no_tools_list"
            tools += m["result"].get("tools", [])
            cursor = m["result"].get("nextCursor"); i += 1
            if not cursor or i > 50: break
        return tools, None
    except (BrokenPipeError, OSError):
        return None, "crashed"
    finally:
        proc.kill(); proc.wait()

EXTRA_ARGS = {"shadcn": ["mcp"], "@modelcontextprotocol/server-filesystem": ["/tmp"], "@angular/cli": ["mcp"],
              "nx": ["mcp"], "firebase-tools": ["mcp"], "convex": ["mcp", "start"], "@azure/mcp": ["server", "start"],
              "@azure-devops/mcp": ["dummyorg"], "@modelcontextprotocol/server-postgres": ["postgresql://u:p@localhost/db"],
              "@modelcontextprotocol/server-sqlite": ["/tmp/x.db"], "figma-developer-mcp": ["--figma-api-key=dummy", "--stdio"], "@bytebase/dbhub": ["--transport", "stdio", "--dsn", "sqlite:///tmp/x.db"], "@sentry/mcp-server": ["--access-token", "dummy"]}
SKIP = {"tsx", "@smithery/cli", "playwright", "create-better-t-stack", "https://registry.npmjs.org"}

def job(kind, pkg, ver, published):
    tools, err = tools_list(kind, pkg, ver, published, extra_args=EXTRA_ARGS.get(pkg, ()))
    if tools is None and kind == "npm" and pkg not in EXTRA_ARGS:  # many servers need a positional arg (e.g. allowed dir)
        tools, err2 = tools_list(kind, pkg, ver, published, extra_args=["/tmp"])
        err = err if tools is None else None
    keep = [{k: t.get(k) for k in ("name", "description", "inputSchema", "annotations")} for t in tools or []]
    return {"kind": kind, "pkg": pkg, "version": ver, "published": published, "ok": tools is not None, "error": err, "tools": keep}

def main(top_n, seed=None):
    if seed:
        freq = Counter({tuple(x.split(":", 1)): 0 for x in seed})
    else:
        configs = [json.loads(l) for l in (D / "configs.jsonl").open()]
        freq = Counter((s["kind"], s["id"]) for c in configs for s in {(x["kind"], x["id"]): x for x in c["servers"]}.values()
                       if s["kind"] in ("npm", "pypi"))
    out = D / "surfaces.jsonl"
    done = {(r["pkg"], r["version"]) for r in map(json.loads, out.open())} if out.exists() else set()
    jobs = []
    for (kind, pkg), n in [x for x in freq.most_common(top_n + len(SKIP)) if x[0][1] not in SKIP][:top_n]:
        if pkg.startswith("git+"): continue
        try: vs = thin(npm_versions(pkg) if kind == "npm" else pypi_versions(pkg))
        except Exception as e: print("versions failed", pkg, e, file=sys.stderr); continue
        jobs += [(kind, pkg, v, t) for v, t in vs if (pkg, v) not in done]
        print(f"{pkg} ({kind}) in {n} configs: {len(vs)} versions", flush=True)
    print("jobs:", len(jobs), flush=True)
    with out.open("a") as fo, ThreadPoolExecutor(6) as pool:
        def safe(j):
            try: return job(*j)
            except Exception as e: return {"kind": j[0], "pkg": j[1], "version": j[2], "published": j[3], "ok": False, "error": f"harness:{type(e).__name__}", "tools": []}
        for r in pool.map(safe, jobs):
            fo.write(json.dumps(r) + "\n"); fo.flush()
            print(r["pkg"], r["version"], "ok" if r["ok"] else r["error"], len(r["tools"]), flush=True)

if __name__ == "__main__":
    if sys.argv[1:2] == ["probe"]:
        print(json.dumps(job(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5]), indent=1)[:800])
    elif sys.argv[1:2] == ["seed"]:
        main(len(sys.argv) - 2, seed=sys.argv[2:])
    else:
        main(int(sys.argv[1]) if len(sys.argv) > 1 else 40)
