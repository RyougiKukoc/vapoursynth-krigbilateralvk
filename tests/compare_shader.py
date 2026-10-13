"""Compare native-depth and FP32 outputs to the literal original GLSL hooks."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import numpy as np
import vapoursynth as vs
from common import ROOT, make_source, metric, load_plugin


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--width', type=int, default=32)
    parser.add_argument('--height', type=int, default=24)
    parser.add_argument('--frames', type=int, default=8)
    parser.add_argument('--format', default='YUV420P16')
    parser.add_argument('--raw', type=Path)
    parser.add_argument('--exact', action='store_true')
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    core = vs.core
    core.num_threads = 4
    dll = load_plugin()
    oracle = Path(os.environ.get('KRIGVK_ORACLE', str(ROOT / 'build/oracle' / ('krig_shader_oracle.dll' if os.name=='nt' else 'krig_shader_oracle.so'))))
    core.std.LoadPlugin(str(oracle))
    if args.raw:
        stride = args.width * args.height * 3 // 2
        raw = np.memmap(args.raw, '<u2', mode='r')
        count = raw.size // stride
        indexes = [i * (count - 1) // max(args.frames - 1, 1) for i in range(args.frames)]
        blank = core.std.BlankClip(width=args.width, height=args.height, format=vs.YUV420P16, length=args.frames)
        def fill(n, f):
            frame = f.copy()
            offset = indexes[n] * stride
            for plane in range(3):
                data = np.asarray(frame[plane])
                data[:] = raw[offset:offset+data.size].reshape(data.shape)
                offset += data.size
            return frame
        source = core.std.ModifyFrame(blank, blank, fill)
    else:
        indexes = list(range(args.frames))
        source = make_source(args.width, args.height, getattr(vs, args.format), args.frames)
    rows = []
    for mode,options in (('input-depth',{}),('fp32',{'bits':32,'sample_type':vs.FLOAT})):
        candidate = core.krigvk.KrigBilateral(source, **options)
        reference = core.krigvk_oracle.KrigBilateral(source,bits=candidate.format.bits_per_sample,sample_type=candidate.format.sample_type)
        for n in range(args.frames):
            with candidate.get_frame(n) as actual, reference.get_frame(n) as expected:
                for plane in range(3):
                    row = metric(np.asarray(expected[plane]), np.asarray(actual[plane]))
                    if not row['finite']:
                        raise RuntimeError('Nonfinite candidate output')
                    rows.append({'mode': mode, 'frame': n, 'plane': plane, **row})
        subset = [r for r in rows if r['mode'] == mode]
        print('output', mode, 'different', sum(r['different'] for r in subset),
              'max', max(r['max'] for r in subset), flush=True)
    result = {'width': args.width, 'height': args.height, 'format': args.format, 'frames': args.frames,
              'source_frame_indices': indexes, 'reference': 'original GLSL hooks, FP32 intermediate buffers, common texel access',
              'shader_sha256': hashlib.sha256((ROOT / 'reference/KrigBilateral.glsl').read_bytes()).hexdigest(),
              'dll_sha256': hashlib.sha256(dll.read_bytes()).hexdigest(),
              'oracle_sha256': hashlib.sha256(oracle.read_bytes()).hexdigest(),
              'all_exact': all(r['equal'] for r in rows), 'rows': rows}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding='utf-8')
    if args.exact and not result['all_exact']:
        raise SystemExit('Original shader comparison was not bit-exact')


if __name__ == '__main__':
    main()
