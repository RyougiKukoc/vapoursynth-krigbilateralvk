"""Locate a test build or an explicitly selected Release library."""
import os
from pathlib import Path
import vapoursynth as vs

ROOT = Path(__file__).resolve().parents[1]


def plugin_path():
    override = os.environ.get("KRIGVK_PLUGIN")
    if override:
        path = Path(override).resolve()
        if not path.is_file():
            raise FileNotFoundError(path)
        return path
    suffix = ".dll" if os.name == "nt" else ".so"
    candidates = [ROOT / folder / ("krigbilateralvk" + suffix) for folder in ("build", "build-cmake")]
    for path in candidates:
        if path.is_file():
            return path
    raise FileNotFoundError("Build the plugin first or set KRIGVK_PLUGIN to a native library")


def load_plugin():
    path = plugin_path()
    vs.core.std.LoadPlugin(str(path))
    return path
