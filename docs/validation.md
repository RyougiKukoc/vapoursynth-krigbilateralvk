# Validation and performance

The records in this directory describe the initial Windows implementation measured on an RTX 3090 Ti. They distinguish comparison with the included Python/NumPy reference from comparison with the original `placebo.Shader` pipeline. Source packaging and later compiler builds may produce a different DLL hash; each measurement records the binary it used.

## Numerical validation

Full-plane comparisons include border pixels and preserve luma exactly. The synthetic matrix covers constant color, ramps, sharp edges, random noise, near-constant values, sine patterns and checkerboards.

| Test | Result against the Python reference |
|---|---|
| 8/9/10/12/14/16-bit; 2?2, 10?6 and 34?26; five patterns per configuration | All 18 integer configurations matched exactly. |
| FP16, same matrix | One differing sample; maximum normalized difference 0.00000762939453125. |
| FP32, same matrix | Maximum normalized difference 0.0000008344650268554688. |
| 128?72 YUV420P16, eight patterns | One differing chroma sample; maximum integer difference 16. |
| Three distinct 1920?1080 YUV420P16 frames | 12 differing chroma samples out of 12,441,600; maximum integer difference 32, equivalent to 0.124513619 on an 8-bit scale. |

For the three real frames, the maximum per-frame/plane RMS difference against Python was 0.000155882 on an 8-bit scale. The sampled frame indices were 0, 71 and 143 from a 144-frame input. Its SHA256 is `9bd42cbecbd611156f6633757d83a2c83e53c18adc7b0a3a452588bd947e798c`. This input is not distributed; the scripts accept a user-provided raw video.

The CPU-input and GPU-resident interfaces produced identical outputs. Frame dimensions, frame rate and properties were preserved. Concurrent, out-of-order requests across two instances passed, and invalid formats/parameters were rejected.

These are finite test results, not a bit-exact guarantee for arbitrary inputs, GPU vendors, drivers or NumPy dispatch paths. Remaining differences are consistent with floating-point solver rounding and quantization boundaries; the tests do not establish the cause of every differing sample.

See [the format matrix](validation/format-matrix.json), [synthetic cases](validation/final-128x72.json) and [real-frame comparisons](validation/final-binary-real-1080p.json).

### Original shader comparison

The Python reference is itself not bit-exact to the original renderer. Against `placebo.Shader`, the default Vulkan mode had 2,334,919 differing chroma samples in the three real frames, maximum integer difference 1,792 (6.972763 on an 8-bit scale), and maximum per-frame/plane RMS difference 0.115357 on an 8-bit scale.

The plugin implements the reconstruction with buffer-based compute kernels. It does not replicate libplacebo's texture sampling and full color decode/encode pipeline. The faster `reference="shader"` mode keeps the original arithmetic style, but should not be presented as a renderer-equivalent output path.

## Performance

Environment: Windows 10 19045, Ryzen 9 5950X, 64 GB RAM, RTX 3090 Ti, NVIDIA driver 617.42, VapourSynth R81 / API 4.3, Python 3.14.7 and NumPy 2.5.3. The original build used GCC 16.2, C++20, `-O3 -ffp-contract=off` and statically linked the C++ runtime.

The 1080p YUV420P16 workload preloaded eight video frames at indices 0, 18, 36, 54, 72, 90, 108 and 126, then repeated them under new output frame numbers. The timer counted completed CPU output frames, including required transfers. Decoding, preloading and shader compilation were excluded.

Each backend ran in three fresh processes, sequentially, with rotated order and four core threads/prefetch. GPU runs used 1,024 timed frames after 16 warmup frames; Python used four timed frames after one warmup frame. GPU runs lasted about 5?11 seconds and Python about 38 seconds. The Python figure describes the included NumPy implementation under this budget, not an optimized CPU ceiling.

| Backend | Median fps | Minimum | Maximum |
|---|---:|---:|---:|
| Vulkan, default Python arithmetic | 148.6126 | 148.4882 | 149.0257 |
| Vulkan, original shader arithmetic | 191.5038 | 191.0593 | 191.6328 |
| Original placebo.Shader | 94.1708 | 93.3737 | 101.1410 |
| Python/NumPy | 0.104129 | 0.103281 | 0.104183 |

The default path was about 1.58? as fast as the original placebo pipeline on this workload. Arithmetic paths have different numerical behavior, so their speed comparison is not a proof of equal-quality output.

The [summary](validation/sustained/summary.json), per-run JSON, logs and GPU telemetry are under [validation/sustained](validation/sustained). Published JSON records have local paths and device identifiers removed; workload, results and binary hashes are retained. [evidence-manifest.json](validation/evidence-manifest.json) hashes the published files.

## Execution checks and limits

Three actual compute modules passed `spirv-val --target-env vulkan1.4`, including the FloatControls2 transforms in the default mode. Input producer waits, scratch lifetime and output producer publication use the core execution pool. Compute barriers order intermediate writes before reads.

The local machine had no Khronos validation layer installed. Module validation and successful concurrent requests are not a substitute for layer-based synchronization/race checks. AMD/Intel/other drivers, GPU device-loss injection and an exhaustive numerical input domain remain untested. Linux CI proves compilation, not Linux GPU runtime behavior.
