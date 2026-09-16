# P0 throwaway: mine public MCP config files from GitHub code search.
# Stores only server identity + launch shape; env values and other args are never written (see PREREG.md).
import json, re, shlex, subprocess, sys, time, tomllib, urllib.request, urllib.parse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

OUT = Path(__file__).parent / "data" / "configs.jsonl"
QUERIES = {
    "cursor": "mcpServers filename:mcp.json path:.cursor",
    "vscode": "servers filename:mcp.json path:.vscode",
    "claude_code": "mcpServers filename:.mcp.json",
    "claude_desktop": "mcpServers filename:claude_desktop_config.json",
    "codex": "mcp_servers filename:config.toml path:.codex",
}
SIZES = ["<250", "250..399", "400..599", "600..899", "900..1299", "1300..1999", "2000..3999", ">3999"]

def search(q, page):
    for _ in range(5):
        r = subprocess.run(["gh", "api", "-X", "GET", "search/code", "-f", f"q={q}",
                            "-f", "per_page=100", "-f", f"page={page}"], capture_output=True, text=True)
        time.sleep(6.5)  # code search: 10 req/min
        if r.returncode == 0:
            return json.loads(r.stdout)
        if "rate limit" in r.stderr.lower() or "403" in r.stderr:
            time.sleep(60); continue
        print("search error", q, page, r.stderr[:200], file=sys.stderr); return None
    return None

def raw(item):
    ref = urllib.parse.parse_qs(urllib.parse.urlparse(item["url"]).query).get("ref", ["HEAD"])[0]
    u = f"https://raw.githubusercontent.com/{item['repository']['full_name']}/{ref}/{urllib.parse.quote(item['path'])}"
    try:
        with urllib.request.urlopen(u, timeout=20) as f:
            return f.read(200_000).decode("utf-8", "replace")
    except Exception:
        return None

def loads_jsonc(s):
    try: return json.loads(s)
    except Exception: pass
    out, i, n, ins = [], 0, len(s), False
    while i < n:  # strip // and /* */ outside strings
        c = s[i]
        if ins:
            out.append(c)
            if c == "\\" and i + 1 < n: out.append(s[i + 1]); i += 1
            elif c == '"': ins = False
        elif c == '"': ins = True; out.append(c)
        elif s.startswith("//", i): i = s.find("\n", i); i = n if i < 0 else i; continue
        elif s.startswith("/*", i): i = s.find("*/", i); i = n if i < 0 else i + 2; continue
        else: out.append(c)
        i += 1
    t = re.sub(r",(\s*[}\]])", r"\1", "".join(out))
    try: return json.loads(t)
    except Exception: return None

NPM_RUNNERS = {"npx", "bunx", "npx.cmd"}
PY_RUNNERS = {"uvx", "pipx"}

def npm_spec(spec):
    m = re.fullmatch(r"(@[^/@\s]+/[^@\s]+|[^@\s][^@\s]*)(?:@(.+))?", spec)
    if not m: return spec, None
    name, ver = m.group(1), m.group(2)
    return name, (ver is not None and ver not in ("latest", "next"))

def py_spec(spec):
    m = re.fullmatch(r"([A-Za-z0-9_.\-\[\]]+)(?:(==|@)(.+))?", spec)
    if not m: return spec, None
    return re.sub(r"\[.*\]", "", m.group(1)).lower(), (m.group(3) is not None and m.group(3) != "latest")

def first_positional(args, skip_with_value=()):
    it = iter(range(len(args)))
    for i in it:
        a = args[i]
        if a in skip_with_value: next(it, None); continue
        if a.startswith("-"): continue
        return i
    return None

def normalize(entry):
    if not isinstance(entry, dict): return None
    url = entry.get("url") or entry.get("serverUrl") or entry.get("httpUrl")
    if isinstance(url, str) and url.startswith("http"):
        return {"kind": "remote", "id": urllib.parse.urlparse(url).hostname, "pinned": None}
    cmd = entry.get("command")
    args = entry.get("args") or []
    if not isinstance(cmd, str): return None
    if not isinstance(args, list): args = []
    return launch(cmd, [str(a) for a in args])

def launch(cmd, args, depth=0):
    parts = [cmd] if re.match(r"^[A-Za-z]:[\\/]", cmd) else cmd.split()  # keep "C:\Program Files\..." whole
    if len(parts) > 1: cmd, args = parts[0], parts[1:] + args
    if not cmd: return None
    base = re.sub(r"\.(exe|cmd)$", "", cmd.replace("\\", "/").rsplit("/", 1)[-1].lower())
    if depth < 3:
        if base == "cmd" and args[:1] and args[0].lower() in ("/c", "/k") and len(args) > 1:
            return launch(args[1], args[2:], depth + 1)
        if base == "wsl":
            a = args[1:] if args[:1] in (["-e"], ["--exec"], ["--"]) else args
            while a[:1] and a[0] in ("-d", "--distribution", "-u", "--user"): a = a[2:]
            if a: return launch(a[0], a[1:], depth + 1)
        if base in ("bash", "sh", "zsh") and len(args) >= 2 and args[0] in ("-c", "-lc", "-ic"):
            try: sub = shlex.split(args[1])
            except ValueError: sub = args[1].split()
            if sub: return launch(sub[0], sub[1:], depth + 1)
    if base in NPM_RUNNERS or (base in {"pnpm", "yarn"} and args[:1] == ["dlx"]):
        a = args[1:] if base in {"pnpm", "yarn"} else args
        i = first_positional(a, {"-p", "--package"})
        if i is None: return None
        name, pinned = npm_spec(a[i])
        if name == "mcp-remote":
            u = next((x for x in a[i + 1:] if x.startswith("http")), None)
            if u: return {"kind": "remote", "id": urllib.parse.urlparse(u).hostname, "pinned": None, "via": "mcp-remote"}
        return {"kind": "npm", "id": name, "pinned": pinned, "_rest": a[i + 1:]}
    if base in PY_RUNNERS or (base == "uv" and args[:2] == ["tool", "run"]):
        a = args[2:] if base == "uv" else (args[1:] if base == "pipx" and args[:1] == ["run"] else args)
        if "--from" in a:
            name, pinned = py_spec(a[a.index("--from") + 1]); return {"kind": "pypi", "id": name, "pinned": pinned, "_rest": a[a.index("--from") + 2:]}
        i = first_positional(a, {"--python", "--with"})
        if i is None: return None
        name, pinned = py_spec(a[i]); return {"kind": "pypi", "id": name, "pinned": pinned, "_rest": a[i + 1:]}
    if base in {"docker", "podman"} and "run" in args:
        a = args[args.index("run") + 1:]
        i = first_positional(a, {"-e", "--env", "-v", "--volume", "--name", "--network", "--env-file", "-p", "--mount", "-u", "--user", "-w", "--workdir", "--platform", "--entrypoint"})
        if i is None: return None
        img = a[i]
        pinned = "@sha256:" in img or (":" in img.rsplit("/", 1)[-1] and not img.endswith(":latest"))
        return {"kind": "docker", "id": re.split(r"[:@]", img)[0] if not img.startswith("ghcr.io") or ":" in img else img, "pinned": pinned, "_rest": a[i + 1:], "_docker_opts": a[:i]}
    local = any(x.endswith((".js", ".py", ".ts", ".mjs")) or "/" in x for x in args[:2])
    return {"kind": "local" if local else "other", "id": base, "pinned": None}

def parse(kind, text, keep=False):
    if kind == "codex":
        try: d = tomllib.loads(text)
        except Exception: return None
        servers = d.get("mcp_servers")
    else:
        d = loads_jsonc(text)
        if not isinstance(d, dict): return None
        servers = d.get("mcpServers") if kind != "vscode" else (d.get("servers") or d.get("mcpServers"))
    if not isinstance(servers, dict): return None
    out = []
    for key, entry in servers.items():
        s = normalize(entry)
        if s:
            s["key"] = str(key)[:80]
            if keep: s["_entry"] = entry
            else: s = {k: v for k, v in s.items() if not k.startswith("_")}
            out.append(s)
    return out

HITS = OUT.parent / "hits.jsonl"

def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    seen = set()
    if OUT.exists():
        seen = {(r["repo"], r["path"]) for r in map(json.loads, OUT.open())}
    done_shards = {r["shard"] for r in map(json.loads, HITS.open()) if "shard" in r} if HITS.exists() else set()
    with OUT.open("a") as fo, HITS.open("a") as fh, ThreadPoolExecutor(8) as pool:
        for kind, q in QUERIES.items():
            for sz in SIZES:
                qq = f"{q} size:{sz}"
                if qq in done_shards: continue
                first = search(qq, 1)
                if not first: continue
                total = first["total_count"]
                print(f"{kind} size:{sz} total={total}", flush=True)
                items = first["items"]
                for page in range(2, min(10, (min(total, 1000) + 99) // 100) + 1):
                    res = search(qq, page)
                    if not res or not res["items"]: break
                    items += res["items"]
                for it in items:
                    fh.write(json.dumps({"file_kind": kind, "repo": it["repository"]["full_name"], "path": it["path"],
                                         "url": it["url"], "fork": it["repository"].get("fork")}) + "\n")
                fh.write(json.dumps({"shard": qq, "total": total, "fetched": len(items)}) + "\n"); fh.flush()
                items = [it for it in items if (it["repository"]["full_name"], it["path"]) not in seen]
                for it, text in zip(items, pool.map(raw, items)):
                    key = (it["repository"]["full_name"], it["path"])
                    if key in seen or text is None: continue
                    seen.add(key)
                    servers = parse(kind, text)
                    if servers is None: continue
                    fo.write(json.dumps({"file_kind": kind, "repo": key[0], "path": key[1],
                                         "fork": it["repository"].get("fork"), "servers": servers}) + "\n")
                fo.flush()
                print(f"  kept so far: {len(seen)}", flush=True)

if __name__ == "__main__":
    main()
