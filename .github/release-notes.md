Version 0.4.1 removes `dither_type` from both public entries. Their signatures are now `KrigBilateral(clip, bits=None, sample_type=None)` and `KrigBilateralGPU(clip, bits=None, sample_type=None)`. Passing the removed argument fails, including `dither_type="none"`.

Fused conversion, integer16 defaults, range handling, low-depth banding warnings and the shaders are unchanged from 0.4.0. No dithering is performed by KrigBilateral; request integer16 or FP32 output and use core resize when dithering is required.

Recommended installation through [api4-wheels](https://github.com/AliceTeaParty/vapoursynth-api4-wheels/tree/main/plugins/vapoursynth-krigbilateralvk):

```console
python -m pip install --upgrade --extra-index-url https://aliceteaparty.github.io/vapoursynth-api4-wheels/simple/ "vapoursynth-krigbilateralvk==0.4.1"
```

Requires Python 3.12+, VapourSynth R81 or newer and Vulkan 1.4. Packages support Windows/Linux x86-64; Linux has a glibc 2.28 floor. Earlier numerical/performance reports remain tied to their measured versions.
