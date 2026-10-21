# Workgroup layout follow-up after BM3DVK2 r2

BM3DVK2 r2 demonstrates that data placement and workgroup layout need to match
the compiler and access pattern. KrigBilateral does not have its block search
or temporal ring. Here the retained change maps the existing 64 invocations
from 8×8 to 32×2 pixels, so consecutive lanes access consecutive row samples.
All three dispatches retain their equations, FP32 arithmetic, range conversion,
storage formats and invocation count. The host dispatch dimensions match the
new layout; out-of-frame invocations still return before touching buffers.

An earlier explicit array-unrolling experiment was rejected: strict original-GLSL
comparison found one integer sample differing by one code at 34×26. It is not
part of this version. Layout-only numerical results are recorded separately.

## Matched throughput

Windows 10, RTX 3090 Ti, NVIDIA 617.42, VapourSynth R81, MinGW-w64 GCC.
The ordinary desktop remained active; other GPU applications were not controlled.
Each row has three fresh-process repetitions, serial rotated A/B order,
2,048 timed frames after 16 warmup frames, four threads and prefetch four.
Both versions use the same eight preloaded real 1080p YUV420P16 frames,
limited range, integer16 output and new output frame numbers.

| Layout and output boundary | Median fps | Min–max fps |
|---|---:|---:|
| Previous 8×8, complete CPU frames | 197.13 | 196.31–197.71 |
| 32×2, complete CPU frames | 366.32 | 365.27–372.08 |
| Previous 8×8, GPU resident completion | 243.81 | 239.80–244.80 |
| 32×2, GPU resident completion | 530.38 | 526.79–540.63 |

Measured throughput ratios are 1.86× and 2.18×. CPU output includes required
uploads and downloads. Resident output uses the test-only completion sink,
without downloading output pixels. Decoding, preloading and shader compilation
are excluded. Every repetition has the same sampled output hash. Neither
ratio is a kernel-only measurement or a prediction for another GPU/driver.

The baseline DLL was rebuilt from `f9a79ec` with the same local CMake build.
The records identify both measured DLL hashes, driver, source input hashes,
commands and telemetry. Published Windows/Linux binaries have separate package
manifests; the measured binary is not assumed identical to every hosted build.

Raw records and final-source numerical evidence are under
[validation/v0.4.2](validation/v0.4.2). Use the repository comparison tools in
[development.md](development.md) to reproduce the original-GLSL, range/depth,
request-order and real-video checks. Performance is established only for the
Windows NVIDIA workload above; Linux software Vulkan checks establish behavior.

Both Windows RTX 3090 Ti and Linux Mesa llvmpipe passed the 32-configuration /
256-frame original-GLSL matrix, 732 fused range/depth cases and 45 input/output
contracts. Windows additionally passed all-pixel real-video comparisons at
integer16 and FP32 output, and the matrix under Khronos synchronization and
thread-safety validation with no VUID or synchronization hazard.

`source.json` hashes the committed source and evidence bytes. Text evidence
uses LF line endings for portable verification; binary and pixel hashes inside
the reports still identify the original measured data.
