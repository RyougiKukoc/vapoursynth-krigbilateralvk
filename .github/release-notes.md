Windows and Linux x86-64 native packages, with SHA-256 checksums, licenses and autoload manifests. Linux binaries are built with a glibc 2.28 baseline and static C++/GCC runtimes. Requires VapourSynth R81 / API 4.3 and a Vulkan 1.4 GPU/driver.

The recommended installation method is the central [api4-wheels package](https://github.com/AliceTeaParty/vapoursynth-api4-wheels/tree/main/plugins/vapoursynth-krigbilateralvk):

```console
python -m pip install --extra-index-url https://aliceteaparty.github.io/vapoursynth-api4-wheels/simple/ vapoursynth-krigbilateralvk
```

This patch adds dual-platform release packaging and updates installation and validation documentation. The native KrigBilateral algorithm, shaders and public filter arguments are unchanged from v0.2.1. The prior central packages passed Windows RTX 3090 Ti and Linux NVIDIA A100 rendering, including signed float chroma, exact luma preservation and CPU/GPU entry equality; the original GLSL bit-exact matrix remains a Windows result.
