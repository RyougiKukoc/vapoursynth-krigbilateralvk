# KrigBilateral Vulkan for VapourSynth

A native Vulkan implementation of **KrigBilateral**, the luma-guided chroma upscaler by Shiandow. It reconstructs YUV 4:2:0 chroma at full luma resolution and returns YUV 4:4:4 at the original size. Output sample type and bit depth default to the input; both can be selected explicitly. At unchanged type/depth, the original luma plane is preserved exactly.

The plugin runs three compute passes on VapourSynth's Vulkan device. It provides both a convenient CPU-frame interface and a GPU-resident interface for processing chains. NumPy is used only by optional comparison tests; neither NumPy nor libplacebo is a runtime dependency.

## Recommended installation: api4-wheels

Install the Windows or Linux x86-64 wheel from [api4-wheels](https://github.com/AliceTeaParty/vapoursynth-api4-wheels) into the Python environment used by VapourSynth:

```console
python -m pip install --extra-index-url https://aliceteaparty.github.io/vapoursynth-api4-wheels/simple/ vapoursynth-krigbilateralvk
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

For manual installation, download `krigbilateralvk-0.4.0-windows-x86_64.zip` or `krigbilateralvk-0.4.0-linux-x86_64.zip` from [Releases](https://github.com/RyougiKukoc/vapoursynth-krigbilateralvk/releases). Extract it and load the native library explicitly:

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
core.krigvk.KrigBilateral(clip, bits=None, sample_type=None, dither_type=None)
core.krigvk.KrigBilateralGPU(clip, bits=None, sample_type=None, dither_type=None)
```

| Argument | Default | Meaning |
|---|---|---|
| `clip` | required | YUV420 input. `KrigBilateral` accepts CPU frames; `KrigBilateralGPU` accepts GPU-resident frames. |
| `bits` | input depth | Output integer depth `8..16`, or float depth `16/32`. `32` selects float; depths below `16` select integer. At `16`, the input sample type is retained unless `sample_type` is supplied. |
| `sample_type` | inferred as above | `vs.INTEGER` or `vs.FLOAT`. Use this to distinguish integer 16-bit from FP16. |
| `dither_type` | `"none"` | Core resize mode: `"none"`, `"ordered"`, `"random"`, or `"error_diffusion"`. Python `None` selects the default. Dithering applies only to integer output. |

Output is YUV444 with the input's dimensions, frame count and frame rate. At unchanged type/depth, luma is shared unchanged even when chroma dithering is requested. When type/depth changes, luma is converted directly from the original input by core resize with the selected dither mode. Range, colorimetry, timestamps and custom properties are retained; `_ChromaLocation` is removed because chroma is no longer subsampled.

For example:

```python
# Keep reconstructed chroma in FP32 instead of quantizing it to the input depth.
floating = core.krigvk.KrigBilateral(source, bits=32)

# Select integer output explicitly when the input is floating point.
integer = core.krigvk.KrigBilateral(floating_source, bits=16, sample_type=vs.INTEGER)

# Quantize to 8-bit with error diffusion in the same call.
eight_bit = core.krigvk.KrigBilateral(source, bits=8, dither_type="error_diffusion")
```

Both entries use the same GPU resize conversions for `none`, `ordered` and `random`, avoiding CPU/GPU conversion rounding differences. The CPU-frame entry adds upload/download nodes; the GPU entry keeps ordinary conversion and reconstruction resident. R81 does not implement GPU error diffusion, so requesting that mode for integer output explicitly adds CPU resize. Transfers restore the requested output residency. Other core resize errors are propagated.

### Sample formats and chroma placement

Input supports every integer depth from 8 to 16, FP16 and FP32. `resize.Point` converts input to canonical YUV420PS before reconstruction and converts the FP32 YUV444 result to the requested output format afterward. Dimensions and subsampling stay unchanged during these boundary conversions; KrigBilateral performs the chroma reconstruction. The plugin no longer divides integer samples by `2^bits-1` or implements its own integer rounding.

Resize reads each frame's `_Range` (`0=limited`, `1=full`; the deprecated `_ColorRange` alias uses inverse numbering). Untagged YUV follows resize's **limited-range default**; format and bit depth alone cannot identify whether an untagged clip is full-range. Tag such input correctly, for example `core.std.SetFrameProps(source, _Range=1)`. The output retains the input range. For limited 8-bit input, luma codes 16 and 235 map to 0 and 1, and chroma code 128 maps to zero; full-range input uses its own scaling and the same neutral chroma center.

The filter uses fixed **left chroma placement** and progressive sampling geometry. An absent `_ChromaLocation` or `_FieldBased` is assumed to mean left or progressive respectively; if present, each must be a single integer equal to `0`. Other placement/field tags fail when the affected frame is requested. Resite chroma or deinterlace/separate fields before filtering if required. The filter performs only 4:2:0 → 4:4:4 reconstruction, not arbitrary resizing.

FP16 and FP32 use the **standard VapourSynth float YUV convention**: nominal Y in `[0, 1]`, U/V in `[-0.5, 0.5]`. Computation and intermediate buffers are FP32 for every input type. The transport only adds/subtracts 0.5 to adapt signed chroma to the original GLSL coordinates. Finite headroom/footroom is retained through reconstruction; final integer clipping, rounding and dithering are handled by resize. FP16 conversions in a GPU-resident chain depend on the core's device support.

Node creation rejects variable format/resolution, non-YUV420 input, nonpositive/odd dimensions, unsupported storage/output types and invalid options, including overflowing integers. Original frame metadata is checked before resize can rewrite it. Frame evaluation checks the actual format, plane dimensions, strides and Vulkan buffer sizes before dispatching. Finite samples are required; the filter does not scan every input pixel to reject NaN/Inf.

## Algorithm fidelity

Version 0.2.0 uses a single implementation derived directly from Shiandow's original GLSL. It retains the weighted moments, covariance equations, packed matrix, fixed elimination and back-substitution order. Guide buffers store FP32 values; the plugin does not introduce half-precision truncation or emulate NumPy arithmetic.

The comparison harness executes the original shader's hook bodies without rewriting the covariance or solver. Both programs receive the same texel inputs, coordinates, FP32 intermediate storage and core resize conversions. This isolates the **KrigBilateral algorithm** from renderer-specific texture interpolation and color conversions. Version 0.3.0 changes the format/range boundary, not the covariance or solver: its integer results need not match v0.2.x, which used storage-range normalization.

On the tested RTX 3090 Ti, all 32 format/size configurations (256 synthetic frames) were bit-exact, including integer, FP16 and FP32 output. The comparison also checks FP32 reconstruction without integer quantization. Historical intermediate-stage and 1080p comparisons remain recorded for their measured versions. See [the validation report](docs/validation.md) for scope and evidence.

This is measured parity with the original GLSL algorithm under common inputs, not a claim that every GPU/driver produces identical results or that the complete `placebo.Shader` rendering pipeline has identical output.

The former Python implementation, arithmetic-emulation path and `reference` argument have been removed. Version 0.4.0 also removes `debug` and all diagnostic output branches; calls passing either removed argument fail.

## Performance

The following measurements are historical **v0.2.0** results; v0.3.0's resize conversions have not been rebenchmarked. The native algorithm and original `placebo.Shader` pipeline were measured in three serial, fresh-process runs on the same 1080p YUV420P16 workload. Timings include completed CPU frames and required transfers; decoding, preloading and shader compilation are excluded.

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
