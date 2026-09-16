# P0 throwaway: v0 rule-based labeller + chronological replay of config verdicts (definitions in PREREG.md).
import json, re, sys
from collections import Counter, defaultdict
from pathlib import Path

D = Path(__file__).parent / "data"
SECRET, UNTRUSTED, SINK, EXEC = "SECRET", "UNTRUSTED", "SINK", "EXEC"
ALL3 = {SECRET, UNTRUSTED, SINK}

READ_V = {"read", "get", "list", "search", "query", "download", "view", "open", "find", "cat", "retrieve", "fetch", "export", "describe", "show"}
PRIVATE_O = {"file", "files", "directory", "dir", "env", "secret", "secrets", "credential", "credentials", "key", "keys",
             "db", "database", "table", "tables", "sql", "repo", "repository", "contents", "content", "email", "emails",
             "message", "messages", "mail", "inbox", "drive", "doc", "docs", "document", "documents", "page", "pages",
             "note", "notes", "memory", "memories", "graph", "entities", "nodes", "code", "commit", "commits", "diff",
             "calendar", "event", "events", "contact", "contacts", "channel", "history", "record", "records", "row", "rows"}
PATH_P = {"path", "paths", "file_path", "filepath", "filename", "sql", "table", "database", "directory"}
EXTERNAL_O = {"issue", "issues", "comment", "comments", "email", "emails", "message", "messages", "mail", "inbox",
              "discussion", "discussions", "pr", "pull", "review", "reviews", "tweet", "tweets", "post", "posts", "notification", "notifications"}
WEB_W = {"fetch", "scrape", "crawl", "browse", "navigate", "web", "websearch", "url", "http", "search"}
URL_P = {"url", "urls", "uri", "href", "link"}
SEND_V = {"send", "post", "create", "add", "comment", "reply", "publish", "push", "upload", "share", "notify", "submit",
          "update", "edit", "write", "append", "insert", "put", "merge", "fork", "invite"}
CARRIER_O = {"issue", "comment", "review", "pull", "pr", "message", "email", "mail", "tweet", "post", "gist", "release",
             "page", "doc", "document", "files", "file", "commit", "task", "ticket", "card", "channel", "webhook", "request", "blob", "wiki"}
LOCAL_SINK_O = {"file", "files"}  # write_file on the local fs is not external; push_files to a remote is
EXEC_W = {"exec", "execute", "run", "shell", "command", "bash", "terminal", "eval", "evaluate", "sh", "powershell"}
EXEC_P = {"command", "cmd", "code", "script", "shell"}

def words(name):
    name = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name or "")
    return set(w for w in re.split(r"[^a-zA-Z0-9]+", name.lower()) if w)

def params(tool):
    s = tool.get("inputSchema") or {}
    return set((s.get("properties") or {}).keys()) if isinstance(s, dict) else set()

def label_tool(tool):
    w, p = words(tool.get("name")), {x.lower() for x in params(tool)}
    ann = tool.get("annotations") or {}
    L = set()
    if (w & EXEC_W) and (p & EXEC_P or w & {"shell", "bash", "terminal", "powershell"}):
        return {EXEC}
    if p & URL_P or w & (WEB_W - {"search"}) or ("search" in w and not (w & PRIVATE_O)):
        L |= {UNTRUSTED, SINK}  # an agent-chosen URL both ingests attacker content and can carry data out
    if (w & READ_V) and (w & EXTERNAL_O):
        L.add(UNTRUSTED)
    if ((w & READ_V) and (w & PRIVATE_O)) or ((w & READ_V) and (p & PATH_P)):
        L.add(SECRET)
    if (w & SEND_V) and (w & CARRIER_O):
        if not ((w & LOCAL_SINK_O) and not (w & {"push", "upload", "commit", "gist"}) and (p & PATH_P)):
            L.add(SINK)
    if ann.get("openWorldHint") is True and ann.get("readOnlyHint") is False:
        L.add(SINK)
    return L

def expand(labels):
    return ALL3 | {EXEC} if EXEC in labels else labels

def surface_key(tools):
    return json.dumps(sorted([t.get("name"), t.get("inputSchema"), t.get("annotations")] for t in tools), sort_keys=True, default=str)

def selfcheck():
    t = lambda name, props=(), ann=None: {"name": name, "inputSchema": {"properties": {k: {} for k in props}}, "annotations": ann}
    assert label_tool(t("read_text_file", ["path"])) == {SECRET}
    assert label_tool(t("write_file", ["path", "content"])) == set()
    assert label_tool(t("fetch", ["url"])) == {UNTRUSTED, SINK}
    assert label_tool(t("create_issue", ["owner", "repo", "title", "body"])) == {SINK}
    assert label_tool(t("push_files", ["owner", "repo", "files"])) == {SINK}
    assert label_tool(t("get_issue", ["owner", "repo", "issue_number"])) == {UNTRUSTED}
    assert label_tool(t("get_file_contents", ["owner", "repo", "path"])) == {SECRET}
    assert label_tool(t("execute_command", ["command"])) == {EXEC}
    assert label_tool(t("search_files", ["path", "pattern"])) == {SECRET}
    assert label_tool(t("resolve-library-id", ["libraryName"])) == set()
    assert label_tool(t("browser_navigate", ["url"])) == {UNTRUSTED, SINK}
    assert malformed({"inputSchema": {"$schema": "x"}}) and malformed({"inputSchema": {"~standard": {}, "def": {}, "type": "object"}})
    assert not malformed({"inputSchema": {"type": "object"}}) and not malformed({"inputSchema": {"properties": {}}})
    print("labeller selfcheck ok")

def malformed(tool):
    sc = tool.get("inputSchema")
    if not isinstance(sc, dict): return True
    if any(k.startswith("~") for k in sc) or "def" in sc or "_def" in sc or "shape" in sc: return True  # serialized zod object, not JSON Schema
    return "properties" not in sc and sc.get("type") != "object"

STRICT = "--strict-schema" in sys.argv  # deviation from PREREG: versions advertising non-JSON-Schema inputSchema are unobservable

def load():
    configs = [json.loads(l) for l in (D / "configs.jsonl").open()]
    hist = defaultdict(list)  # pkg -> [(published, version, labels, surface_key)]
    fails = Counter()
    for r in map(json.loads, (D / "surfaces.jsonl").open()):
        if not r["ok"]: fails[r["pkg"]] += 1; continue
        if STRICT and any(malformed(x) for x in r["tools"]): fails[r["pkg"] + " (malformed schema)"] += 1; continue
        labels = set().union(*[label_tool(x) for x in r["tools"]]) if r["tools"] else set()
        hist[r["pkg"]].append((r["published"], r["version"], expand(labels), surface_key(r["tools"])))
    for v in hist.values(): v.sort()
    return configs, hist, fails

def replay(servers, hist):
    pkgs = sorted({s["id"] for s in servers})
    t0 = max(hist[p][0][0] for p in pkgs)
    state = {p: max((h for h in hist[p] if h[0] <= t0), key=lambda h: h[0]) for p in pkgs}
    unsafe = lambda st: ALL3 <= set().union(*[h[2] for h in st.values()])
    base_unsafe = unsafe(state)
    events = sorted((h, p) for p in pkgs for h in hist[p] if h[0] > t0)
    flips, alerts, cur_unsafe, flip_detail = 0, 0, base_unsafe, []
    for h, p in events:
        if h[3] != state[p][3]: alerts += 1
        prev = state[p]; state[p] = h
        now = unsafe(state)
        if now and not cur_unsafe:
            flips += 1; flip_detail.append((p, prev[1], h[1], sorted(h[2] - prev[2])))
        cur_unsafe = now
    return base_unsafe, flips, alerts, flip_detail, t0

def main():
    selfcheck()
    configs, hist, fails = load()
    configs = [c for c in configs if not c.get("fork")]
    uniq = {}
    for c in configs:  # dedupe identical server sets within a repo (e.g. .cursor and .vscode copies)
        sig = (c["repo"], tuple(sorted({(s["kind"], s["id"]) for s in c["servers"]})))
        uniq.setdefault(sig, c)
    configs = list(uniq.values())
    multi = [c for c in configs if len({(s["kind"], s["id"]) for s in c["servers"]}) >= 2]
    stdio = [s for c in configs for s in c["servers"] if s["kind"] in ("npm", "pypi", "docker")]
    kinds = Counter(s["kind"] for c in configs for s in c["servers"])
    print(f"configs (non-fork, deduped): {len(configs)}; with >=2 servers: {len(multi)}")
    print("server entry kinds:", dict(kinds))
    sizes = Counter(min(len({s['id'] for s in c['servers']}), 10) for c in configs)
    print("set size distribution (10 = 10+):", dict(sorted(sizes.items())))
    print(f"G4 unpinned stdio entries: {sum(1 for s in stdio if s['pinned'] is False)}/{len(stdio)} = {sum(1 for s in stdio if s['pinned'] is False)/max(1,len(stdio)):.1%}")
    top = Counter(s["id"] for c in configs for s in {x["id"]: x for x in c["servers"]}.values())
    print("top servers:", top.most_common(25))
    resolved = [c for c in multi if all(s["kind"] in ("npm", "pypi") and len(hist.get(s["id"], [])) >= 1 for s in c["servers"])]
    print(f"N eligible (>=2 servers, all resolved): {len(resolved)}")
    print("surface collection failures by pkg:", dict(fails.most_common(15)))
    rows = [replay(c["servers"], hist) for c in resolved]
    base_unsafe = sum(r[0] for r in rows)
    safe_rows = [r for r in rows if not r[0]]
    flipped = sum(1 for r in safe_rows if r[1] > 0)
    flips = sum(r[1] for r in safe_rows); alerts_safe = sum(r[2] for r in safe_rows); alerts_all = sum(r[2] for r in rows)
    n = len(rows)
    print(f"\nG2 baseline UNSAFE: {base_unsafe}/{n} = {base_unsafe/max(1,n):.1%}  (<= 80% required)")
    print(f"G1 baseline-SAFE configs with >=1 flip: {flipped}/{len(safe_rows)} = {flipped/max(1,len(safe_rows)):.1%}  (>= 5% required)")
    print(f"G3 pin-all alerts / flips on baseline-SAFE configs: {alerts_safe}/{flips} = {alerts_safe/max(1,flips):.1f}  (>= 5 required); alerts over all eligible: {alerts_all}")
    ex = Counter((d[0], d[1], d[2], tuple(d[3])) for r in safe_rows for d in r[3])
    print(f"flip-causing version steps ({len(ex)} distinct):", ex.most_common(10))
    g = {"N": n >= 100, "G1": flipped / max(1, len(safe_rows)) >= 0.05, "G2": base_unsafe / max(1, n) <= 0.80, "G3": flips > 0 and alerts_safe / flips >= 5}
    print("\ncriteria:", g, "->", "INCONCLUSIVE" if not g["N"] else ("GO" if g["G1"] and g["G2"] and g["G3"] else "NO-GO"))

if __name__ == "__main__":
    selfcheck() if sys.argv[1:2] == ["selfcheck"] else main()
