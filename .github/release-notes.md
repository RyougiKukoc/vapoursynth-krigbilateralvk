Version 0.4.0 fuses integer range normalization and output quantization into the existing three KrigBilateral kernels. It removes boundary resize nodes and full-frame FP32 conversion buffers while preserving canonical float YUV and the original reconstruction equations. `_Range` is resolved per frame (0=limited, 1=full); legacy `_ColorRange` setters work through the core alias, and untagged YUV defaults to limited.

Integer input now defaults to integer16 output. Explicit integer8-15 output emits one VapourSynth warning that no dither is applied and banding may occur. `dither_type` accepts only `None`/`"none"`; apply core resize after 16-bit or FP32 output when dithering is needed. Float defaults retain input depth. Y is shared at unchanged type/depth and otherwise converted by the final kernel. CPU/GPU entries use identical arithmetic with the requested output residency.

`debug` and all diagnostic output branches are removed; calls passing `debug`, even at zero, fail. Windows RTX 3090 Ti and Linux NVIDIA A100 each pass 730 range/depth checks, 41 contracts and the 32-configuration original-GLSL matrix. Eight real 1080p frames match across 99,532,800 integer16/FP32 output samples. Matched 1080p measurements improve CPU16 from 112.67 to 189.29 fps and resident GPU16 from 122.29 to 220.73 fps; timing boundaries differ between CPU and resident paths. The reports identify these measured source binaries separately from release artifacts.

Recommended installation through [api4-wheels](https://github.com/AliceTeaParty/vapoursynth-api4-wheels/tree/main/plugins/vapoursynth-krigbilateralvk):

```console
python -m pip install --extra-index-url https://aliceteaparty.github.io/vapoursynth-api4-wheels/simple/ vapoursynth-krigbilateralvk
```

Windows/Linux x86-64 native ZIPs include licenses, manifests and SHA-256 checksums. Requires VapourSynth R81 / API 4.3 and Vulkan 1.4; Linux release binaries require glibc 2.28+.

The final source also passes all 730 depth/range checks, 41 contracts and the original-GLSL matrix on Mesa software Vulkan. Integer chroma adaptation has a precision boundary to prevent contraction into the normalization affine; float transport and algorithm equations remain unchanged.
