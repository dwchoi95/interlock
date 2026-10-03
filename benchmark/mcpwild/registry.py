"""Look up the latest npm/PyPI release of every registry-distributed server in criteria.D (registry.json) and the
GitHub metadata of every candidate repository (candidates/github_meta.json, needs the gh CLI). The versions it
records are the ones collect.py pins, so it never overwrites them unless run with --refresh.

Run: python run.py benchmark registry --refresh"""
import json, re, subprocess, sys, urllib.parse, urllib.request
from pathlib import Path
from criteria import D

OUT = Path(__file__).with_name("registry.json")
META = Path(__file__).with_name("candidates") / "github_meta.json"


def get(url):
    try:
        return json.load(urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "curl/8"}), timeout=40))
    except Exception:
        return None


def lookup(kind, pkg):
    r = {"kind": kind, "pkg": pkg, "exists": False}
    if kind == "npm":
        d = get("https://registry.npmjs.org/" + urllib.parse.quote(pkg, safe="@"))
        if d and "versions" in d:
            ver = d["dist-tags"]["latest"]; m = d["versions"][ver]; scripts = m.get("scripts", {})
            r.update(exists=True, version=ver, published=d["time"].get(ver), unpacked=m.get("dist", {}).get("unpackedSize"),
                     files=m.get("dist", {}).get("fileCount"), deprecated=m.get("deprecated"),
                     platform_binaries=[k for k in m.get("optionalDependencies", {}) if re.search(r"darwin|linux|win32|windows", k)],
                     postinstall=scripts.get("postinstall") or scripts.get("install"))
    else:
        d = get(f"https://pypi.org/pypi/{pkg}/json")
        if d:
            names = [f["filename"] for f in d["urls"]]
            r.update(exists=True, version=d["info"]["version"], files=len(names), summary=d["info"]["summary"],
                     published=min((f["upload_time_iso_8601"] for f in d["urls"]), default=None),
                     platform_wheels=sum(n.endswith(".whl") and not n.endswith("-none-any.whl") for n in names),
                     sdist=any(n.endswith((".tar.gz", ".zip")) for n in names))
    return r


if __name__ == "__main__":
    if OUT.exists() and "--refresh" not in sys.argv:
        sys.exit(f"{OUT.name} holds the pinned versions; pass --refresh to look them up again")
    out = {repo: [lookup(k, p) for k, p in v if k in ("npm", "pypi")] for repo, v in D.items() if isinstance(v, list)}
    json.dump({r: v for r, v in out.items() if v}, open(OUT, "w"), indent=1)
    jq = "{full_name, language, owner_type: .owner.type, license: .license.spdx_id, created_at, archived}"
    json.dump({r: json.loads(subprocess.run(["gh", "api", f"repos/{r}", "--jq", jq], capture_output=True, text=True, check=True).stdout)
               for r in D}, open(META, "w"), indent=1)
