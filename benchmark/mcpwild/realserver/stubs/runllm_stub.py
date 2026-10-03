"""Local stand-in for RunLLM's MCP endpoint (https://mcp.runllm.com/mcp), which phoenix-support calls: a
streamable-HTTP MCP server with the one tool the client uses, search, recording every query."""
import json
import ssl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOOL = {"name": "search", "description": "Search the Phoenix documentation.",
        "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}
ANSWER = "Phoenix collects traces through OpenInference instrumentors; see the tracing guide for setup."


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def reply(self, code, obj=None, headers=()):
        data = json.dumps(obj).encode() if obj is not None else b""
        self.send_response(code)
        for k, v in headers:
            self.send_header(k, v)
        if obj is not None:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # no server-initiated stream
        self.reply(405)

    def do_DELETE(self):
        self.reply(200)

    def do_POST(self):
        msg = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        method, mid = msg.get("method"), msg.get("id")
        if mid is None:  # a notification
            return self.reply(202)
        if method == "initialize":
            result = {"protocolVersion": msg.get("params", {}).get("protocolVersion", "2025-06-18"),
                      "capabilities": {"tools": {}}, "serverInfo": {"name": "runllm-stub", "version": "1"}}
            return self.reply(200, {"jsonrpc": "2.0", "id": mid, "result": result}, [("Mcp-Session-Id", "stub")])
        if method == "tools/list":
            return self.reply(200, {"jsonrpc": "2.0", "id": mid, "result": {"tools": [TOOL]}})
        if method == "tools/call":
            query = msg.get("params", {}).get("arguments", {}).get("query", "")
            with open("/records/runllm.jsonl", "a") as f:
                f.write(json.dumps({"query": query}) + "\n")
            result = {"content": [{"type": "text", "text": ANSWER}]}
            return self.reply(200, {"jsonrpc": "2.0", "id": mid, "result": result})
        return self.reply(200, {"jsonrpc": "2.0", "id": mid, "result": {}})

    def log_message(self, *args):
        pass


server = ThreadingHTTPServer(("0.0.0.0", 443), Handler)
ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
ctx.load_cert_chain("/certs/runllm.pem", "/certs/runllm.key")
server.socket = ctx.wrap_socket(server.socket, server_side=True)
server.serve_forever()
