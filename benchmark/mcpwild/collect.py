"""Collect every server's tool definitions (tools/list) at its pinned version and write tools_list.jsonl.

Each server runs alone in a container (2 GB memory, 1 CPU) with placeholder credentials. LAUNCH holds, per server,
the one launch that starts it; servers that contact a backing service at start-up run next to a real local instance
of it (services()). dagster-dg-cli is run too, to record that its latest version no longer has an MCP command.

Run: python run.py benchmark collect [server ...]
"""
import calendar, json, select, signal, subprocess, sys, tempfile, time, urllib.request, uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager, nullcontext
from pathlib import Path

HERE = Path(__file__).resolve().parent
REG = {r["pkg"]: r for rs in json.load(open(HERE / "registry.json")).values() for r in rs}
RUNNER = "mcp-bench-runner"  # built from ./Dockerfile, one tag per platform
NET = "mcp-bench"
PLACEHOLDER = [
    "GITHUB_PERSONAL_ACCESS_TOKEN", "GITHUB_TOKEN", "API_KEY", "API_TOKEN", "OPENAI_API_KEY", "BRAVE_API_KEY",
    "FIRECRAWL_API_KEY", "TAVILY_API_KEY", "EXA_API_KEY", "SLACK_BOT_TOKEN", "SLACK_TEAM_ID", "NOTION_API_KEY",
    "NOTION_TOKEN", "LINEAR_API_KEY", "SUPABASE_ACCESS_TOKEN", "CONTEXT7_API_KEY", "PERPLEXITY_API_KEY", "GOOGLE_API_KEY",
    "FIGMA_API_KEY", "STRIPE_SECRET_KEY", "DATABASE_URL", "POSTGRES_URL", "TWENTY_FIRST_API_KEY", "N8N_API_URL",
    "N8N_API_KEY", "QDRANT_URL", "COLLECTION_NAME", "MYSQL_HOST", "MYSQL_USER", "MYSQL_PASS", "MYSQL_DB", "JIRA_URL",
    "JIRA_USERNAME", "JIRA_API_TOKEN", "CONFLUENCE_URL", "OBSIDIAN_API_KEY", "PHOENIX_API_KEY", "MILVUS_ADDRESS",
    "EMBEDDING_PROVIDER", "GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET", "LINGODOTDEV_API_KEY",
    "TIANJI_BASE_URL", "TIANJI_API_KEY", "TIANJI_WORKSPACE_ID", "ADO_ORG"]

APT = "apt-get update -qq >/dev/null && apt-get install -y -qq curl ca-certificates >/dev/null && "
KUBECONFIG = ("printf 'apiVersion: v1\\nkind: Config\\nclusters: [{name: c, cluster: {server: \"https://127.0.0.1:6443\"}}]\\n"
              "contexts: [{name: c, context: {cluster: c, user: u}}]\\ncurrent-context: c\\nusers: [{name: u, user: {token: x}}]\\n'"
              " > /tmp/kc && export KUBECONFIG=/tmp/kc && ")


def npm(*args, **kw): return dict(kind="npm", args=list(args), **kw)
def pypi(exe=None, *args, **kw): return dict(kind="pypi", exe=exe, args=list(args), **kw)
def image(ref, *args, **kw): return dict(kind="image", image=ref, args=list(args), **kw)


def release(url, run, version, **kw):
    """A release binary of the same tag as the source, fetched into a plain Debian container."""
    # ponytail: arm64 assets, as the host is Apple silicon; switch the asset name on an amd64 host
    return dict(kind="image", image="debian:bookworm-slim", version=version,
                args=["sh", "-c", APT + f"curl -sfL -o /b {url} && chmod +x /b && " + run], timeout=600, **kw)


# One entry per server of criteria.D that passes criteria (i)-(iii). Unlisted fields: npx pkg@ver / uvx --from pkg==ver
# with dependencies resolved as of the release day, placeholder credentials, 300 s to answer.
LAUNCH = {
    # modelcontextprotocol/servers
    "@modelcontextprotocol/server-everything": npm("stdio"),
    "@modelcontextprotocol/server-filesystem": npm("/tmp"),
    "@modelcontextprotocol/server-memory": npm(),
    "@modelcontextprotocol/server-sequential-thinking": npm(),
    "mcp-server-fetch": pypi(), "mcp-server-git": pypi(), "mcp-server-time": pypi(),
    # other npm servers
    "chrome-devtools-mcp": npm(), "@agent-infra/mcp-server-browser": npm(), "@arizeai/phoenix-mcp": npm(),
    "cursor-talk-to-figma-mcp": npm(), "firecrawl-mcp": npm(), "@wonderwhy-er/desktop-commander": npm(),
    "@executeautomation/playwright-mcp-server": npm(), "firebase-tools": npm("mcp"), "@mobilenext/mobile-mcp": npm(),
    "@notionhq/notion-mcp-server": npm(), "exa-mcp-server": npm(), "@antv/mcp-server-chart": npm(), "godot-mcp": npm(),
    "@supabase/mcp-server-supabase": npm(), "mcp-markdownify-server": npm(), "@brightdata/mcp": npm(),
    "@perplexity-ai/mcp-server": npm(), "opendia": npm(), "tavily-mcp": npm(), "@anaisbetts/mcp-installer": npm(),
    "nx-mcp": npm(), "mcp-server-kubernetes": npm(),
    "@bytebase/dbhub": npm("--transport", "stdio", "--dsn", "sqlite:///tmp/x.db"),
    "@zilliz/claude-context-mcp": npm(env={"EMBEDDING_PROVIDER": "OpenAI", "OPENAI_API_KEY": "sk-dummy", "MILVUS_ADDRESS": "localhost:19530"}),
    "tianji-mcp-server": npm(env={"TIANJI_BASE_URL": "http://localhost:12345"}),
    # the latest release (0.13.2) is missing a file it imports; its previous release is used
    "figma-developer-mcp": npm("--figma-api-key=dummy", "--stdio", version="0.13.1", cutoff=False),
    "@azure/mcp": npm("server", "start", platform="linux/amd64", timeout=600),
    "@azure-devops/mcp": npm("dummyorg", "--authentication", "envvar", env={"ADO_MCP_AUTH_TOKEN": "x"},
                             prep="apt-get update -qq >/dev/null 2>&1; apt-get install -y -qq libsecret-1-0 >/dev/null 2>&1;"),
    # its first stdin line would otherwise answer an analytics consent prompt
    "genkit-cli": npm("mcp", timeout=600, prep="mkdir -p /tmp/g && cd /tmp/g && echo '{}' > package.json && "
                      "npx -y genkit-cli@1.43.0 config set analyticsOptOut true >/dev/null 2>&1;"),
    # other PyPI servers
    "awslabs.aws-documentation-mcp-server": pypi(), "mcp-atlassian": pypi(), "vizro-mcp": pypi(), "mcp-obsidian": pypi(),
    "arxiv-mcp-server": pypi(), "workspace-mcp": pypi(), "mcp-for-blender": pypi(), "ableton-mcp": pypi(),
    "excel-mcp-server": pypi("excel-mcp-server", "stdio"), "mcp-memory-service": pypi("memory", "server"),
    "basic-memory": pypi("basic-memory", "mcp", prerelease=True),
    "cognee-mcp": pypi(python="3.11", timeout=600),
    "mcp-server-qdrant": pypi(env={"EMBEDDING_PROVIDER": "fastembed", "QDRANT_URL": "http://localhost:6333", "COLLECTION_NAME": "c"}),
    "mcp-run-python": pypi("mcp-run-python", "stdio", timeout=600,
                           prep="curl -fsSL https://deno.land/install.sh | sh -s -- -y >/dev/null 2>&1; export PATH=/root/.deno/bin:$PATH;"),
    "skyvern": pypi("skyvern", "run", "mcp", extra="server", cutoff=False, timeout=600,
                    env={"SKYVERN_API_KEY": "x", "SKYVERN_BASE_URL": "http://localhost:8000"}),
    "mcp-grafana": pypi(platform="linux/amd64", cutoff=False, env={"GRAFANA_URL": "http://localhost:3000"}),
    "dagster-dg-cli": pypi("dg", "mcp", "serve", with_=["dagster-dg-cli[mcp]"]),
    # container images and binaries, pinned to the release tag
    "github-mcp-server": image("ghcr.io/github/github-mcp-server:v1.12.2", "stdio", version="v1.12.2"),
    "terraform-mcp-server": image("hashicorp/terraform-mcp-server:1.3.0", version="1.3.0"),
    "semgrep": image("semgrep/semgrep:1.178.0", "semgrep", "mcp", version="1.178.0"),
    "mcp-language-server": image("golang:1.24", "sh", "-c",
                                 "go install github.com/isaacphi/mcp-language-server@v0.1.1 >/dev/null 2>&1 && "
                                 "go install golang.org/x/tools/gopls@v0.18.1 >/dev/null 2>&1 && mkdir -p /w && cd /w && "
                                 "go mod init w >/dev/null 2>&1; exec mcp-language-server --workspace /w --lsp gopls",
                                 version="v0.1.1", timeout=900),
    "daytona": release("https://github.com/daytonaio/daytona/releases/download/v0.190.0/daytona-linux-arm64",
                       "exec /b mcp start", "v0.190.0", env={"DAYTONA_API_KEY": "x"}),
    "kubernetes-mcp-server": release("https://github.com/containers/kubernetes-mcp-server/releases/download/v0.0.67/kubernetes-mcp-server-linux-arm64",
                                     KUBECONFIG + "exec /b", "v0.0.67"),
    # its built-in demo mode; --no-cache, or stdio waits for a user cache demo mode never fills
    "slack-mcp-server": release("https://github.com/korotovsky/slack-mcp-server/releases/download/v1.3.0/slack-mcp-server-linux-arm64",
                                "exec /b --transport stdio --no-cache", "v1.3.0", env={"SLACK_MCP_XOXP_TOKEN": "demo"}),
    # backing services started by services()
    "@benborla29/mcp-server-mysql": npm(timeout=480, service=True,
                                        env={"MYSQL_HOST": "w-mysql", "MYSQL_PORT": "3306", "MYSQL_USER": "root", "MYSQL_PASS": "pw", "MYSQL_DB": "db"}),
    "@leonardsellem/n8n-mcp-server": npm(service=True, env={"N8N_API_URL": "http://w-n8n:5678/api/v1"}),  # key set by services()
    "@toolbox-sdk/server": release("https://storage.googleapis.com/mcp-toolbox-for-databases/v1.13.1/linux/amd64/toolbox",
                                   "exec /b --prebuilt postgres --stdio", "v1.13.1", platform="linux/amd64", service=True,
                                   env={"POSTGRES_HOST": "w-pg", "POSTGRES_PORT": "5432", "POSTGRES_DATABASE": "db",
                                        "POSTGRES_USER": "postgres", "POSTGRES_PASSWORD": "pw"}),
    # Streamable HTTP only, so probed over HTTP inside its own image (see safeline())
    "safeline-mcp": dict(kind="http", version="v9.4.2"),
}


def rpc(proc, msg):
    proc.stdin.write((json.dumps(msg) + "\n").encode()); proc.stdin.flush()


def read_id(proc, want, deadline):
    buf = b""
    while time.time() < deadline:
        if not select.select([proc.stdout], [], [], 1)[0]:
            if proc.poll() is not None: return None
            continue
        chunk = proc.stdout.read1(65536)
        if not chunk: return None
        buf += chunk
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            try: m = json.loads(line)
            except ValueError: continue
            if isinstance(m, dict) and m.get("id") == want: return m
    return None


def as_of(published):
    """Resolve dependencies as a user would have one day after the release."""
    t = calendar.timegm(time.strptime(published[:19], "%Y-%m-%dT%H:%M:%S")) + 86400
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def command(name, f, env):
    reg = REG.get(name, {})
    version = f.get("version") or reg["version"]
    cmd = ["docker", "run", "-i", "--rm", "--name", f"mcp-bench-{uuid.uuid4().hex[:12]}", "--memory", "2g", "--cpus", "1"]
    if f.get("platform"): cmd += ["--platform", f["platform"]]
    if f.get("service"): cmd += ["--network", NET]
    if f["kind"] == "image":
        inner, img = f["args"], f["image"]
    else:
        img = f"{RUNNER}:{(f.get('platform') or 'linux/arm64').split('/')[1]}"
        cmd += ["-v", "mcp-bench-npm:/root/.npm", "-v", "mcp-bench-uv:/root/.cache/uv"]
        cutoff = as_of(reg["published"]) if f.get("cutoff", True) else None
        if f["kind"] == "npm":
            if cutoff: env = {**env, "npm_config_before": cutoff}
            inner = ["npx", "-y", f"{name}@{version}", *f["args"]]
        else:
            spec = f"{name}[{f['extra']}]=={version}" if f.get("extra") else f"{name}=={version}"
            inner = (["uvx"] + (["--exclude-newer", cutoff] if cutoff else []) + (["--prerelease=allow"] if f.get("prerelease") else [])
                     + (["--python", f["python"]] if f.get("python") else []) + ["--from", spec]
                     + sum((["--with", w] for w in f.get("with_", [])), []) + [f.get("exe") or name, *f["args"]])
        if f.get("prep"):
            inner = ["sh", "-c", f["prep"] + " exec " + " ".join(f"'{x}'" if " " in x or "[" in x else x for x in inner)]
    for k, v in env.items(): cmd += ["-e", f"{k}={v}"]
    return cmd + [img, *inner], version


def stdio(name, f, env):
    cmd, version = command(name, f, env)
    tools, err = None, None
    with tempfile.TemporaryFile() as log:
        p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log)
        deadline = time.time() + f.get("timeout", 300)
        try:
            rpc(p, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                                                                                   "clientInfo": {"name": "mcp-bench", "version": "0"}}})
            if not read_id(p, 1, deadline):
                err = "no_initialize"
            else:
                rpc(p, {"jsonrpc": "2.0", "method": "notifications/initialized"})
                tools, cursor, i = [], None, 2
                while True:
                    rpc(p, {"jsonrpc": "2.0", "id": i, "method": "tools/list", "params": {"cursor": cursor} if cursor else {}})
                    m = read_id(p, i, deadline)
                    if not m or "result" not in m:
                        tools, err = None, "no_tools_list"; break
                    tools += m["result"].get("tools", []); cursor = m["result"].get("nextCursor"); i += 1
                    if not cursor or i > 50: break
        except OSError:
            err = "crashed"
        finally:
            p.kill(); p.wait()
            sh("docker", "rm", "-f", cmd[cmd.index("--name") + 1])  # killing the client leaves the container running
        log.seek(0); tail = log.read().decode(errors="ignore")[-600:]
    return version, tools, err, tail


SAFELINE = r"""mkdir -p /run/secrets && for i in a b; do echo x > /run/secrets/safeline-$i.token; chmod 600 /run/secrets/safeline-$i.token; done
MCP_AUTH_DISABLED=true /app/mcp-server --config /etc/safeline-mcp/config.yaml >/tmp/log 2>&1 & sleep 3
post() { wget -qO- -S --header="Content-Type: application/json" --header="Accept: application/json, text/event-stream" \
  --header="MCP-Protocol-Version: 2025-06-18" ${SID:+--header="Mcp-Session-Id: $SID"} --post-data="$1" http://127.0.0.1:5678/mcp 2>>/tmp/hdr; echo; }
post '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"mcp-bench","version":"0"}}}' >/dev/null
SID=$(grep -i "mcp-session-id" /tmp/hdr | awk '{print $2}' | tr -d '\r' | head -1)
post '{"jsonrpc":"2.0","method":"notifications/initialized"}' >/dev/null
post '{"jsonrpc":"2.0","id":2,"method":"tools/list"}'"""


def safeline(name, f, env):
    """SafeLine's server speaks Streamable HTTP only; run it on loopback with authentication off, as its code allows there."""
    p = subprocess.run(["docker", "run", "--rm", "--memory", "2g", "--cpus", "1", "--entrypoint", "sh",
                        f"chaitin/safeline-mcp:{f['version']}", "-c", SAFELINE], capture_output=True, text=True)
    for line in p.stdout.splitlines():
        if '"id":2' in line:
            return f["version"], json.loads(line)["result"]["tools"], None, ""
    return f["version"], None, "no_tools_list", p.stderr[-600:]


def attempt(name):
    f = LAUNCH[name]
    env = {**{k: "dummy" for k in PLACEHOLDER}, **f.get("env", {})}
    if name == "@leonardsellem/n8n-mcp-server": env["N8N_API_KEY"] = N8N_KEY
    version, tools, err, tail = (safeline if f["kind"] == "http" else stdio)(name, f, env)
    return {"pkg": name, "version": version, "ok": tools is not None, "error": err, "n_tools": len(tools or []),
            "tools": [{k: t.get(k) for k in ("name", "description", "inputSchema", "annotations")} for t in tools or []],
            "stderr_tail": "" if tools is not None else tail}


def sh(*cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def wait(check, what, limit=180):
    for _ in range(limit // 3):
        if check(): return
        time.sleep(3)
    sys.exit(f"{what} did not come up")


def n8n(path, body, cookie=None):
    req = urllib.request.Request("http://127.0.0.1:5679" + path, json.dumps(body).encode(), {"Content-Type": "application/json", **({"Cookie": cookie} if cookie else {})})
    r = urllib.request.urlopen(req, timeout=30)
    return json.load(r), r.headers.get("Set-Cookie", "").split(";")[0]


@contextmanager
def services():
    """Real MySQL, PostgreSQL and n8n instances on a private network, and an n8n API key made through n8n's own API."""
    global N8N_KEY
    sh("docker", "network", "create", NET)
    sh("docker", "run", "-d", "--name", "w-mysql", "--network", NET, "--memory", "1g", "-e", "MYSQL_ROOT_PASSWORD=pw", "-e", "MYSQL_DATABASE=db", "mysql:8.4")
    sh("docker", "run", "-d", "--name", "w-pg", "--network", NET, "--memory", "512m", "-e", "POSTGRES_PASSWORD=pw", "-e", "POSTGRES_DB=db", "postgres:16")
    sh("docker", "run", "-d", "--name", "w-n8n", "--network", NET, "--memory", "1g", "-p", "127.0.0.1:5679:5678",
       "-e", "N8N_SECURE_COOKIE=false", "-e", "N8N_DIAGNOSTICS_ENABLED=false", "n8nio/n8n:1.100.0")
    try:
        wait(lambda: sh("docker", "exec", "w-mysql", "mysql", "-h127.0.0.1", "-uroot", "-ppw", "-e", "select 1").returncode == 0, "MySQL")
        wait(lambda: sh("docker", "exec", "w-pg", "pg_isready", "-U", "postgres").returncode == 0, "PostgreSQL")
        wait(lambda: sh("curl", "-sf", "http://127.0.0.1:5679/healthz").returncode == 0, "n8n")
        user = {"email": "o@example.com", "password": "Passw0rd!Passw0rd"}
        n8n("/rest/owner/setup", {**user, "firstName": "o", "lastName": "o"})
        _, cookie = n8n("/rest/login", {"emailOrLdapLoginId": user["email"], "password": user["password"]})
        N8N_KEY = n8n("/rest/api-keys", {"label": "mcp-bench", "expiresAt": None, "scopes": ["workflow:list", "workflow:read"]}, cookie)[0]["data"]["rawApiKey"]
        yield
    finally:
        sh("docker", "rm", "-f", "w-mysql", "w-pg", "w-n8n"); sh("docker", "network", "rm", NET)


N8N_KEY = None

if __name__ == "__main__":
    names = sys.argv[1:] or list(LAUNCH)
    for plat in {LAUNCH[n].get("platform") or "linux/arm64" for n in names if LAUNCH[n]["kind"] in ("npm", "pypi")}:
        sh("docker", "build", "-q", "--platform", plat, "-t", f"{RUNNER}:{plat.split('/')[1]}", str(HERE), check=True)
    needs = any(LAUNCH[n].get("service") for n in names)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(1))  # so services() still cleans up
    with (services() if needs else nullcontext()), ThreadPoolExecutor(4) as ex, open(HERE / "tools_list.jsonl", "w") as out:
        for fut in as_completed([ex.submit(attempt, n) for n in names]):
            r = fut.result()
            out.write(json.dumps(r) + "\n"); out.flush()
            print(f"{'OK ' if r['ok'] else 'ERR'} {r['n_tools']:4d} {r['pkg'][:48]:48s} {r['version']:14s} {r['error'] or ''}", flush=True)
