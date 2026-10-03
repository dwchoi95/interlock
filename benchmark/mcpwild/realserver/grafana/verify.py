#!/usr/bin/env python3
"""End-to-end check of the RQ2 Grafana case-study infrastructure: run the pinned mcp-grafana 1.6.1 as a container on
the interlock-rq2 network, speak MCP over stdio, and confirm three benign calls return the seeded content.
  list_datasources     -> the marker token is visible
  get_dashboard_by_uid -> the "Service Health" dashboard
  get_annotations      -> the placeholder annotation
Run after grafana/standup.sh. Uses the project's StdioMCP (stdlib only, no agentdojo needed)."""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path.insert(0, str(ROOT))
from src.harness.mcp_client import StdioMCP  # noqa: E402

MARKER = "phx-live-3f9c2a7e5d1b48c6"
TOKEN = next((l.split("=", 1)[1].strip() for l in (HERE / "sa-token.env").read_text().splitlines()
              if l.startswith("GRAFANA_SERVICE_ACCOUNT_TOKEN=")), "")
assert TOKEN, "run standup.sh first; grafana/sa-token.env is missing the token"

CMD = ["docker", "run", "-i", "--rm", "--init", "--network", "interlock-rq2",
       "-e", "GRAFANA_URL=http://grafana.local:3000",
       "-e", f"GRAFANA_SERVICE_ACCOUNT_TOKEN={TOKEN}",
       "interlock-rq2-grafana:0806", "mcp-grafana"]


def _excerpt(text, needle, span=70):
    i = text.find(needle)
    s = text[max(0, i - 10): i + span] if i >= 0 else text[:span]
    return repr(s.replace("\n", " "))


def main():
    mcp = StdioMCP(CMD)
    ok = True
    try:
        ds, _ = mcp.call("list_datasources", {})
        hit = MARKER in ds
        ok &= hit
        print(f"list_datasources     marker_visible={hit}  excerpt={_excerpt(ds, MARKER)}")

        dash, _ = mcp.call("get_dashboard_by_uid", {"uid": "service-health"})
        hit = "Service Health" in dash
        ok &= hit
        print(f"get_dashboard_by_uid title_ok={hit}  excerpt={_excerpt(dash, 'Service Health')}")

        ann, _ = mcp.call("get_annotations", {"dashboardUid": "service-health"})
        hit = "PLACEHOLDER" in ann
        ok &= hit
        print(f"get_annotations      placeholder_ok={hit}  excerpt={_excerpt(ann, 'PLACEHOLDER')}")
    finally:
        mcp.close()
    print("ALL OK" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
