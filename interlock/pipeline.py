"""Turn a model judgement into a profile only the checked parts of which are trusted."""
from __future__ import annotations
from pathlib import Path
from interlock.adjudicate import adjudicate
from interlock.evidence import check_tool
from interlock.profile import Profile, ToolEffect
from interlock.select import select_files
from interlock.source import fetch_source
from interlock.surface import load_surface


def verify(raw: dict, surface: dict, root: Path) -> tuple[Profile, dict]:
    tools: dict[str, ToolEffect] = {}
    stats = {"tools": len(surface["tools"]), "claims": 0, "verified": 0, "demoted": 0,
              "cleared_verified": 0, "cleared_unverified": 0, "missing_tools": [], "unknown_tools": []}
    judged = raw.get("tools", {})
    surface_names = {t["name"] for t in surface["tools"]}
    stats["unknown_tools"] = sorted(set(judged) - surface_names)
    for t in surface["tools"]:
        name = t["name"]
        j = judged.get(name)
        if j is None:
            stats["missing_tools"].append(name)
            tools[name] = ToolEffect(labels=["HOSTEXEC"], evidence=[], rationale="not judged; least restrictive reading",
                                     default_enabled=True, undetermined=True)
            continue
        try:
            # Copy labels/evidence: they must not alias the caller's raw lists.
            effect = ToolEffect(labels=list(j["labels"]), evidence=list(j["evidence"]), rationale=j["rationale"],
                                default_enabled=j.get("default_enabled", True),
                                undetermined=bool(j.get("undetermined")))
        except ValueError as e:
            raise ValueError(f"{surface['package']}@{surface['version']}: tool {name!r}: {e}") from e
        # Evidence is checked whether the model asserted labels or cleared the tool outright:
        # a clearance is itself a judgement, and an unverifiable one must not pass silently.
        ok, reason = check_tool(root, effect, name)
        if effect.labels:
            stats["claims"] += 1
            if ok:
                stats["verified"] += 1
            else:
                stats["demoted"] += 1
                effect.undetermined = True
                effect.rationale = f"{effect.rationale} [unverified: {reason}]"
        else:
            if ok:
                stats["cleared_verified"] += 1
            else:
                stats["cleared_unverified"] += 1
                effect.undetermined = True
                effect.rationale = f"{effect.rationale} [unverified: {reason}]"
        tools[name] = effect
    profile = Profile(package=surface["package"], version=surface["version"], kind=surface["kind"],
                      source=str(root), tools=tools, value_conditions=raw.get("value_conditions", []),
                      notes=raw.get("notes", []))
    return profile, stats


def build_profile(package: str, version: str | None, kind: str, surfaces_path: Path, cache_dir: Path, client,
                  source_ref: str | None = None) -> tuple[Profile, dict]:
    surface = load_surface(surfaces_path, package, version)
    root = fetch_source(kind, source_ref or package, surface["version"], cache_dir)
    files = select_files(root, [t["name"] for t in surface["tools"]])
    raw = adjudicate(surface, files, client=client)
    return verify(raw, surface, root)
