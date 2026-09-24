"""Advertised tool surfaces, as recovered by the Docker tools/list harness in spikes/p0."""
from __future__ import annotations
import copy
import json
from functools import lru_cache
from pathlib import Path

@lru_cache(maxsize=8)
def _load_rows(path: str) -> tuple[dict, ...]:
    return tuple(json.loads(l) for l in Path(path).open())

def _rows(path: Path, package: str) -> list[dict]:
    resolved = str(Path(path).resolve())
    rows = _load_rows(resolved)
    good = [r for r in rows if r["pkg"] == package and r.get("ok")]
    if not good:
        raise KeyError(f"no recovered surface for {package} in {resolved}")
    return sorted(good, key=lambda r: r["published"])

def latest_version(path: Path, package: str) -> str:
    return _rows(path, package)[-1]["version"]

def load_surface(path: Path, package: str, version: str | None = None) -> dict:
    rows = _rows(path, package)
    row = rows[-1] if version is None else next((r for r in rows if r["version"] == version), None)
    if row is None:
        raise KeyError(f"no recovered surface for {package}@{version} in {Path(path).resolve()}")
    return {"package": package, "version": row["version"], "kind": row["kind"], "tools": copy.deepcopy(row["tools"])}
