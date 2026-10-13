KrigBilateral delegates its input/output range and depth conversion to VapourSynth `resize.Point`. Full/limited range is read per frame; processing and intermediate buffers use canonical FP32. Integer 8..16-bit, FP16 and FP32 input are supported. Finite chroma headroom is retained until the core's final output conversion.

Version 0.3.1 routes ordinary conversions for both public entries through the same GPU resize backend. This fixes an entry-dependent last-bit rounding difference observed with Mesa when CPU resize was used for CPU-frame callers. Error diffusion still explicitly uses CPU resize; transfers produce the requested CPU/GPU output residency.

Both `KrigBilateral` and `KrigBilateralGPU` accept `bits`, `sample_type` and `dither_type`. Output type/depth defaults to input; dithering defaults to `none` (Python `None` is accepted). Available modes are `none`, `ordered`, `random` and `error_diffusion`. At unchanged type/depth the original Y plane is preserved. R81's GPU resize lacks error diffusion, so explicitly selecting it for integer GPU output adds CPU conversion and transfers; the returned node remains GPU-resident.

Untagged YUV uses resize's limited-range default. Tag full-range source with `_Range=1`; `_Range=0` denotes limited. The plugin cannot infer range from untagged pixels. The covariance equations and solver are unchanged, but integer results may differ from v0.2.x's storage-range normalization. See the README for 16-bit integer versus FP16 output selection.

Recommended installation through [api4-wheels](https://github.com/AliceTeaParty/vapoursynth-api4-wheels/tree/main/plugins/vapoursynth-krigbilateralvk):

```console
python -m pip install --extra-index-url https://aliceteaparty.github.io/vapoursynth-api4-wheels/simple/ "vapoursynth-krigbilateralvk==0.3.1"
```

Windows/Linux x86-64 native ZIPs include licenses, manifests and SHA-256 checksums. Requires VapourSynth R81 / API 4.3 and Vulkan 1.4; Linux release binaries require glibc 2.28+.
