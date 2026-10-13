"""Original GLSL parity, formats/tails, luma/props, residency and concurrent seeks."""
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
    parser.add_argument('--output', type=Path, default=ROOT / 'results/format-matrix.json')
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    core = vs.core
    core.num_threads = 8
    dll = load_plugin()
    oracle = Path(os.environ.get('KRIGVK_ORACLE', str(ROOT / 'build/oracle' / ('krig_shader_oracle.dll' if os.name=='nt' else 'krig_shader_oracle.so'))))
    core.std.LoadPlugin(str(oracle))
    cases = []
    formats = ('YUV420P8', 'YUV420P9', 'YUV420P10', 'YUV420P12', 'YUV420P14', 'YUV420P16', 'YUV420PH', 'YUV420PS')
    for name in formats:
        for width, height in ((2,2), (10,6), (34,26), (128,72)):
            source = make_source(width, height, getattr(vs,name))
            native = core.krigvk.KrigBilateral(source)
            reference = core.krigvk_oracle.KrigBilateral(source)
            assert native.num_frames == source.num_frames
            assert (native.fps_num,native.fps_den) == (source.fps_num,source.fps_den)
            rows = []
            for n in range(8):
                with source.get_frame(n) as original, native.get_frame(n) as frame, reference.get_frame(n) as expected:
                    assert (frame.width,frame.height) == (width,height)
                    assert frame.format.subsampling_w == frame.format.subsampling_h == 0
                    assert frame.format.bits_per_sample == original.format.bits_per_sample
                    assert np.array_equal(np.asarray(original[0]),np.asarray(frame[0]))
                    for prop in ('_Range','_Matrix','_Transfer','_Primaries','_FieldBased'):
                        assert frame.props[prop] == original.props[prop]
                    assert '_ChromaLocation' not in frame.props
                    for plane in (1,2):
                        row = metric(np.asarray(expected[plane]),np.asarray(frame[plane]))
                        assert row['finite'] and row['equal'], (name,width,height,n,plane,row)
                        rows.append({'frame':n,'plane':plane,**row})
            cases.append({'format':name,'width':width,'height':height,'rows':rows})
            print(name,width,height,'bit-exact',flush=True)
    source = make_source(34,26,vs.YUV420P16)
    direct = core.krigvk.KrigBilateral(source)
    resident = core.std.GPUDownload(core.krigvk.KrigBilateralGPU(core.std.GPUUpload(source)))
    second = core.krigvk.KrigBilateral(source)
    requests = [7,1,3,0,7,6,2,1]
    expected = {}
    for n in range(8):
        with direct.get_frame(n) as frame:
            expected[n] = [np.asarray(frame[p]).copy() for p in range(3)]
    futures = [resident.get_frame_async(n) for n in requests] + [second.get_frame_async(n) for n in requests]
    for n,future in zip(requests*2,futures):
        with future.result() as frame:
            assert all(np.array_equal(expected[n][p],np.asarray(frame[p])) for p in range(3))
    errors = []
    for clip,options in ((core.std.BlankClip(format=vs.RGB24),{}), (core.std.BlankClip(format=vs.YUV444P16),{}),
                         (source,{'debug':8}), (source,{'reference':'shader'})):
        try:
            core.krigvk.KrigBilateral(clip,**options)
        except vs.Error as error:
            errors.append(str(error))
        else:
            raise AssertionError('Invalid or removed argument was accepted')
    result = {'dll_sha256':hashlib.sha256(dll.read_bytes()).hexdigest(),
              'oracle_sha256':hashlib.sha256(oracle.read_bytes()).hexdigest(),
              'shader_sha256':hashlib.sha256((ROOT/'reference/KrigBilateral.glsl').read_bytes()).hexdigest(),
              'reference':'original GLSL hooks with range-aware core resize transport; FP32 guide storage',
              'cases':cases,'all_exact':True,'luma_exact':True,'color_properties_preserved':True,
              'output_chroma_location_removed':True,'float_chroma_convention':'signed -0.5..0.5',
              'cpu_gpu_entries_identical':True,'multiple_instances_concurrent_random_requests_exact':True,
              'invalid_inputs_and_removed_arguments_rejected':errors}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2),encoding='utf-8')


if __name__ == '__main__':
    main()
