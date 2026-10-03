"""A minimal MCP client over stdio (JSON-RPC 2.0, one message per line): enough to list and call tools."""
from __future__ import annotations

import itertools
import json
import queue
import subprocess
import threading
import time
from collections import deque


class StdioMCP:
    def __init__(self, cmd: list[str], timeout: float = 180.0) -> None:
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     text=True, bufsize=1)
        self.inbox: queue.Queue = queue.Queue()
        self.stderr: deque[str] = deque(maxlen=50)
        self.ids = itertools.count(1)
        self.timeout = timeout
        threading.Thread(target=self._read, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()
        self.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                    "clientInfo": {"name": "interlock-realserver", "version": "1"}})
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def _read(self) -> None:
        for line in self.proc.stdout:
            try:
                self.inbox.put(json.loads(line))
            except json.JSONDecodeError:
                pass  # a server that logs to stdout

    def _read_stderr(self) -> None:
        for line in self.proc.stderr:
            self.stderr.append(line.rstrip())

    def _send(self, msg: dict) -> None:
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()

    def request(self, method: str, params: dict | None = None) -> dict:
        rid = next(self.ids)
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
        deadline = time.time() + self.timeout
        while True:
            left = deadline - time.time()
            if left <= 0:
                raise TimeoutError(f"{method}: no reply; stderr: {list(self.stderr)[-5:]}")
            try:
                msg = self.inbox.get(timeout=left)
            except queue.Empty:
                continue
            if msg.get("id") == rid and "method" not in msg:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})
            if "method" in msg and "id" in msg:  # a request from the server (ping, roots/list): answer empty
                self._send({"jsonrpc": "2.0", "id": msg["id"], "result": {}})

    def tools(self) -> list[dict]:
        out, cursor = [], None
        while True:
            res = self.request("tools/list", {"cursor": cursor} if cursor else {})
            out += res.get("tools", [])
            cursor = res.get("nextCursor")
            if not cursor:
                return out

    def call(self, name: str, arguments: dict) -> tuple[str, bool]:
        res = self.request("tools/call", {"name": name, "arguments": arguments})
        text = "\n".join(c.get("text", "") for c in res.get("content", []) if c.get("type") == "text")
        return text, bool(res.get("isError"))

    def close(self) -> None:
        try:
            self.proc.stdin.close()
            self.proc.terminate()
            self.proc.wait(timeout=10)
        except Exception:
            self.proc.kill()
