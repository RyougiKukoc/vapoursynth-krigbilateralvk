"""Package an explicit Windows DLL and its license/build information."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plugin", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    dll = args.plugin.read_bytes()
    if args.plugin.suffix.lower() != ".dll" or dll[:2] != b"MZ":
        raise ValueError("This package requires a Windows plugin DLL")
    pe_offset = int.from_bytes(dll[0x3C:0x40], "little")
    if dll[pe_offset:pe_offset + 4] != b"PE\0\0" or dll[pe_offset + 4:pe_offset + 6] != b"\x64\x86":
        raise ValueError("Expected an x86-64 PE DLL")
    version = re.search(r"project\(krigbilateralvk VERSION ([\d.]+)", (ROOT / "CMakeLists.txt").read_text()).group(1)
    revision = subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()
    tracked_changes = subprocess.check_output(["git", "-C", str(ROOT), "status", "--porcelain", "--untracked-files=no"], text=True).strip()
    if tracked_changes:
        raise ValueError("Commit source changes before packaging a Release")
    manifest = {
        "name": "krigbilateralvk", "version": version, "source_commit": revision,
        "repository": "https://github.com/RyougiKukoc/vapoursynth-krigbilateralvk",
        "runtime": "VapourSynth R81 / API 4.3, Vulkan 1.4, Windows x86-64",
        "file_sha256": {"krigbilateralvk.dll": hashlib.sha256(dll).hexdigest()},
        "license": "LGPL-3.0-or-later",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.output, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        archive.writestr("krigbilateralvk/krigbilateralvk.dll", dll)
        for filename in ("README.md", "LICENSE", "THIRD_PARTY.md", "licenses/LGPL.txt", "licenses/GPL.txt"):
            archive.write(ROOT / filename, "krigbilateralvk/" + filename)
        archive.writestr("krigbilateralvk/manifest.json", json.dumps(manifest, indent=2) + "\n")
    digest = hashlib.sha256(args.output.read_bytes()).hexdigest()
    args.output.with_suffix(".zip.sha256").write_text(digest + "  " + args.output.name + "\n", encoding="ascii")
    print(json.dumps({"file": args.output.name, "sha256": digest, **manifest}, indent=2))


if __name__ == "__main__":
    main()
