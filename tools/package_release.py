"""Package an explicit Windows/Linux x86-64 plugin with licenses and provenance."""
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
    parser.add_argument("--max-glibc", help="Check Linux release ABI and static C++ runtime closure")
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    binary = args.plugin.read_bytes()
    if args.plugin.suffix.lower() == ".dll":
        if len(binary) < 64 or binary[:2] != b"MZ":
            raise ValueError("Expected a Windows plugin DLL")
        pe_offset = int.from_bytes(binary[0x3C:0x40], "little")
        if binary[pe_offset:pe_offset + 4] != b"PE\0\0" or binary[pe_offset + 4:pe_offset + 6] != b"\x64\x86":
            raise ValueError("Expected an x86-64 PE DLL")
        platform, filename = "windows-x86_64", "krigbilateralvk.dll"
    elif args.plugin.suffix.lower() == ".so":
        if (len(binary) < 64 or binary[:6] != b"\x7fELF\x02\x01"
                or binary[16:20] != b"\x03\x00\x3e\x00"):
            raise ValueError("Expected a little-endian x86-64 ELF shared library")
        platform, filename = "linux-x86_64", "krigbilateralvk.so"
    else:
        raise ValueError("Select a Windows .dll or Linux .so")
    if args.max_glibc:
        if platform != "linux-x86_64":
            raise ValueError("--max-glibc requires a Linux .so")
        versions = subprocess.check_output(["readelf", "--version-info", str(args.plugin)], text=True)
        required = [tuple(map(int, v.split("."))) for v in re.findall(r"GLIBC_(\d+(?:\.\d+)+)", versions)]
        maximum = tuple(map(int, args.max_glibc.split(".")))
        if not required or max(required) > maximum:
            raise ValueError(f"GLIBC requirements exceed {args.max_glibc}: {required}")
        dynamic = subprocess.check_output(["readelf", "-d", str(args.plugin)], text=True)
        needed = set(re.findall(r"\(NEEDED\).*\[([^]]+)\]", dynamic))
        if needed - {"libc.so.6", "libm.so.6", "libpthread.so.0", "libdl.so.2", "librt.so.1", "ld-linux-x86-64.so.2"}:
            raise ValueError(f"Unexpected dynamic runtime dependencies: {sorted(needed)}")
    version = re.search(r"project\(krigbilateralvk VERSION ([\d.]+)", (ROOT / "CMakeLists.txt").read_text()).group(1)
    revision = subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()
    tracked_changes = subprocess.check_output(["git", "-C", str(ROOT), "status", "--porcelain", "--untracked-files=no"], text=True).strip()
    if tracked_changes:
        raise ValueError("Commit source changes before packaging a Release")
    manifest = {
        "name": "krigbilateralvk", "version": version, "source_commit": revision,
        "repository": "https://github.com/RyougiKukoc/vapoursynth-krigbilateralvk",
        "platform": platform,
        "runtime": "VapourSynth R81 / API 4.3, Vulkan 1.4, " + platform,
        "file_sha256": {filename: hashlib.sha256(binary).hexdigest()},
        "license": "LGPL-3.0-or-later",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.output, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        archive.writestr("krigbilateralvk/" + filename, binary)
        archive.writestr("krigbilateralvk/manifest.vs", "[VapourSynth Manifest V1]\nkrigbilateralvk\n")
        for filename in ("README.md", "LICENSE", "THIRD_PARTY.md", "licenses/LGPL.txt", "licenses/GPL.txt"):
            archive.write(ROOT / filename, "krigbilateralvk/" + filename)
        archive.writestr("krigbilateralvk/manifest.json", json.dumps(manifest, indent=2) + "\n")
    digest = hashlib.sha256(args.output.read_bytes()).hexdigest()
    args.output.with_suffix(".zip.sha256").write_text(digest + "  " + args.output.name + "\n", encoding="ascii")
    print(json.dumps({"file": args.output.name, "sha256": digest, **manifest}, indent=2))


if __name__ == "__main__":
    main()
