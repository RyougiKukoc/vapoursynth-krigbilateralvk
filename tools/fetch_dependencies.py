"""Fetch pinned headers into new directories, preserving existing worktrees."""
import argparse
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
SOURCES = {
    "vulkan-headers": ("https://github.com/KhronosGroup/Vulkan-Headers.git", "e3b1eec08173d6b825cd3ac88c885a63b621504a"),
    "vapoursynth": ("https://github.com/vapoursynth/vapoursynth.git", "dd11a9da6f8e2bb24ab4bb084d44bf01fb93612a"),
}


def fetch(path, url, commit):
    def git(*arguments):
        return subprocess.check_output(
            ["git", "-c", "core.autocrlf=false", "-c", "core.eol=lf", "-C", str(path), *arguments],
            text=True, encoding="utf-8"
        ).strip()

    if not path.exists():
        path.mkdir(parents=True)
        git("init")
        git("config", "core.autocrlf", "false")
        git("config", "core.eol", "lf")
        git("remote", "add", "origin", url)
        git("fetch", "--depth", "1", "origin", commit)
        git("checkout", "--detach", "FETCH_HEAD")
    if git("rev-parse", "HEAD") != commit or git("status", "--porcelain"):
        raise RuntimeError(f"{path}: existing dependency differs from the pin; select a new destination")
    print(f"Verified {path.name} {commit}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, default=ROOT / "dependencies")
    parser.add_argument("--with-vapoursynth", action="store_true", help="Also fetch the standalone R81 headers for CI or SDK-only builds")
    args = parser.parse_args()
    names = ["vulkan-headers"] + (["vapoursynth"] if args.with_vapoursynth else [])
    for name in names:
        fetch(args.destination / name, *SOURCES[name])


if __name__ == "__main__":
    main()
