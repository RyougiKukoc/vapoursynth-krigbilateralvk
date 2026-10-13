# Fused sample conversion contract

KrigBilateral reads integer samples directly in its existing compute kernels,
normalizes them to FP32 at each load, and quantizes reconstructed chroma in the
last kernel. It allocates only the two existing FP32 moment/guide buffers and
the requested output planes. No input/output resize nodes or canonical FP32
frame are inserted. Y is shared when storage matches; otherwise the last kernel
also converts Y directly from the original samples.

## Frame range

The [VapourSynth C API reference](https://www.vapoursynth.com/doc/apireference.html#reserved-frame-properties)
defines `_Range` as **0=limited, 1=full**. It was introduced in API R4.2.
The deprecated `_ColorRange` uses **1=limited, 0=full** and is linked by the core's
C API to `_Range`. The plugin reads and writes only `_Range` and validates that,
when present, it contains exactly one integer, 0 or 1. Setting `_ColorRange` on
input through `std.SetFrameProps` therefore works without a second precedence
rule. A missing range tag means limited, matching resize's YUV default.

The range is resolved **per frame**, including out-of-order and concurrent
requests; it is not inferred from bit depth or cached from frame zero. The
resolved `_Range` is written to output. Other properties are copied, except
`_ChromaLocation`, which is removed on YUV444 output. The range flag does not
change the meaning of float samples: float YUV always uses Y in [0,1] and signed
U/V in [-0.5,0.5], with finite headroom/footroom allowed.

The R81 Python wrapper's deprecated `_ColorRange` getter rewrites its name
before reading the property, so in the tested build it returns the canonical
numbering. Use `_Range` for Python inspection. This does not affect the native
plugin or the legacy setter. The test suite checks legacy setter behavior by
reading the resulting `_Range`.

## Integer numbering

For bit depth b and s=2^(b−8), limited Y has nominal codes [16s,235s], and
limited U/V [16s,240s], with neutral chroma 128s. Full range spans [0,2^b−1],
with neutral chroma 2^(b−1). The conversion is:

| Range | Y → canonical FP32 | U/V → canonical FP32 |
|---|---|---|
| limited | (Y−16s)/(219s) | (C−128s)/(224s) |
| full | Y/(2^b−1) | (C−2^(b−1))/(2^b−1) |

| b | Limited Y | Limited U/V | Neutral U/V |
|---:|---|---|---:|
| 8 | 16–235 | 16–240 | 128 |
| 9 | 32–470 | 32–480 | 256 |
| 10 | 64–940 | 64–960 | 512 |
| 11 | 128–1880 | 128–1920 | 1024 |
| 12 | 256–3760 | 256–3840 | 2048 |
| 13 | 512–7520 | 512–7680 | 4096 |
| 14 | 1024–15040 | 1024–15360 | 8192 |
| 15 | 2048–30080 | 2048–30720 | 16384 |
| 16 | 4096–60160 | 4096–61440 | 32768 |

These match the sample-numbering helpers in
[VapourSynth R81 gpuresize.cpp](https://github.com/vapoursynth/vapoursynth/blob/744afbd68570697c9c39e126fd8c41b78c37b025/src/core/gpuresize.cpp#L457),
which follow zimg's depth/quantize definitions. This is a depth-only conversion,
so `_Matrix` is preserved and does not change the 224 chroma span, matching
resize's depth-only handling of YCgCo. No color matrix conversion is performed.

The GPU uses the equivalent FP32 multiply/add affine with format constants,
like GPU resize. It does not clip input to nominal legal range. Signed chroma
is then shifted by +0.5 for the original GLSL algorithm and shifted back before
output. The original covariance and solver expressions remain unchanged.

For integer loads, the +0.5 canonical-to-shader adaptation has a GLSL `precise`
boundary so it cannot contract into the normalization affine. The affine keeps
the core resize contraction policy. Float transport stays as in the original
GLSL harness. This prevents a Mesa last-bit difference while retaining the
observed NVIDIA parity, without another pass or intermediate frame.

## Output and warnings

Integer input defaults to **integer 16-bit output** for every accepted input
depth 8–16. Float input defaults to its original float depth. Explicit `bits`
and `sample_type` select integer 8–16 or float 16/32 as documented in the README.

The last kernel applies the inverse numbering for the requested output depth,
rounds integer output to nearest with ties to even, and clips to [0,2^b−1].
It does not clip to the nominal limited interval. There is **no dithering**.
Every requested integer output below 16 bits emits one `mtWarning` through the
VapourSynth core log at node creation, explaining the banding risk. It continues
processing. The warning applies to both entries and to float-to-integer output
as well. Default integer16 and all float outputs do not emit this warning.

`dither_type` remains a compatibility argument accepting only `None`/`"none"`;
other modes fail at construction. To dither, request 16-bit or FP32 output and
then use `core.resize.Point(..., format=..., dither_type=...)`. GPU resize in
R81 supports none/ordered/random; use CPU resize for error diffusion.

Direct limited integer Y widening is an exact left shift. Full integer Y uses
the ratio of storage peaks. When type/depth matches, Y is shared bit-for-bit,
including float headroom; no Y output write is recorded. Both public entries
execute the same kernels and conversion policy. Their only difference is the
core upload/download needed for CPU-frame output.

## Independent verification

`tests/shader_oracle_host.cpp` freezes the pre-fusion host from commit
6d946dcb07015b4a00c865809b79ca920d9fc5bf. The oracle uses real core GPU resize
at both boundaries and the literal original GLSL hook bodies. It does not copy
the new conversion code. It is test-only and is not packaged.

`tests/validate_depth.py` checks all nine input integer depths and both float
depths, both range values, all eleven output formats, and both public entries;
it compares all three planes at gradient, random, and noisy frames. It also
checks legal endpoints, untagged and legacy input, per-frame concurrent seeks,
float headroom, malformed tags/options, defaults, and warning count/content.
`tests/validate_matrix.py` adds minimal dimensions, dispatch tails, multiple
instances and concurrent seeks. Real-video and throughput reports identify
the exact candidate binary independently of historical release results.
