Version 0.4.2 maps each 64-invocation compute workgroup to 32x2 pixels, improving contiguous row access without changing KrigBilateral equations, FP32 arithmetic or conversion semantics. Host dispatch sizes and device-limit checks match the new layout.

On RTX 3090 Ti / VapourSynth R81, three rotated 2,048-frame A/B runs measured 197.13 -> 366.32 fps for complete CPU output (1.86x), and 243.81 -> 530.38 fps for GPU-resident completion (2.18x). The ordinary desktop remained active. These are device/workload-specific results. Original-GLSL numerical checks, range/depth contracts, real video and Vulkan synchronization validation accompany the change; see docs/r2-optimization.md for exact scope and raw evidence.

Install the Windows/Linux x86-64 wheels through api4-wheels:

```console
python -m pip install --upgrade --extra-index-url https://aliceteaparty.github.io/vapoursynth-api4-wheels/simple/ "vapoursynth-krigbilateralvk==0.4.2"
```

Requires Python 3.12+, VapourSynth R81 or newer and Vulkan 1.4. Linux wheels require glibc 2.28+. The README now begins with the recommended api4-wheels installation.
