"""Load a Windows/Linux plugin and compute CPU/GPU outputs on a Vulkan device."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile


def check(path, registration_only=False):
    import vapoursynth as vs
    core = vs.core
    core.num_threads = 4
    core.std.LoadPlugin(str(path.resolve()))
    functions = {f.name: f.signature for f in core.krigvk.functions()}
    assert set(functions) == {"KrigBilateral", "KrigBilateralGPU"}
    assert all("reference:" not in signature for signature in functions.values())
    assert all("debug:" not in signature for signature in functions.values())
    if registration_only:
        return {"plugin_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "functions": functions, "rendered": False}
    source = core.std.BlankClip(width=16, height=12, format=vs.YUV420P16, color=[16384, 32768, 32768], length=4)
    cpu = core.krigvk.KrigBilateral(source)
    resident = core.krigvk.KrigBilateralGPU(core.std.GPUUpload(source))
    gpu = core.std.GPUDownload(resident)
    assert resident.gpu_resident
    assert not cpu.gpu_resident
    assert cpu.format.id == int(vs.YUV444P16)
    for n in (0, 3, 1):
        with source.get_frame(n) as original, cpu.get_frame(n) as frame, gpu.get_frame(n) as other:
            assert "_ChromaLocation" not in frame.props
            assert frame[0].tobytes() == original[0].tobytes()
            for plane in range(3):
                assert frame[plane].tobytes() == other[plane].tobytes()
            for plane in (1, 2):
                assert all(value == 32768 for row in frame[plane].tolist() for value in row)
    # Float YUV must preserve the standard signed chroma convention.
    for fmt in (vs.YUV420PH, vs.YUV420PS):
        floating=core.std.BlankClip(source,format=fmt,color=[0.25,-0.25,0.25])
        with core.krigvk.KrigBilateral(floating).get_frame(0) as frame:
            assert all(value==-0.25 for row in frame[1].tolist() for value in row)
            assert all(value==0.25 for row in frame[2].tolist() for value in row)
    with core.krigvk.KrigBilateral(source, bits=32).get_frame(0) as frame:
        assert frame.format.id == int(vs.YUV444PS)
        assert "KrigBilateralDebug" not in frame.props
    return {"plugin_sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "cpu_gpu_equal": True,
            "luma_exact": True, "constant_chroma_exact": True, "signed_float_chroma":True, "core": str(core)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plugin", type=Path)
    mode.add_argument("--archive", type=Path)
    parser.add_argument("--registration-only", action="store_true", help="Load and check the API without requesting GPU frames")
    args = parser.parse_args()
    if args.archive:
        with tempfile.TemporaryDirectory(prefix="krig-smoke-") as directory:
            temp = Path(directory)
            with zipfile.ZipFile(args.archive) as archive:
                for name in archive.namelist():
                    relative = Path(name)
                    if relative.is_absolute() or ".." in relative.parts or not name.startswith("krigbilateralvk/"):
                        raise ValueError("Invalid plugin archive path")
                archive.extractall(temp)
            folder = temp / "krigbilateralvk"
            manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
            filename = "krigbilateralvk.dll" if sys.platform == "win32" else "krigbilateralvk.so"
            path = folder / filename
            if hashlib.sha256(path.read_bytes()).hexdigest() != manifest["file_sha256"][path.name]:
                raise ValueError("Plugin hash does not match the manifest")
            # Windows keeps a loaded DLL open until process exit. Test in a
            # child process so the temporary package can be removed afterward.
            command = [sys.executable, str(Path(__file__).resolve()), "--plugin", str(path)]
            if args.registration_only:
                command.append("--registration-only")
            result = json.loads(subprocess.check_output(
                command,
                text=True, encoding="utf-8"
            ))
    else:
        result = check(args.plugin, args.registration_only)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
