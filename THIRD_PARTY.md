# Sources and third-party notices

| Component | Origin | License / use |
|---|---|---|
| KrigBilateral algorithm and shader | Shiandow, [supplied KrigBilateral.glsl](reference/KrigBilateral.glsl) | LGPL-3.0-or-later. The supplied shader is preserved in `reference/`; generated compute kernels adapt its reconstruction and fixed-order solver. |
| Python reference | The supplied KrigBilateral CPU translation in `reference/KrigBilateral.py` | Preserved as a numerical reference for the LGPL adaptation. It is not invoked by the native plugin. |
| NumPy expf approximation | [NumPy v2.4.0](https://github.com/numpy/numpy/tree/v2.4.0/numpy/_core/src/umath), `loops_exponent_log.dispatch.c.src` and `npy_simd_data.h` | BSD-3-Clause; see `licenses/NUMPY.txt`. The shader generator adapts range reduction, rational coefficients and explicit FMA evaluation. |
| VapourSynth headers | [R81, dd11a9da6f8e2bb24ab4bb084d44bf01fb93612a](https://github.com/vapoursynth/vapoursynth/tree/dd11a9da6f8e2bb24ab4bb084d44bf01fb93612a/include) | LGPL-2.1-or-later; fetched for compilation, not vendored in this repository. |
| Vulkan-Headers | [SDK 1.4.357.0, e3b1eec08173d6b825cd3ac88c885a63b621504a](https://github.com/KhronosGroup/Vulkan-Headers/tree/e3b1eec08173d6b825cd3ac88c885a63b621504a) | Apache-2.0 / MIT as identified by the upstream file notices; fetched for compilation. |

The plugin resolves Vulkan calls through VapourSynth and does not redistribute a Vulkan loader, NumPy binary or libplacebo binary. The original shader's attribution is retained in the generated reconstruction source. Both [LGPLv3](licenses/LGPL.txt) and its referenced [GPLv3](licenses/GPL.txt) are included.

Historical benchmark JSON under `docs/validation/` contains sanitized measurement records. Paths and device UUIDs were removed; numerical results, workload settings and binary hashes were retained. The test video is not distributed.
