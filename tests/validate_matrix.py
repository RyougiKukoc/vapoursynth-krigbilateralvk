"""Formats, tails, small frames, preserved luma/props, GPU chains and concurrent seeks."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import numpy as np
import vapoursynth as vs
from compare import ROOT,make_source,metric
from common import load_plugin
sys.path.insert(0,str(ROOT/'reference'))
from KrigBilateral import krig_bilateral

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'results/format-matrix.json')
    args=parser.parse_args()
    if args.output.exists():raise FileExistsError(args.output)
    core=vs.core;core.num_threads=8
    dll=load_plugin()
    cases=[]
    formats=('YUV420P8','YUV420P9','YUV420P10','YUV420P12','YUV420P14','YUV420P16','YUV420PH','YUV420PS')
    for fmt_name in formats:
        for w,h in ((2,2),(10,6),(34,26)):
            src=make_source(w,h,getattr(vs,fmt_name))
            native=core.krigvk.KrigBilateral(src)
            cpu=krig_bilateral(src)
            assert native.num_frames==src.num_frames
            assert (native.fps_num,native.fps_den)==(src.fps_num,src.fps_den)
            rows=[]
            for n in (0,1,3,6,7):
                with src.get_frame(n) as sf,native.get_frame(n) as f,cpu.get_frame(n) as reference:
                    assert (f.width,f.height)==(w,h)
                    assert f.format.subsampling_w==f.format.subsampling_h==0
                    assert f.format.bits_per_sample==sf.format.bits_per_sample
                    assert np.array_equal(np.asarray(sf[0]),np.asarray(f[0]))
                    for prop in ('_Range','_Matrix','_Transfer','_Primaries','_ChromaLocation','_FieldBased'):
                        assert f.props[prop]==sf.props[prop]
                    for p in (1,2):
                        row=metric(np.asarray(reference[p]),np.asarray(f[p]));assert row['finite']
                        rows.append({'frame':n,'plane':p,**row})
            cases.append({'format':fmt_name,'width':w,'height':h,'rows':rows})
            print(fmt_name,w,h,'max',max(r['max'] for r in rows),'different',sum(r['different'] for r in rows),flush=True)
    src=make_source(34,26,vs.YUV420P16)
    direct=core.krigvk.KrigBilateral(src)
    resident=core.std.GPUDownload(core.krigvk.KrigBilateralGPU(core.std.GPUUpload(src)))
    second=core.krigvk.KrigBilateral(src)
    requests=[7,1,3,0,7,6,2,1]
    expected={}
    for n in range(8):
        with direct.get_frame(n) as f:expected[n]=[np.asarray(f[p]).copy() for p in range(3)]
    futures=[resident.get_frame_async(n) for n in requests]+[second.get_frame_async(n) for n in requests]
    for n,future in zip(requests*2,futures):
        with future.result() as f:
            assert all(np.array_equal(expected[n][p],np.asarray(f[p])) for p in range(3))
    errors=[]
    for clip,opts in [(core.std.BlankClip(format=vs.RGB24),{}),
                      (core.std.BlankClip(format=vs.YUV444P16),{}),
                      (src,{'reference':'bad'}),(src,{'debug':8})]:
        try:core.krigvk.KrigBilateral(clip,**opts)
        except vs.Error as e:errors.append(str(e))
        else:raise AssertionError('Invalid input accepted')
    result={'dll_sha256':hashlib.sha256(dll.read_bytes()).hexdigest(),
            'cases':cases,'luma_exact':True,'properties_preserved':True,'cpu_gpu_entries_identical':True,
            'multiple_instances_concurrent_random_requests_exact':True,'invalid_inputs_rejected':errors}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2),encoding='utf-8')

if __name__=='__main__':main()
