# Original-shader algorithm and platform validation

The implementation follows the original KrigBilateral GLSL algorithm directly. The Python implementation and the `reference` argument were removed in v0.2.0. Version 0.2.1 added explicit input/output contracts and standard VapourSynth signed-float chroma transport; v0.3.0 replaces integer storage-range normalization and shader-side quantization with the core's range-aware resize conversions.

## v0.3.0 range, depth and dithering

The range/depth/dither suite passes 420 cases on both Windows RTX 3090 Ti ([report](validation/v0.3.0/range-depth-dither.json)) and Linux NVIDIA A100 ([report](validation/v0.3.0/linux/range-depth-dither.json)), using VapourSynth R81. Linux was compiled natively in an isolated mamba environment and tested directly over SSH. The suite checks limited/full code-value endpoints independently of the plugin (limited 8-bit Y=16/235 maps to 0/1 and neutral U/V=128 maps to zero), per-frame range changes and random requests, untagged YUV's limited default, the legacy property alias, every integer output depth 8..16, FP16/FP32 output, type inference and defaults, float headroom, and invalid options/range metadata.

For fractional gradients, every output dither mode matches a separate core resize of the FP32 reconstruction, and ordered/random/error-diffusion modes actually change samples relative to none. A further 60 CPU/GPU entry configurations compare all planes byte-for-byte across three nonuniform frames, with both ranges, five input formats, selected output depths and all four dither modes. At unchanged type/depth the original luma bytes are retained; changed-depth luma matches a direct resize of the original input. Floating output ignores dithering, as core resize specifies. Integer GPU error diffusion uses an explicit CPU conversion and transfers back to GPU residency.

The updated [original GLSL matrix](validation/v0.3.0/format-matrix.json) remains exact in 32 configurations / 256 frames using common range-aware transport. The [stage comparison](validation/v0.3.0/stages.json) matches all stages and final output; the separate [input/output contract suite](validation/v0.3.0/input-output-contract.json) passes 42 cases. The original algorithm bodies are unchanged. These results do not assert pixel identity with v0.2.x's different range normalization or with the complete libplacebo rendering pipeline. Historical performance below does not measure the added resize work.

The same [32-configuration / 256-frame GLSL matrix](validation/v0.3.0/linux/format-matrix.json), [stage comparison](validation/v0.3.0/linux/stages.json) and [42 contracts](validation/v0.3.0/linux/input-output-contract.json) also passed on Linux A100. Each platform compares production output with an independently generated original-GLSL oracle on that device; these are not Windows-versus-Linux sample comparisons or a cross-driver bit-exact guarantee.

Untagged data cannot reveal its intended range: callers must tag full-range source correctly. Float samples use canonical signed-chroma coordinates independently of the retained range tag, which controls integer output conversion. The kernel does not clip finite headroom before the core's final format conversion.

Version 0.2.2 adds Windows/Linux release packaging and installation documentation. It does not change the native algorithm or shaders; the numerical reports below remain evidence for v0.2.1.

## Windows and Linux runtime validation

The central v0.2.1+alice.1 packages passed actual GPU rendering on Windows (RTX 3090 Ti, driver 617.42, Vulkan 1.4.351) and Linux (Ubuntu 22.04, glibc 2.35, NVIDIA A100-PCIE-40GB, driver 580.95.05, Vulkan 1.4.312). Linux used an isolated mamba environment with Python 3.13, VapourSynth R81 and a Vulkan 1.4 loader, without changing the system driver.

The published Linux wheel, its release-payload source-install loopback and a native source rebuild each passed integer reconstruction, signed FP16/FP32 chroma, nonuniform chroma, exact luma preservation and CPU-frame/GPU-resident entry equality. The published Windows wheel passed the same rendering checks. Hosted Linux CI also renders with software Vulkan; physical NVIDIA testing was performed separately over SSH.

These platform checks validate installation, transport and rendering. They do not extend the Windows original-GLSL bit-exact matrix below to Linux, and they are not cross-platform performance measurements. Machine addresses, account paths and device identifiers are excluded from public documentation.

## Historical v0.2.1 input/output validation

The additional [contract tests](validation/v0.2.1/input-output-contract.json) passed 42 cases: incompatible and variable formats/resolutions, overflowing option values, non-left chroma/interlaced/malformed metadata, every integer depth from 8 to 16, float chroma extremes/neutral values, CPU/GPU residency adapters, normal output properties and diagnostic tags. Output `_ChromaLocation` is removed; colorimetry/range/timestamps are preserved for normal output. Diagnostic buffers clear color/range tags and identify their stage.

FP16/FP32 now accept standard signed U/V. Adding 0.5 before the shader and subtracting it at output adapts sample representation; it does not modify the KrigBilateral covariance or solve equations. The oracle uses the same transport with the original GLSL hook bodies.

Node validation rejects invalid declarations before constructing GPU kernels. Frame validation checks actual format, dimensions, signed-index-safe strides and device-bounded storage ranges before dispatch. Present `_ChromaLocation`/`_FieldBased` tags must be integer zero. Finite samples remain a caller precondition; the plugin does not perform a separate per-pixel NaN/Inf/range scan.

## What is compared

The test reference executes the original GLSL hook bodies, including weighted moments, covariance expressions, packed matrix indexing, elimination and the original back-substitution loop. It is built by [tests/shader_reference.py](../tests/shader_reference.py); the production generator does not supply its algorithm code.

Both programs use common clamped texel inputs and sample coordinates, FP32 guide storage, and the same output type conversion. Float frame U/V use signed VapourSynth chroma and are converted at this transport boundary. This excludes renderer-specific texture interpolation, intermediate framebuffer format selection, color decode/encode and dithering. Such renderer choices are not prescribed by the KrigBilateral algorithm source.

The original 0.1.0 shader path still included half-precision guide/output truncation introduced for NumPy matching. In an eight-frame 32?24 algorithm comparison it differed in 9,910 output chroma samples, with a maximum 16-bit difference of 316. Removing that quantization aligned the guides and covariances. Scalarizing the solver still changed a few final float rounding results; retaining the original packed matrix and back-substitution loop eliminated those differences.

## Historical v0.2.1 bit-level results

Comparison uses raw sample bytes, including FP16/FP32 sign bits, and records hashes of both outputs. It does not substitute approximate `allclose` checks for exact equality.

- **32 format/size configurations, 256 frames:** 8/9/10/12/14/16-bit, FP16 and FP32; each at 2?2, 10?6, 34?26 and 128?72, with eight constant/ramp/edge/noise/near-constant/sine/checkerboard/noisy-gradient patterns. Every output sample matched exactly.
- **128?72 stage comparison:** vertical guide, horizontal guide, local variance, representative covariances, neighbor inputs, intrinsic probes, unquantized FP32 reconstruction and final output all matched bit-for-bit.
- **Three 1080p video frames, indices 0, 71, 143:** every compared algorithm stage matched exactly. Final output and unquantized FP32 U/V each covered 12,441,600 samples with zero differing bits.
- Luma preservation, properties, dimensions/frame rate, CPU/GPU entry equivalence, two-instance concurrent requests and invalid/removed argument rejection passed.

Raw reports: [format matrix](validation/v0.2.1/format-matrix.json), [synthetic stages](validation/v0.2.0/stages-128x72.json), [real-video stages](validation/v0.2.1/real-stages-1080p.json). The original shader SHA256 and both native library hashes are recorded in each report; [evidence-manifest.json](validation/evidence-manifest.json) hashes the published records.

This is observed numerical equivalence to the original GLSL algorithm on the tested GPU and inputs. It is not a guarantee that different GPUs/drivers compile GLSL floating-point operations identically, and it does not claim identical whole-pipeline `placebo.Shader` output.

## Performance

The following throughput measurements are from v0.2.0. Version 0.2.1 adds input/frame validation and signed-float transport; it has not been rebenchmarked here.

Windows 10 / Ryzen 9 5950X / 64GB / RTX3090Ti / driver617.42 / VapourSynth R81 / Python3.14.7. Inputs were eight preloaded 1080p YUV420P16 frames at indices 0,18,36,54,72,90,108,126, repeated under new output frame numbers. Three fresh-process runs were serialized in rotated order, with 2,048 timed frames after 16 warmup frames and four core threads/prefetch.

Timings include completed CPU output frames and required uploads/downloads, but exclude decoding, preloading and shader compilation. Placebo performs its complete rendering pipeline; the plugin performs the KrigBilateral algorithm.

| Backend | Median fps | Minimum | Maximum |
|---|---:|---:|---:|
| Vulkan v0.2.0, original shader algorithm | 196.48 | 196.44 | 196.64 |
| Original placebo.Shader pipeline | 103.63 | 102.42 | 105.22 |

The native algorithm was approximately 1.90? faster on this workload. This is a throughput comparison of those execution paths, not an assertion that renderer output pixels are identical. Logs, telemetry and per-run results are in [sustained](validation/v0.2.0/sustained).

## Execution and scope

All three production compute modules passed `spirv-val --target-env vulkan1.4`. The core execution pool manages frame producer waits, retained buffers and output publication; pass barriers order intermediate writes before reads. No Khronos validation layer was installed for these tests, so layer-based race/synchronization checks are not claimed. Other GPU vendors/drivers and exhaustive inputs remain untested.

Input media is not distributed. Its hash was `9bd42cbecbd611156f6633757d83a2c83e53c18adc7b0a3a452588bd947e798c`. Published records retain workload/results/binary hashes but remove machine-local paths and device identifiers.

The previous NumPy-targeted measurements are preserved in the [historical v0.1.0 report](validation-v0.1.0.md); they do not describe the current implementation.
