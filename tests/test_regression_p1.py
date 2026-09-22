import json
from pathlib import Path
import pytest
from src.profile import expand

P1 = Path("spikes/p0/adjudication/out")
EXPECTED_UNION = {
    "playwright_mcp": {"HOSTEXEC", "SECRET", "UNTRUSTED", "SINK"},
    "modelcontextprotocol_server-filesystem": {"SECRET"},
    "modelcontextprotocol_server-sequential-thinking": set(),
    "mcp-server-fetch": {"SECRET", "UNTRUSTED", "SINK"},
    "modelcontextprotocol_server-brave-search": {"UNTRUSTED"},
}

@pytest.mark.parametrize("name,expected", sorted(EXPECTED_UNION.items()))
def test_p1_union_is_pinned(name, expected):
    d = json.loads((P1 / f"{name}.json").read_text())
    union = set()
    for t in d["tools"].values():
        if t.get("default_enabled", True):
            union |= expand(set(t["labels"]))
    assert union == expected
