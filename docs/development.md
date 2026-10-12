# Development and validation

## Shader sources

`tools/generate_shaders.py` reads `reference/KrigBilateral.glsl` and writes `src/vertical.comp`, `src/horizontal.comp`, `src/reconstruct.comp` and the embedded `src/shaders.h`. Regenerate after editing the generator or reference:

```sh
python tools/generate_shaders.py
python tools/check_sources.py
```

The generated files are committed so a normal CMake build needs neither NumPy nor a shader compiler. `check_sources.py` verifies syntax, documentation links, reference hashes and byte-for-byte regeneration in a temporary copy. The host creates pipelines from GLSL compiled by VapourSynth's core.

The host shares the core Vulkan device and uses its execution pool to retain input frames and scratch buffers until submissions complete. Three compute passes produce the guide and reconstruct U/V; barriers order writes before subsequent reads. The output shares the input Y plane. Each filter has its own pool and immutable row weights.

## Running comparisons

Install NumPy in the same Python environment as VapourSynth. Tests discover a native library in `build/` or `build-cmake/`; set `KRIGVK_PLUGIN` to select another file, such as an extracted Release DLL.

```sh
python tests/validate_matrix.py --output results/format-matrix.json
python tests/compare.py --width 128 --height 72 --output results/comparison.json
python tests/inspect_stages.py --width 34 --height 26
```

The format matrix checks luma preservation, frame properties, dimensions/frame rate, small sizes, workgroup tails, finite output, CPU/GPU entry consistency, invalid inputs, and concurrent requests across two instances. It records differences from the NumPy reference rather than claiming that all floating-point cases must match exactly. `compare.py` also compares to `placebo.Shader` when that optional plugin is available and the input is 16-bit integer.

Real-video tests accept external **1920×1080 YUV420P16 little-endian** raw input. They do not decode or distribute the video:

```sh
python tests/compare_real.py --raw sample.yuv --output results/real.json --frames 3
python tests/run_benchmarks.py --raw sample.yuv --results results/benchmark
```

`compare_real.py` samples distinct positions across the input and compares all pixels of every output plane. It reports maximum error, RMS, differing-sample counts and output hashes. `run_benchmarks.py` preloads eight positions across the video and repeats them under new output frame numbers. It serializes three fresh-process repetitions with rotated backend order; decoding/preloading/JIT are outside the timed section, required transfers and completed CPU frames are inside. Optional NVIDIA telemetry is written beside each run.

The original placebo comparison requires the optional `placebo` plugin. Test scripts preserve existing comparison/benchmark output files; use a new filename or directory for each run. The historical report is [validation.md](validation.md).

## Diagnostics

Both plugin entry points accept `debug`; diagnostic output is YUV444PS with normalized Y in plane 0:

| `debug` | Plane U | Plane V |
|---|---|---|
| `0` | Normal reconstructed U | Normal reconstructed V |
| `1` | Vertical guide mean | Vertical guide variance |
| `2` | Horizontal guide mean | Horizontal guide variance |
| `3` | Local variance | Target luma |
| `4` | Covariance `C(0,1)` | Target covariance `c(0)` |
| `5` | Reconstructed U before clipping/quantization | Reconstructed V before clipping/quantization |
| `6` | First neighbor's guide mean | First neighbor's guide variance |
| `7` | Exponential probe | Reciprocal square-root probe |

Set `KRIGVK_DUMP_SPIRV` to a writable directory before creating a node to save its pipeline modules. Subsequent instances overwrite the same three filenames. Validate with:

```sh
spirv-val --target-env vulkan1.4 vertical.spv
spirv-val --target-env vulkan1.4 horizontal.spv
spirv-val --target-env vulkan1.4 reconstruct.spv
```

`VS_VULKAN_VALIDATION=1` asks the core to enable the Khronos validation layer. Install the layer before relying on it; the historical measurements had no layer installed. Diagnostics and validation runs should be separate from throughput measurements.

## CI and packages

GitHub Actions compiles Windows x86-64 and Linux x86-64 libraries against pinned R81 headers and checks source regeneration. Hosted runners are used for compilation; those checks are not GPU runtime verification.

Package an explicitly selected Windows DLL with the licenses and build information:

```sh
python tools/package_release.py --plugin build/krigbilateralvk.dll --output dist/krigbilateralvk-0.1.0-windows-x86_64.zip
python tools/smoke.py --archive dist/krigbilateralvk-0.1.0-windows-x86_64.zip
```

The smoke command runs on a machine with VapourSynth R81 and a usable Vulkan device. It checks the packaged DLL's hash, constant-color reconstruction, luma preservation, format, and CPU/GPU entry equality. Release source is provided by the repository's matching tag; the plugin zip contains the DLL, licenses, installation notes and a manifest. No graphics-driver or VapourSynth DLLs are bundled.
