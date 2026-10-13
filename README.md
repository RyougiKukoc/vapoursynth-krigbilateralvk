# KrigBilateral Vulkan for VapourSynth

A native Vulkan implementation of **KrigBilateral**, the luma-guided chroma upscaler by Shiandow. It reconstructs YUV 4:2:0 chroma at full luma resolution and returns YUV 4:4:4 at the original size. Integer input defaults to 16-bit integer output; float input retains its float depth. Both can be selected explicitly. At unchanged type/depth, the original luma plane is preserved exactly.

The plugin runs three compute passes on VapourSynth's Vulkan device. It provides both a convenient CPU-frame interface and a GPU-resident interface for processing chains. NumPy is used only by optional comparison tests; neither NumPy nor libplacebo is a runtime dependency.

## Recommended installation: api4-wheels

Install the Windows or Linux x86-64 wheel from [api4-wheels](https://github.com/AliceTeaParty/vapoursynth-api4-wheels) into the Python environment used by VapourSynth:

```console
python -m pip install --extra-index-url https://aliceteaparty.github.io/vapoursynth-api4-wheels/simple/ vapoursynth-krigbilateralvk
```

This is the recommended installation method. It installs the native plugin, autoload manifest and licenses, and selects the matching VapourSynth R81 dependency. No compiler or manual DLL/SO copying is needed. Use Python 3.12 or newer; Linux wheels require glibc 2.28 or newer. A Vulkan 1.4 GPU and driver are still required for rendering.

The native source is maintained here and included in api4-wheels as a git submodule. The central repository owns pip packaging, Windows/Linux wheel publication and the package index. After installation, `vs.core.krigvk` is available automatically; see [usage](#usage). Version 0.4.0 introduces fused conversion and defaults integer input to integer16 output; earlier packages retain their documented conversion behavior.

## Requirements

- **VapourSynth R81 / API 4.3**, with `std.GPUUpload`, `std.GPUDownload` and the core Vulkan API.
- A GPU and driver supporting **Vulkan 1.4** and VapourSynth's required device features.
- Constant-format YUV 4:2:0 input with even width and height.
- Windows or Linux x86-64 for prebuilt packages. GPU runtime validation has passed on Windows with an RTX 3090 Ti and Linux with an NVIDIA A100. Other platforms require a source build and remain unvalidated.

The initial binary targets the **R81 release headers**. The GPU interface evolves with VapourSynth: when changing core versions, rebuild against matching headers rather than assuming GPU ABI compatibility.

## Manual installation

For manual installation, download `krigbilateralvk-0.4.1-windows-x86_64.zip` or `krigbilateralvk-0.4.1-linux-x86_64.zip` from [Releases](https://github.com/RyougiKukoc/vapoursynth-krigbilateralvk/releases). Extract it and load the native library explicitly:

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
core.krigvk.KrigBilateral(clip, bits=None, sample_type=None)
core.krigvk.KrigBilateralGPU(clip, bits=None, sample_type=None)
```

| Argument | Default | Meaning |
|---|---|---|
| `clip` | required | YUV420 input. `KrigBilateral` accepts CPU frames; `KrigBilateralGPU` accepts GPU-resident frames. |
| `bits` | 16 for integer input; input depth for float | Output integer depth `8..16`, or float depth `16/32`. `32` selects float; depths below `16` select integer. At `16`, the input sample type is retained unless `sample_type` is supplied. |
| `sample_type` | inferred as above | `vs.INTEGER` or `vs.FLOAT`. Use this to distinguish integer 16-bit from FP16. |

Output is YUV444 with the input's dimensions, frame count and frame rate. At unchanged type/depth, luma is shared unchanged. When type/depth changes, the last Krig kernel converts luma directly from the original samples. Range, colorimetry, timestamps and custom properties are retained; `_ChromaLocation` is removed because chroma is no longer subsampled.

For example:

```python
# Keep reconstructed chroma in FP32 instead of quantizing it to the input depth.
floating = core.krigvk.KrigBilateral(source, bits=32)

# Select integer output explicitly when the input is floating point.
integer = core.krigvk.KrigBilateral(floating_source, bits=16, sample_type=vs.INTEGER)

# Apply error diffusion explicitly after 16-bit reconstruction.
sixteen_bit = core.krigvk.KrigBilateral(source)
eight_bit = core.resize.Point(sixteen_bit, format=vs.YUV444P8, dither_type="error_diffusion")

# Explicit low-depth output without dither emits a banding warning.
eight_bit_no_dither = core.krigvk.KrigBilateral(source, bits=8)
```

Both entries use the same fused range/depth arithmetic and quantizer. Only the CPU-frame entry adds upload/download nodes. Selecting integer output below 16 bits emits one VapourSynth warning at node creation: no dithering is performed and low depth may cause banding. Processing continues. Default integer16 and float outputs do not emit that warning.

### Sample formats and chroma placement

Input supports every integer depth from 8 to 16, FP16 and FP32. Integer loads are normalized to canonical FP32 inside the existing Krig kernels, and output is quantized in the last kernel. There are no boundary resize nodes or full-frame FP32 conversion intermediates. The covariance and solver remain FP32.

Each frame's `_Range` is read and retained: **0=limited, 1=full**. The deprecated `_ColorRange` has inverse numbering and is linked by the core's C API. Untagged YUV defaults to limited; tag full-range input with `core.std.SetFrameProps(source, _Range=1)`. For b-bit limited input, s=2^(b-8): Y uses `(Y-16s)/(219s)`, U/V use `(C-128s)/(224s)`. Full range uses the storage peak 2^b-1 and chroma center 2^(b-1). See the [complete conversion contract and 8-16-bit range table](docs/range-depth.md).

The filter uses fixed **left chroma placement** and progressive sampling geometry. An absent `_ChromaLocation` or `_FieldBased` is assumed to mean left or progressive respectively; if present, each must be a single integer equal to `0`. Other placement/field tags fail when the affected frame is requested. Resite chroma or deinterlace/separate fields before filtering if required. The filter performs only 4:2:0 → 4:4:4 reconstruction, not arbitrary resizing.

FP16 and FP32 use the **standard VapourSynth float YUV convention**: nominal Y in `[0, 1]`, U/V in `[-0.5, 0.5]`. Computation and intermediate buffers are FP32 for every input type. The transport only adds/subtracts 0.5 to adapt signed chroma to the original GLSL coordinates. Finite headroom/footroom is retained through reconstruction. Final integer conversion rounds to nearest, ties to even, and clips to the storage range without dithering. FP16 storage requires the core's device support.

Node creation rejects variable format/resolution, non-YUV420 input, nonpositive/odd dimensions, unsupported storage/output types and invalid options, including overflowing integers. Original frame metadata is checked before any shader dispatch. Frame evaluation checks the actual format, plane dimensions, strides and Vulkan buffer sizes before dispatching. Finite samples are required; the filter does not scan every input pixel to reject NaN/Inf.

## Algorithm fidelity

Version 0.2.0 uses a single implementation derived directly from Shiandow's original GLSL. It retains the weighted moments, covariance equations, packed matrix, fixed elimination and back-substitution order. Guide buffers store FP32 values; the plugin does not introduce half-precision truncation or emulate NumPy arithmetic.

The comparison harness executes the original shader's hook bodies without rewriting the covariance or solver. The candidate uses fused conversion; the test oracle uses independently frozen core resize transport. Both use common texel coordinates and FP32 intermediate storage. This isolates the **KrigBilateral algorithm** from renderer-specific texture interpolation and color conversions. The fused conversion retains the range semantics introduced in version 0.3.0 and leaves the covariance and solver unchanged: its integer results need not match v0.2.x, which used storage-range normalization.

On the tested RTX 3090 Ti, all 32 format/size configurations (256 synthetic frames) were bit-exact, including integer, FP16 and FP32 output. The comparison also checks FP32 reconstruction without integer quantization. Historical intermediate-stage and 1080p comparisons remain recorded for their measured versions. See [the validation report](docs/validation.md) for scope and evidence.

This is measured parity with the original GLSL algorithm under common inputs, not a claim that every GPU/driver produces identical results or that the complete `placebo.Shader` rendering pipeline has identical output.

The former Python implementation, arithmetic-emulation path and `reference` argument have been removed. Version 0.4.0 removes `debug` and all diagnostic output branches. Version 0.4.1 removes `dither_type` from both entries; callers use core resize for dithering. Calls passing removed arguments fail.

## Performance

The final v0.4.0 source and the saved pre-fusion resize build were measured together on RTX 3090 Ti / VapourSynth R81 after the user reported exiting the game; the ordinary desktop remained active. Each configuration uses the same eight preloaded 1080p YUV420P16 frames with `_Range=0`, three serial fresh-process runs of 2,048 timed frames after 16 warmup frames, and four threads/prefetch. CPU timings include full output transfers. Resident timings wait for each GPU output to complete without downloading its pixels. Decoding, preloading and shader compilation are excluded.

| Backend | Median fps | Three-run range |
|---|---:|---:|
| Fused v0.4.0 CPU-frame entry, 16-bit output | 189.29 | 183.57-189.52 |
| Pre-fusion resize CPU-frame entry, 16-bit output | 112.67 | 106.56-114.19 |
| Fused v0.4.0 GPU-resident entry, 16-bit output | 220.73 | 219.78-228.94 |
| Pre-fusion resize GPU-resident entry, 16-bit output | 122.29 | 121.58-128.34 |
| Fused v0.4.0 GPU-resident entry, FP32 output | 190.10 | 187.19-191.66 |
| Original placebo.Shader pipeline | 116.21 | 114.66-122.53 |

The matched 16-bit comparisons improve throughput by **1.68× for CPU output** and **1.80× for resident GPU output**, with equal sampled output hashes. Windows and Linux numerical suites independently confirm the fused conversion against original GLSL plus core resize. The extra resize passes, allocations and intermediate traffic explain the measured conversion cost; no driver instruction analysis is claimed. FP32 output writes converted Y as well and has a different output bandwidth cost. The placebo row measures the complete rendering pipeline and does not establish whole-renderer pixel equality. CPU and resident rows also have different transfer boundaries. See [the measured-source validation and raw repetitions](docs/validation.md#final-source-performance). Earlier game-contaminated observations remain historical only; reports identify the measured binaries separately from hosted release artifacts.

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
