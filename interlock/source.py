"""Fetch provider source for reading only. Nothing here executes provider code."""
from __future__ import annotations
import hashlib, shutil, subprocess, tarfile, zipfile
from pathlib import Path

def _unpack(archive: Path, dest: Path) -> None:
    if archive.suffix in (".tgz", ".gz"):
        with tarfile.open(archive) as t:
            t.extractall(dest, filter="data")
    else:
        with zipfile.ZipFile(archive) as z:
            z.extractall(dest)

def fetch_source(kind: str, package: str, version: str, cache_dir: Path, run=subprocess.run) -> Path:
    """Return a directory holding the unpacked sources of package@version."""
    cache_dir = Path(cache_dir)
    slug = f"{kind}_{package.replace('/', '_').lstrip('@')}_{version}"
    dest = cache_dir / slug
    if dest.exists():
        return dest
    work = cache_dir / f".work_{slug}"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    if kind == "npm":
        run(["npm", "pack", f"{package}@{version}", "--silent"], cwd=str(work), capture_output=True, text=True, check=True)
    elif kind == "pypi":
        run(["pip", "download", "--no-deps", "--no-build-isolation", f"{package}=={version}", "-d", str(work)],
            capture_output=True, text=True, check=True)
    elif kind == "git":
        run(["git", "clone", "--depth", "1", "--branch", version, package, str(work / "repo")],
            capture_output=True, text=True, check=True)
    else:
        raise ValueError(f"unknown source kind: {kind}")
    unpacked = work / "unpacked"
    unpacked.mkdir()
    for archive in list(work.glob("*.tgz")) + list(work.glob("*.whl")) + list(work.glob("*.tar.gz")):
        _unpack(archive, unpacked)
    roots = [p for p in unpacked.iterdir()] if unpacked.exists() else []
    root = roots[0] if len(roots) == 1 and roots[0].is_dir() else (work / "repo" if (work / "repo").exists() else unpacked)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(root), str(dest))
    shutil.rmtree(work, ignore_errors=True)
    return dest

def source_digest(path: Path) -> str:
    h = hashlib.sha256()
    for f in sorted(Path(path).rglob("*")):
        if f.is_file():
            h.update(str(f.relative_to(path)).encode())
            h.update(f.read_bytes())
    return h.hexdigest()
