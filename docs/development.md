# Development and validation

## Shader sources

`tools/generate_shaders.py` reads `reference/KrigBilateral.glsl` and writes `src/vertical.comp`, `src/horizontal.comp`, `src/reconstruct.comp` and the embedded `src/shaders.h`. Regenerate after editing the generator or reference:

```sh
python tools/generate_shaders.py
python tools/check_sources.py
```

The generated files are committed so a normal CMake build needs neither NumPy nor a shader compiler. `check_sources.py` verifies syntax, documentation links, reference hashes and byte-for-byte regeneration in a temporary copy. The host creates pipelines from GLSL compiled by VapourSynth's core.

The host shares the core Vulkan device and uses its execution pool to retain input frames and scratch buffers until submissions complete. Three compute passes produce the guide and reconstruct U/V; barriers order writes before subsequent reads. The output shares the input Y plane. Each filter has its own pool. All three algorithm passes execute on the GPU.

## Running comparisons

Install NumPy in the same Python environment as VapourSynth. Tests discover a native library in `build/` or `build-cmake/`; set `KRIGVK_PLUGIN` to select another file, such as an extracted Release DLL.

```sh
python tests/shader_reference.py --cxx /path/to/g++
python tests/validate_matrix.py --output results/format-matrix.json
python tests/validate_contract.py --output results/input-output-contract.json
python tests/validate_depth.py --output results/range-depth-dither.json
python tests/compare.py --exact --width 128 --height 72 --output results/comparison.json
python tests/compare_shader.py --exact --width 34 --height 26 --output results/fp32-comparison.json
```

The format matrix compares every output sample to the original GLSL algorithm, including byte-level equality for FP16/FP32. It also checks luma preservation, frame properties, dimensions/frame rate, small sizes, workgroup tails, finite output, CPU/GPU entry consistency, removed/invalid arguments and concurrent requests across two instances.

`validate_contract.py` separately tests node/frame rejection, removed arguments, variable format/resolution, all integer depths 8..16, signed float chroma boundaries/neutral values, normal output metadata, and the core's automatic residency adapters. `validate_depth.py` checks full/limited endpoints against independent code-value expectations, per-frame range changes, every output depth, actual dither effects on fractional gradients, equality with a separate core resize of FP32 reconstruction, CPU/GPU entry parity, float headroom and invalid options. The GLSL reference harness uses the same range-aware resize boundary before executing the original algorithm; the equations and solver are unchanged.

`tests/shader_reference.py` builds a test-only library from the original hook bodies. The reference keeps the original covariance expressions, packed matrix indexing and loops; it does not import generated production shaders. Host frame transport is shared to keep input/output handling identical. The harness adapts mpv texture macros to common clamped texel access, fixes the common sample coordinates and stores guides as FP32. No libplacebo rendering pipeline or Python algorithm is used in this comparison.

`compare_shader.py` compares normal input-depth output and FP32 reconstruction against the original GLSL oracle. `--exact` fails on any differing sample bits; per-plane hashes are recorded for both implementations. Production diagnostic outputs were removed in v0.4.0. Historical stage reports remain evidence for their measured versions. This is a device-specific numerical check, not a cross-driver proof.

Real-video tests accept external **1920×1080 YUV420P16 little-endian** raw input. They do not decode or distribute the video:

```sh
python tests/compare_real.py --exact --raw sample.yuv --output results/real.json --frames 3
python tests/build_bench_wait.py --cxx /path/to/g++
python tests/run_benchmarks.py --raw sample.yuv --results results/benchmark
```

`compare_real.py` samples distinct positions across the input and compares all pixels of every output plane. It reports maximum error, RMS, differing-sample counts and output hashes. `run_benchmarks.py` preloads eight positions across the video and repeats them under new output frame numbers. It serializes three fresh-process repetitions with rotated backend order. `--cases` accepts a JSON list of `name`, `backend`, optional `plugin`, `bits`, `sample_type`, `dither_type` and `frames`. Decoding/preloading/JIT are outside timing; each timed output uses a new frame number. The source template uses `keep=True` to avoid generating an unused full-size blank frame on every request. Optional NVIDIA telemetry is written beside each run.

CPU-entry timings include upload, filtering, conversion and completed full CPU output. For resident timings, eight input frames are uploaded and completed before timing. The separate test-only `bench_wait.cpp` library calls the core's `waitGPUFrame` on every output and returns a 1x1 CPU acknowledgement. This includes completion-wait overhead but transfers no output pixels; it is not a kernel-only timestamp. The sink is never packaged with the production plugin. `baseline` and `baseline_gpu` cases measure source delivery/control overhead without KrigBilateral processing.

The throughput comparison with the complete original pipeline requires the optional `placebo` plugin. Shader-algorithm equality tests do not require it. Test scripts preserve existing comparison/benchmark output files; use a new filename or directory for each run. Set `KRIGVK_ORACLE` to select a separately built reference library when comparing a Release DLL.

## Shader validation

Set `KRIGVK_DUMP_SPIRV` to a writable directory before creating a node to save its pipeline modules. Subsequent instances overwrite the same three filenames. Validate with:

```sh
spirv-val --target-env vulkan1.4 vertical.spv
spirv-val --target-env vulkan1.4 horizontal.spv
spirv-val --target-env vulkan1.4 reconstruct.spv
```

`VS_VULKAN_VALIDATION=1` asks the core to enable the Khronos validation layer. Install the layer before relying on it; the historical measurements had no layer installed. Diagnostics and validation runs should be separate from throughput measurements.

## CI and packages

GitHub Actions compiles Windows x86-64 and Linux x86-64 libraries against pinned R81 headers and checks source regeneration. Linux releases build in manylinux_2_28 with static C++/GCC runtimes and hidden archive symbols; packaging rejects dependencies outside the system C runtime and a GLIBC requirement above 2.28. A matching `v<version>` tag publishes both platform ZIPs and SHA-256 files after packaged-library registration checks. Hosted registration checks do not claim physical GPU rendering; see [platform validation](validation.md#windows-and-linux-runtime-validation).

Package an explicitly selected Windows DLL or Linux SO with licenses, an autoload manifest and build information, after committing the source:

```sh
python tools/package_release.py --plugin build/krigbilateralvk.dll --output dist/krigbilateralvk-0.4.0-windows-x86_64.zip
python tools/smoke.py --archive dist/krigbilateralvk-0.4.0-windows-x86_64.zip
# Linux:
python tools/package_release.py --plugin build/krigbilateralvk.so --output dist/krigbilateralvk-0.4.0-linux-x86_64.zip
python tools/smoke.py --archive dist/krigbilateralvk-0.4.0-linux-x86_64.zip
```

The smoke command runs on a machine with VapourSynth R81 and a usable Vulkan device. It checks the packaged library's hash, constant-color reconstruction, signed floating-point chroma, luma preservation, format, and CPU/GPU entry equality. Use `--registration-only` when checking package loading on a host without a usable Vulkan device. Run archive smoke tests in a clean environment without another autoloaded copy of this plugin. Release source is provided by the repository's matching tag; the ZIP contains the native library, licenses, installation notes and manifests. No graphics-driver or VapourSynth libraries are bundled. Pip users should use the [central api4-wheels package](https://github.com/AliceTeaParty/vapoursynth-api4-wheels/tree/main/plugins/vapoursynth-krigbilateralvk).
