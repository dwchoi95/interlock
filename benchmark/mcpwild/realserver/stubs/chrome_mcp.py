"""The developer's browser for the Chrome DevTools scenarios: Chromium with the given tabs open and
chrome-devtools-mcp attached to it; this process relays the MCP server's stdio.

chrome-devtools-mcp connects to the browser on its first tool call and selects one of the pages it finds then, in
an order that varies from run to run. So this wrapper makes that first call itself while only a blank tab exists
(the selected tab the agent will navigate), then opens the developer's tabs and relays the client's messages,
answering the client's initialize with the server's own reply."""
import json
import subprocess
import sys
import threading
import time
import urllib.request

PORT = 9222
tabs = sys.argv[1:]
subprocess.Popen(["chromium", "--headless=new", "--no-sandbox", "--disable-gpu", "--ignore-certificate-errors",
                  f"--remote-debugging-port={PORT}", "--user-data-dir=/tmp/profile", "about:blank"],
                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def devtools(path: str, method: str = "GET"):
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", method=method)
    return json.loads(urllib.request.urlopen(req, timeout=2).read())


for _ in range(200):
    try:
        devtools("/json/version")
        break
    except Exception:
        time.sleep(0.1)
server = subprocess.Popen(["chrome-devtools-mcp", f"--browserUrl=http://127.0.0.1:{PORT}", "--no-usage-statistics",
                           "--no-performance-crux"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)


def ask(msg: dict) -> dict:
    server.stdin.write(json.dumps(msg) + "\n")
    server.stdin.flush()
    while True:
        line = server.stdout.readline()
        try:
            reply = json.loads(line)
        except json.JSONDecodeError:
            continue
        if reply.get("id") == msg["id"]:
            return reply


init = ask({"jsonrpc": "2.0", "id": "wrapper-init", "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "wrapper", "version": "1"}}})
server.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
server.stdin.flush()
ask({"jsonrpc": "2.0", "id": "wrapper-warmup", "method": "tools/call", "params": {"name": "list_pages", "arguments": {}}})
for url in tabs:
    devtools("/json/new?" + url, method="PUT")
for _ in range(100):  # every tab has its title once its page has loaded
    pages = [t for t in devtools("/json/list") if t.get("type") == "page" and t["url"] != "about:blank"]
    if len(pages) >= len(tabs) and all(t["title"] and t["title"] != t["url"] for t in pages):
        break
    time.sleep(0.1)


def relay_out() -> None:
    for line in server.stdout:
        sys.stdout.write(line)
        sys.stdout.flush()


threading.Thread(target=relay_out, daemon=True).start()
for line in sys.stdin:
    try:
        msg = json.loads(line)
    except json.JSONDecodeError:
        continue
    if msg.get("method") == "initialize":  # the server is initialized already: give the client the server's reply
        sys.stdout.write(json.dumps({**init, "id": msg["id"]}) + "\n")
        sys.stdout.flush()
        continue
    if msg.get("method") == "notifications/initialized":
        continue
    server.stdin.write(line if line.endswith("\n") else line + "\n")
    server.stdin.flush()
server.terminate()
