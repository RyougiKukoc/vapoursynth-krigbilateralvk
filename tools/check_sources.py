"""Validate source syntax, local links, evidence integrity and generated shaders."""
import ast
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    excluded = {".git", "dependencies", "__pycache__", "dist", "results"}
    files = [p for p in ROOT.rglob("*") if p.is_file()
             and not any(part in excluded or part.startswith("build") for part in p.relative_to(ROOT).parts[:-1])]
    for path in files:
        if path.suffix == ".py":
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if path.suffix == ".json":
            json.loads(path.read_text(encoding="utf-8"))
        if path.suffix == ".md":
            for target in re.findall(r"\]\(([^)]+)\)", path.read_text(encoding="utf-8")):
                if target.startswith(("https://", "http://", "#")):
                    continue
                if not (path.parent / target.split("#")[0]).exists():
                    raise ValueError(f"{path}: missing {target}")
    evidence = json.loads((ROOT / "docs/validation/evidence-manifest.json").read_text(encoding="utf-8"))
    for relative, expected in evidence["published_files_sha256"].items():
        if hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() != expected:
            raise ValueError(f"{relative}: published evidence changed")
    for relative, expected in evidence["reference_files_sha256"].items():
        if hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() != expected:
            raise ValueError(f"{relative}: algorithm reference changed")
    with tempfile.TemporaryDirectory(prefix="krig-sources-") as directory:
        temp = Path(directory)
        for name in ("tools", "reference", "src"):
            shutil.copytree(ROOT / name, temp / name, ignore=shutil.ignore_patterns("__pycache__"))
        subprocess.run([sys.executable, str(temp / "tools/generate_shaders.py")], check=True)
        for name in ("vertical.comp", "horizontal.comp", "reconstruct.comp", "shaders.h"):
            if (ROOT / "src" / name).read_bytes() != (temp / "src" / name).read_bytes():
                raise ValueError(f"{name}: regenerate the committed shader sources")
    print(f"Checked {len(files)} files, documentation links, evidence hashes and shader regeneration")


if __name__ == "__main__":
    main()
