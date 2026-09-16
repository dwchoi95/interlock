"""Advertised tool surfaces, as recovered by the Docker tools/list harness in spikes/p0."""
from __future__ import annotations
import json
from pathlib import Path

def _rows(path: Path, package: str) -> list[dict]:
    rows = [json.loads(l) for l in Path(path).open()]
    good = [r for r in rows if r["pkg"] == package and r.get("ok")]
    if not good:
        raise KeyError(f"no recovered surface for {package}")
    return sorted(good, key=lambda r: r["published"])

def latest_version(path: Path, package: str) -> str:
    return _rows(path, package)[-1]["version"]

def load_surface(path: Path, package: str, version: str | None = None) -> dict:
    rows = _rows(path, package)
    row = rows[-1] if version is None else next((r for r in rows if r["version"] == version), None)
    if row is None:
        raise KeyError(f"no recovered surface for {package}@{version}")
    return {"package": package, "version": row["version"], "kind": row["kind"], "tools": row["tools"]}
