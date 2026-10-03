"""Local stand-in for the chart server's rendering service (VIS_REQUEST_SERVER): records every request body and
answers as the service does, with a chart URL."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode("utf-8", "replace")
        with open("/records/render.jsonl", "a") as f:
            f.write(json.dumps({"path": self.path, "body": body}) + "\n")
        out = json.dumps({"success": True, "errorMessage": "", "resultObj": "https://render.stub/chart.png"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *args):
        pass


ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
