# KrigBilateral Vulkan for VapourSynth

A native Vulkan implementation of **KrigBilateral**, the luma-guided chroma upscaler by Shiandow. It reconstructs YUV 4:2:0 chroma at full luma resolution and returns YUV 4:4:4 at the original size and bit depth. The luma plane is preserved exactly.

The plugin runs three compute passes on VapourSynth's Vulkan device. It provides both a convenient CPU-frame interface and a GPU-resident interface for processing chains. NumPy is used only by optional comparison tests; neither NumPy nor libplacebo is a runtime dependency.

## Recommended installation: api4-wheels

Install the Windows or Linux x86-64 wheel from [api4-wheels](https://github.com/AliceTeaParty/vapoursynth-api4-wheels) into the Python environment used by VapourSynth:

```console
python -m pip install --extra-index-url https://aliceteaparty.github.io/vapoursynth-api4-wheels/simple/ "vapoursynth-krigbilateralvk==0.2.2+alice.1"
```

This is the recommended installation method. It installs the native plugin, autoload manifest and licenses, and selects the matching VapourSynth R81 dependency. No compiler or manual DLL/SO copying is needed. Use Python 3.12 or newer; Linux wheels require glibc 2.28 or newer. A Vulkan 1.4 GPU and driver are still required for rendering.

The native source is maintained here and included in api4-wheels as a git submodule. The central repository owns pip packaging, Windows/Linux wheel publication and the package index. After installation, `vs.core.krigvk` is available automatically; see [usage](#usage).

## Requirements

- **VapourSynth R81 / API 4.3**, with `std.GPUUpload`, `std.GPUDownload` and the core Vulkan API.
- A GPU and driver supporting **Vulkan 1.4** and VapourSynth's required device features.
- Constant-format YUV 4:2:0 input with even width and height.
- Windows or Linux x86-64 for prebuilt packages. GPU runtime validation has passed on Windows with an RTX 3090 Ti and Linux with an NVIDIA A100. Other platforms require a source build and remain unvalidated.

The initial binary targets the **R81 release headers**. The GPU interface evolves with VapourSynth: when changing core versions, rebuild against matching headers rather than assuming GPU ABI compatibility.

## Manual installation

For manual installation, download `krigbilateralvk-0.2.2-windows-x86_64.zip` or `krigbilateralvk-0.2.2-linux-x86_64.zip` from [Releases](https://github.com/RyougiKukoc/vapoursynth-krigbilateralvk/releases). Extract it and load the native library explicitly:

```python
import vapoursynth as vs

core = vs.core
core.std.LoadPlugin(r"C:\plugins\krigbilateralvk\krigbilateralvk.dll")
# Linux: core.std.LoadPlugin("/path/to/krigbilateralvk/krigbilateralvk.so")
```

Alternatively, place the extracted `krigbilateralvk` directory in your VapourSynth plugin autoload directory. Use one copy per environment to avoid duplicate plugin registrations. Both release binaries include their C++ runtime; Linux releases are built for glibc 2.28 or newer. VapourSynth and a Vulkan-capable graphics driver must already be installed.

## Usage

For an ordinary VapourSynth clip:

```python
# source is an integer YUV420 clip, for example YUV420P16.
output = core.krigvk.KrigBilateral(source)
output.set_output()
```

This interface returns CPU frames and automatically adds upload and download nodes. The reconstruction itself runs on the GPU.

For a GPU-resident chain:

```python
gpu_source = core.std.GPUUpload(source)
gpu_output = core.krigvk.KrigBilateralGPU(gpu_source)

# Pass gpu_output to another GPU filter, or download when CPU frames are needed.
output = core.std.GPUDownload(gpu_output)
```

### API

```python
core.krigvk.KrigBilateral(clip, debug=0)
core.krigvk.KrigBilateralGPU(clip, debug=0)
```

| Argument | Default | Meaning |
|---|---|---|
| `clip` | required | YUV420 input. `KrigBilateral` accepts CPU frames; `KrigBilateralGPU` accepts GPU-resident frames. |
| `debug` | `0` | Normal output at `0`. Values `1..7` expose intermediate results as YUV444PS; see [diagnostics](docs/development.md#diagnostics). |

With `debug=0`, output is YUV444 with the input's dimensions, sample type, bit depth, frame count and frame rate. Luma is shared unchanged. Frame properties, including range, colorimetry and timestamps, are copied except `_ChromaLocation`, which is removed because chroma is no longer subsampled. Diagnostic output also removes colorimetry/range tags and sets `KrigBilateralDebug` to the selected stage.

### Sample formats and chroma placement

Integer input supports every bit depth from 8 to 16. Values are normalized by the format's full integer range without limited/full-range color conversion. The input `_Range` is preserved; reconstructed integer U/V are clipped to the storage range and rounded to nearest, ties to even.

The filter uses fixed **left chroma placement** and progressive sampling geometry. An absent `_ChromaLocation` or `_FieldBased` is assumed to mean left or progressive respectively; if present, each must be a single integer equal to `0`. Other placement/field tags fail when the affected frame is requested. Resite chroma or deinterlace/separate fields before filtering if required. The filter performs only 4:2:0 → 4:4:4 reconstruction, not arbitrary resizing.

FP16 and FP32 use the **standard VapourSynth float YUV convention**: Y in `[0, 1]`, U/V in `[-0.5, 0.5]`. The transport maps signed U/V to the shader's `[0, 1]` samples and maps reconstruction back to signed chroma. Luma stays bit-exact; reconstructed U/V are clipped to `[-0.5, 0.5]`. FP16 requires the device's `shaderFloat16` feature, otherwise node creation fails with a specific error.

Node creation rejects variable format/resolution, non-YUV420 input, nonpositive/odd dimensions, unsupported storage types and `debug` outside `0..7`, including integers that would overflow a 32-bit cast. Frame evaluation checks the actual format, plane dimensions, strides and Vulkan buffer sizes before dispatching. Finite samples are required; the filter does not scan every input pixel to reject NaN/Inf or nominal-range violations. Range overshoot in reconstructed chroma is clipped as described above.

## Algorithm fidelity

Version 0.2.0 uses a single implementation derived directly from Shiandow's original GLSL. It retains the weighted moments, covariance equations, packed matrix, fixed elimination and back-substitution order. Guide buffers store FP32 values; the plugin does not introduce half-precision truncation or emulate NumPy arithmetic.

The comparison harness executes the original shader's hook bodies without rewriting the covariance or solver. Both programs receive the same texel inputs, coordinates, FP32 intermediate storage and output format. This isolates the **KrigBilateral algorithm** from renderer-specific texture interpolation and color conversions.

On the tested RTX 3090 Ti, all 32 format/size configurations (256 synthetic frames) were bit-exact, including integer, FP16 and FP32 output. Tests of intermediate stages and three distinct 1080p frames also matched byte-for-byte, including unquantized FP32 reconstruction. See [the validation report](docs/validation.md) for scope and evidence.

This is measured parity with the original GLSL algorithm under common inputs, not a claim that every GPU/driver produces identical results or that the complete `placebo.Shader` rendering pipeline has identical output.

The former Python implementation, arithmetic-emulation path and `reference` argument have been removed. Calls using `reference=` must drop that argument.

## Performance

The native algorithm and the original `placebo.Shader` pipeline are measured in three serial, fresh-process runs on the same 1080p YUV420P16 workload. Timings include completed CPU frames and required transfers; decoding, preloading and shader compilation are excluded.

| Backend | Median fps | Three-run range |
|---|---:|---:|
| Vulkan, original shader algorithm | **196.48** | 196.44–196.64 |
| Original placebo.Shader pipeline | 103.63 | 102.42–105.22 |

Current results, repeat ranges, environment and workload details are in [the performance report](docs/validation.md#performance). The source-algorithm parity test and the whole-renderer throughput test serve different purposes: the latter includes placebo's rendering work.

## Building from source

Use a C++20 compiler, CMake 3.20+ and Ninja. No Vulkan loader library or standalone GLSL compiler is needed: the plugin resolves Vulkan functions from the core, which also compiles the shaders at node creation.

```sh
git clone https://github.com/RyougiKukoc/vapoursynth-krigbilateralvk.git
cd vapoursynth-krigbilateralvk
python tools/fetch_dependencies.py
cmake -S . -B build -G Ninja -DCMAKE_BUILD_TYPE=Release
cmake --build build
```

The Python selected by CMake must have VapourSynth R81 installed. Select it with `-DPython3_EXECUTABLE=/path/to/python`; select your compiler with `-DCMAKE_CXX_COMPILER=/path/to/compiler` if necessary. Windows MinGW builds statically link the C++ runtime. The output is `build/krigbilateralvk.dll` on Windows or `build/krigbilateralvk.so` on Linux.

For an SDK-only build without importing VapourSynth:

```sh
python tools/fetch_dependencies.py --with-vapoursynth
cmake -S . -B build -G Ninja -DCMAKE_BUILD_TYPE=Release -DVAPOURSYNTH_INCLUDE_DIR=dependencies/vapoursynth/include
cmake --build build
```

The header script pins VapourSynth R81 and Vulkan-Headers SDK 1.4.357.0. You can instead set `VAPOURSYNTH_INCLUDE_DIR` and `VULKAN_HEADERS` to your own matching include directories. See [development and tests](docs/development.md) for numerical comparisons, shader generation and release packaging.

## License and credits

KrigBilateral was written by **Shiandow**. The [original GLSL](reference/KrigBilateral.glsl) and its license notice are preserved. This plugin and its adaptations are distributed under **LGPL-3.0-or-later**; see [LICENSE](LICENSE) and [GPLv3](licenses/GPL.txt).

Component origins and header licenses are listed in [THIRD_PARTY.md](THIRD_PARTY.md).
