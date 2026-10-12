"""CPU KrigBilateral reconstruction for VapourSynth."""

from __future__ import annotations

import numpy as np
import vapoursynth as vs


_CHROMA_444 = {
    8: vs.YUV444P8,
    9: vs.YUV444P9,
    10: vs.YUV444P10,
    12: vs.YUV444P12,
    14: vs.YUV444P14,
    16: vs.YUV444P16,
}

def _half_down(exact: np.ndarray) -> np.ndarray:
    half = exact.astype(np.float16)
    return np.where(half.astype(np.float32) > exact,
                    np.nextafter(half, np.float16(-np.inf)).astype(np.float32),
                    half.astype(np.float32))


def _half_nearest_up(exact: np.ndarray) -> np.ndarray:
    nearest = exact.astype(np.float16).astype(np.float32)
    lower = _half_down(exact)
    upper = np.nextafter(lower.astype(np.float16), np.float16(np.inf)).astype(np.float32)
    midpoint = (lower + upper) * np.float32(0.5)
    return np.where(exact == midpoint, upper, nearest)


def _normalized(a: np.ndarray, fmt: vs.VideoFormat) -> np.ndarray:
    if fmt.sample_type == vs.FLOAT:
        return a.astype(np.float32, copy=False)
    return a.astype(np.float32) / float((1 << fmt.bits_per_sample) - 1)


def _exp32(argument: np.ndarray) -> np.ndarray:
    return np.exp(argument)


def _rsqrt32(argument: np.ndarray) -> np.ndarray:
    return 1.0 / np.sqrt(argument)


def _one_plus_ratio(numerator: np.ndarray, denominator: np.ndarray) -> np.ndarray:
    reciprocal = np.float32(1) / denominator
    return (np.float64(1) + numerator.astype(np.float64) *
            reciprocal.astype(np.float64)).astype(np.float32)


def _store(a: np.ndarray, dst: np.ndarray, fmt: vs.VideoFormat) -> None:
    a = np.clip(a, 0.0, 1.0)
    if fmt.sample_type == vs.FLOAT:
        dst[...] = a
    else:
        dst[...] = np.rint(_half_down(a.astype(np.float32)) *
                           ((1 << fmt.bits_per_sample) - 1)).astype(dst.dtype)


def _vertical_guide(y: np.ndarray, out_h: int) -> tuple[np.ndarray, np.ndarray]:
    """First shader pass: the two luma rows inside each chroma row."""
    top = y[0:2 * out_h:2]
    bottom = y[1:2 * out_h:2]
    rows = np.arange(out_h, dtype=np.float32)[:, None]
    luma_pos = (rows + np.float32(0.5)) / np.float32(out_h)
    luma_pt = np.float32(1 / (2 * out_h))
    low_pos = luma_pt * (2 * rows + np.float32(0.5))
    high_pos = luma_pt * (2 * rows + np.float32(1.5))
    low_weight = np.float32(1) - np.abs((low_pos - luma_pos) * np.float32(out_h))
    high_weight = np.float32(1) - np.abs((high_pos - luma_pos) * np.float32(out_h))
    total_weight = low_weight + high_weight
    mean = (low_weight * top + high_weight * bottom) / total_weight
    second = (low_weight * (top * top) + high_weight * (bottom * bottom)) / total_weight
    variance = np.abs(second - mean * mean)
    return _half_down(mean), _half_down(variance)


def _horizontal_guide(guide: np.ndarray, variance: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Second shader pass: adjacent columns, with vertical variance added."""
    left = guide[:, 0::2]
    right = guide[:, 1::2]
    mean = (left + right) * 0.5
    second = (left * left + right * right) * 0.5
    sampled_var = _half_nearest_up((variance[:, 0::2] + variance[:, 1::2]) * 0.5)
    result_var = np.abs(second - mean * mean) + sampled_var
    return _half_down(mean), _half_down(result_var)


def _solve_rows(yn: np.ndarray, un: np.ndarray, vn: np.ndarray,
                guide: np.ndarray, guide_var: np.ndarray,
                start: int, end: int) -> tuple[np.ndarray, np.ndarray]:
    h, w = yn.shape
    ch, cw = un.shape
    yy, xx = np.indices((end - start, w), dtype=np.float32)
    yy += np.float32(start)
    gx = xx * cw / w
    gy = (yy + 0.5) * ch / h - 0.5
    base_x = np.rint(gx).astype(np.int32)
    base_y = np.rint(gy).astype(np.int32)
    off = np.stack((gx - base_x.astype(np.float32),
                    gy - base_y.astype(np.float32)), axis=-1)

    coords = np.array([[-1, -1], [-1, 0], [-1, 1], [0, -1], [0, 1],
                       [1, -1], [1, 0], [1, 1], [0, 0]], dtype=np.float32)
    ix = np.clip(base_x[..., None] + coords[:, 0], 0, cw - 1)
    iy = np.clip(base_y[..., None] + coords[:, 1], 0, ch - 1)
    ix = ix.astype(np.intp)
    iy = iy.astype(np.intp)
    samples_y = guide[iy, ix]
    samples_var = guide_var[iy, ix]
    samples_u = un[iy, ix]
    samples_v = vn[iy, ix]
    samples = np.stack((samples_y, samples_var, samples_u, samples_v), axis=-1)

    spatial = np.clip(1.5 - np.abs(coords[None, None, :, :] - off[..., None, :]), 0.0, 1.0)
    total_w = spatial[..., 0] * spatial[..., 1]
    moments = np.stack((samples_y, samples_y * samples_y, samples_var), axis=-1)
    total = np.zeros(moments.shape[:2] + (3,), dtype=np.float32)
    for index in range(9):
        total = (total.astype(np.float64) +
                 total_w[..., index, None].astype(np.float64) *
                 moments[..., index, :].astype(np.float64)).astype(np.float32)
    total /= np.maximum(total_w.sum(axis=2, keepdims=True), 1e-12)
    local_var = 0.05 * 0.05 + np.abs(total[..., 1] - total[..., 0] ** 2) + total[..., 2]
    local_var = np.maximum(local_var, 1e-8)
    x = samples
    center = x[..., 8, :]
    d = coords[:8]
    radius = 1.0
    # C(i,j) and c(i), evaluated for every output pixel as a small batch.
    delta = x[..., :, None, :] - x[..., None, :, :]
    coords_all = coords
    spatial_ij = ((coords_all[..., :, None, :] - coords_all[..., None, :, :]) / radius) ** 2
    denom_ij = local_var[..., None, None] + x[..., :, None, 1] + x[..., None, :, 1]
    cov = _exp32(-0.5 * (((delta[..., 0] ** 2 + 0.0) / denom_ij) + spatial_ij.sum(axis=-1)))
    cov *= _rsqrt32(1.0 + (x[..., :, None, 1] + x[..., None, :, 1]) / local_var[..., None, None])
    target_y = yn[start:end]
    cov += ((x[..., :, None, 0] - target_y[..., None, None]) * (x[..., None, :, 0] - target_y[..., None, None])) / local_var[..., None, None]
    delta_c = x[..., :8, 0] - target_y[..., None]
    denom_c = local_var[..., None] + x[..., :8, 1]
    c = _exp32(-0.5 * ((delta_c ** 2) / denom_c + ((d[None, None, :, 0] - off[..., None, 0]) ** 2 + (d[None, None, :, 1] - off[..., None, 1]) ** 2) / radius ** 2))
    c *= _rsqrt32(_one_plus_ratio(x[..., :8, 1], local_var[..., None]))
    c_center = _exp32(-0.5 * (((center[..., 0] - target_y) ** 2) /
                      (local_var + center[..., 1]) + np.sum(off * off, axis=-1)))
    c_center *= _rsqrt32(_one_plus_ratio(center[..., 1], local_var))

    # Shader's reduced 8x8 system and its fixed-order, no-pivot elimination.
    a = (cov[:, :, :8, :8] - cov[:, :, None, :8, 8] -
         cov[:, :, :8, 8, None] + cov[:, :, 8, 8, None, None])
    b = c - c_center[..., None] - cov[:, :, :8, 8] + cov[:, :, 8, 8, None]
    a = a.copy()
    b = b.copy()
    with np.errstate(divide="ignore", invalid="ignore"):
        for k in range(7):
            for i in range(k + 1, 8):
                numerator = a[..., i, k]
                pivot = a[..., k, k]
                b[..., i] -= (b[..., k] * numerator) / pivot
                for j in range(i, 8):
                    a[..., i, j] -= (a[..., k, j] * numerator) / pivot
                    a[..., j, i] = a[..., i, j]
        interp = center.copy()
        for i in range(7, -1, -1):
            for j in range(i + 1, 8):
                b[..., i] -= a[..., i, j] * b[..., j]
            b[..., i] /= a[..., i, i]
            interp += np.nan_to_num(b[..., i])[..., None] * (x[..., i, :] - center)
    return interp[..., 2], interp[..., 3]


def _krig_frame(y: np.ndarray, u: np.ndarray, v: np.ndarray,
                fmt: vs.VideoFormat) -> tuple[np.ndarray, np.ndarray]:
    h, w = y.shape
    yn = _normalized(y, fmt)
    un = _normalized(u, fmt)
    vn = _normalized(v, fmt)
    vertical_y, vertical_var = _vertical_guide(yn, u.shape[0])
    guide, guide_var = _horizontal_guide(vertical_y, vertical_var)
    result_u = np.empty((h, w), dtype=np.float32)
    result_v = np.empty((h, w), dtype=np.float32)
    for start in range(0, h, 16):
        end = min(start + 16, h)
        result_u[start:end], result_v[start:end] = _solve_rows(
            yn, un, vn, guide, guide_var, start, end)
    return result_u, result_v


def krig_bilateral(clip: vs.VideoNode) -> vs.VideoNode:
    """Approximate CPU reconstruction for constant-format YUV 4:2:0."""
    if clip.format is None or clip.format.color_family != vs.YUV:
        raise vs.Error("krig_bilateral: expected a constant-format YUV clip")
    fmt = clip.format
    if (fmt.subsampling_w, fmt.subsampling_h) != (1, 1):
        raise vs.Error("krig_bilateral: input must be YUV 4:2:0")
    if fmt.sample_type == vs.FLOAT:
        out_fmt = vs.YUV444PS if fmt.bits_per_sample == 32 else vs.YUV444PH
    else:
        try:
            out_fmt = _CHROMA_444[fmt.bits_per_sample]
        except KeyError as exc:
            raise vs.Error(f"unsupported bit depth: {fmt.bits_per_sample}") from exc
    out_format_id = int(out_fmt)
    out = clip if clip.format.id == out_format_id else clip.resize.Point(format=out_format_id)

    def selector(n: int, f: vs.VideoFrame) -> vs.VideoFrame:
        dst = out.get_frame(n).copy()
        y = np.asarray(f[0])
        u = np.asarray(f[1])
        v = np.asarray(f[2])
        uu, vv = _krig_frame(y, u, v, fmt)
        _store(uu, np.asarray(dst[1]), out.format)
        _store(vv, np.asarray(dst[2]), out.format)
        return dst

    return vs.core.std.ModifyFrame(clip=out, clips=[clip], selector=selector)

