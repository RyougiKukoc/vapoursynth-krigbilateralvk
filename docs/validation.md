# Original-shader algorithm and platform validation

## v0.4.0 fused conversion candidate

The current candidate fuses input normalization and output quantization into the original three Krig kernels, with no boundary resize nodes or full-frame FP32 conversion buffers. It reads `_Range` per frame (0=limited, 1=full), preserves finite headroom through FP32 reconstruction, and defaults every integer input depth to integer16 output. Low-depth integer output emits one banding warning and does no dither. `dither_type` accepts only `None`/`"none"`; callers can apply external resize when dithering is required. `debug` remains removed from both entries. The exact conversion policy and documentation sources are in [the range/depth contract](range-depth.md).

The independent oracle uses a frozen pre-fusion host with real core GPU resize at both boundaries and the literal original GLSL hooks. It does not reuse the candidate's normalization or quantization helpers. Tests compare all three output planes across all nine integer input depths, FP16/FP32, both ranges, every output format and both entries, plus endpoint, malformed metadata, headroom, per-frame concurrent range, default and warning behavior.

Windows RTX 3090 Ti and Linux NVIDIA A100 each pass **730 range/depth cases**, **41 contracts**, and the **32-configuration / 256-frame original-GLSL matrix**. Linux compiled natively with CMake/GCC and used a test environment with plugin autoload disabled, leaving the installed plugin unchanged. Neither platform's ordinary or FP32 comparison has a differing sample bit. The Windows real-video comparison adds eight 1920×1080 frames, indices 0,20,40,61,81,102,122,143, checking default integer16 and FP32 output across all three planes: **99,532,800 samples, zero differences**. These are per-device comparisons, not a guarantee of identical output across devices/drivers.

Evidence: [Windows range/depth](validation/v0.4.0-fused/windows/range-depth.json), [contracts](validation/v0.4.0-fused/windows/contracts.json), [matrix](validation/v0.4.0-fused/windows/matrix.json), [real video](validation/v0.4.0-fused/windows/real-1080p.json); [Linux range/depth](validation/v0.4.0-fused/linux/range-depth.json), [contracts](validation/v0.4.0-fused/linux/contracts.json), [matrix](validation/v0.4.0-fused/linux/matrix.json), [FP32](validation/v0.4.0-fused/linux/fp32.json). [Candidate source and binary hashes](validation/v0.4.0-fused/candidate-source.json) identify the the pre-commit measured source and binaries; these are not hosted release artifacts.

Earlier pre-fusion 0.4.0 candidate records remain under `validation/v0.4.0/` and describe their own recorded binaries. Their 420 depth/dither cases and performance observations do not validate the new fused conversion. Historical stage reports below also remain tied to their measured releases. The initial publication hold was lifted before v0.4.0 release preparation.

The implementation follows the original KrigBilateral GLSL directly. The Python implementation and `reference` argument were removed in v0.2.0. Version v0.3.0 established range-aware canonical float semantics; the fused candidate retains these semantics while moving conversion into the algorithm kernels.

## Fused candidate performance

The candidate and the saved pre-fusion resize build run serially in three fresh-process repetitions with rotated order. RTX 3090 Ti / VapourSynth R81; the same eight real 1080p integer16 frames, indices 0,18,36,54,72,90,108,126, tagged limited; 2,048 timed frames, 16 warmup, four threads/prefetch. The source SHA256 is 9bd42cbecbd611156f6633757d83a2c83e53c18adc7b0a3a452588bd947e798c. The user reported closing the game before these investigations; ordinary desktop activity remained. The final batch had no concurrent local validation. An exploratory batch was excluded because a short local GPU contract test may have overlapped its final CPU runs.

CPU entry and placebo timings include completed full output and transfers. Resident input is uploaded/completed before timing; the test-only sink calls `waitGPUFrame` for every output and returns a 1×1 CPU acknowledgement without pixel download. Decoding, preloading, construction/JIT and warmup are excluded. These are throughput measurements with completion, not kernel-only timestamps. The sink and oracle are not packaged.

| Configuration | Median fps | Minimum | Maximum |
|---|---:|---:|---:|
| Fused CPU16 | 180.95 | 174.15 | 182.80 |
| Resize CPU16 | 101.49 | 99.77 | 106.17 |
| Fused GPU16 | 205.13 | 204.10 | 211.47 |
| Resize GPU16 | 118.65 | 112.07 | 121.42 |
| Fused GPU FP32 | 173.66 | 172.38 | 187.91 |
| Original placebo.Shader CPU16 | 113.49 | 110.29 | 114.50 |

Fused/resize ratios are 1.7829× for CPU16 and 1.7288× for GPU16. All compared integer16 sample hashes are equal across repetitions and both entries. FP32 output writes converted Y and U/V, whereas integer16 input/output shares Y; their byte traffic differs. The placebo row describes the full renderer and is not a claim of equal whole-renderer pixels. The data supports the cost of extra conversion passes, allocation and intermediate reads/writes; it does not establish a specific compiler instruction lowering.

The [sanitized summary](validation/v0.4.0-fused/performance/summary.json) links each configuration to its binary hash, frame/output scope and repetitions. Individual `*-r0.json`, `*-r1.json`, `*-r2.json` records reside beside it; private paths/device identifiers are excluded. These measured-source records identify their binaries separately from final hosted release artifacts.

## v0.3.1 conversion parity

The Mesa software Vulkan check exposed a last-bit difference between CPU and GPU resize conversions when the public entries selected different conversion backends. Version 0.3.1 uses the same GPU resize path around reconstruction for both entries, with transfers at the CPU interface. Integer error diffusion remains an explicit CPU resize. This keeps range/depth handling in the core and makes entry selection affect residency rather than conversion arithmetic.

The [420 range/depth/dither cases](validation/v0.3.1/range-depth-dither.json) and [42 input/output contracts](validation/v0.3.1/input-output-contract.json) pass on RTX 3090 Ti. The same [420 cases](validation/v0.3.1/mesa/range-depth-dither.json) and [42 contracts](validation/v0.3.1/mesa/input-output-contract.json) also pass in the Linux Mesa software Vulkan environment that exposed the mismatch. The [32-configuration / 256-frame original-GLSL matrix](validation/v0.3.1/format-matrix.json) and [stage comparison](validation/v0.3.1/stages.json) remain exact on Windows. Output conversion expectations use independent core GPU resize for none/ordered/random and CPU resize for error diffusion, matching the explicitly selected backend.

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

## Historical pre-fusion performance

The user subsequently confirmed a game was running on the same GPU during these measurements. All results in this table are retained as shared-GPU observations and excluded from an idle-GPU release baseline. They cannot establish a regression from historical runs or isolate the cost of range conversion.

The v0.4.0 observations are from Windows 10 / Ryzen 9 5950X / RTX 3090 Ti / driver 617.42 / VapourSynth R81 / Python 3.14.7. All configurations use the same eight preloaded 1080p YUV420P16 frames, indices 0,18,36,54,72,90,108,126, with limited-range metadata. Three fresh-process repetitions run serially in rotated order; each filter run requests 2,048 new output frame numbers after 16 warmup frames, with four core threads/prefetch. The source's kept blank template avoids unused full-frame generation during timed delivery. Construction, decoding, preloading, JIT and warmup are excluded.

CPU entry and placebo wait for full CPU output, including required uploads/downloads. GPU-entry input frames are uploaded and completed before timing. A separate test-only sink calls core `waitGPUFrame` per output and returns a 1x1 CPU acknowledgement, with no output pixel readback. The latter includes completion synchronization and acknowledgement overhead; it is not a shader-only GPU timestamp. These boundaries describe practical CPU versus resident GPU usage and are not identical workloads. Error diffusion GPU output includes CPU conversion and the transfers needed to restore residency.

| Configuration | Median fps | Minimum | Maximum |
|---|---:|---:|---:|
| v0.4.0 CPU entry, 16-bit output | 102.48 | 100.12 | 105.42 |
| v0.4.0 resident GPU entry, 16-bit output | 119.46 | 111.19 | 121.92 |
| v0.4.0 resident GPU entry, FP32 output | 140.82 | 131.80 | 144.94 |
| v0.4.0 resident GPU entry, 8-bit ordered | 102.74 | 93.49 | 110.38 |
| v0.4.0 GPU entry, 8-bit error diffusion | 106.12 | 104.23 | 108.60 |
| v0.3.1 CPU entry, 16-bit output | 103.32 | 102.81 | 106.59 |
| v0.2.0 CPU entry, rerun on the same setup | 183.01 | 181.75 | 186.43 |
| Original placebo.Shader CPU output | 113.53 | 106.41 | 119.80 |
| CPU source delivery only | 7690.10 | 7259.86 | 7774.51 |
| GPU source delivery and completion only | 6375.61 | 6271.67 | 6511.90 |

Source-delivery controls use 16,384 frames and are much faster than the filters. The v0.3.1/v0.4.0 normal output sample hashes match, but concurrent game activity makes the throughput differences inconclusive. The older v0.2.0 also uses different normalization, and the resident and CPU cases have different transfer boundaries. FP32 resident output skips final integer conversion, but these measurements do not isolate that operation's cost. No speedup/regression, per-operation cost, Linux throughput or cross-device speed claim is made from this shared-GPU table.

Sanitized [summary](validation/v0.4.0/performance/summary.json) and the 30 individual reports in that directory record input/output hashes, native binary hashes, frame counts, completion boundaries and environments. Exact benchmark scripts are [benchmark.py](../tests/benchmark.py), [run_benchmarks.py](../tests/run_benchmarks.py) and the test-only [completion sink](../tests/bench_wait.cpp). The full original renderer has different pixel processing; throughput comparison does not assert matching rendered pixels.

### Historical v0.2.0 measurements

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
