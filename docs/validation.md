# Original-shader algorithm validation ? v0.2.0

The implementation now follows the original KrigBilateral GLSL algorithm directly. The Python implementation and the `reference` argument have been removed.

## What is compared

The test reference executes the original GLSL hook bodies, including weighted moments, covariance expressions, packed matrix indexing, elimination and the original back-substitution loop. It is built by [tests/shader_reference.py](../tests/shader_reference.py); the production generator does not supply its algorithm code.

Both programs use common clamped texel inputs and sample coordinates, FP32 guide storage, and the same output type conversion. This excludes renderer-specific texture interpolation, intermediate framebuffer format selection, color decode/encode and dithering. Such renderer choices are not prescribed by the KrigBilateral algorithm source.

The original 0.1.0 shader path still included half-precision guide/output truncation introduced for NumPy matching. In an eight-frame 32?24 algorithm comparison it differed in 9,910 output chroma samples, with a maximum 16-bit difference of 316. Removing that quantization aligned the guides and covariances. Scalarizing the solver still changed a few final float rounding results; retaining the original packed matrix and back-substitution loop eliminated those differences.

## Bit-level results

Comparison uses raw sample bytes, including FP16/FP32 sign bits, and records hashes of both outputs. It does not substitute approximate `allclose` checks for exact equality.

- **32 format/size configurations, 256 frames:** 8/9/10/12/14/16-bit, FP16 and FP32; each at 2?2, 10?6, 34?26 and 128?72, with eight constant/ramp/edge/noise/near-constant/sine/checkerboard/noisy-gradient patterns. Every output sample matched exactly.
- **128?72 stage comparison:** vertical guide, horizontal guide, local variance, representative covariances, neighbor inputs, intrinsic probes, unquantized FP32 reconstruction and final output all matched bit-for-bit.
- **Three 1080p video frames, indices 0, 71, 143:** every compared algorithm stage matched exactly. Final output and unquantized FP32 U/V each covered 12,441,600 samples with zero differing bits.
- Luma preservation, properties, dimensions/frame rate, CPU/GPU entry equivalence, two-instance concurrent requests and invalid/removed argument rejection passed.

Raw reports: [format matrix](validation/v0.2.0/format-matrix.json), [synthetic stages](validation/v0.2.0/stages-128x72.json), [real-video stages](validation/v0.2.0/real-stages-1080p.json). The original shader SHA256 and both native library hashes are recorded in each report; [evidence-manifest.json](validation/evidence-manifest.json) hashes the published records.

This is observed numerical equivalence to the original GLSL algorithm on the tested GPU and inputs. It is not a guarantee that different GPUs/drivers compile GLSL floating-point operations identically, and it does not claim identical whole-pipeline `placebo.Shader` output.

## Performance

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
