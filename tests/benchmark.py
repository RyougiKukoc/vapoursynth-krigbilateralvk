"""Fresh-process full-frame throughput; preloaded inputs and disjoint frame numbers."""
import argparse
import hashlib
import platform
import shutil
import subprocess
import json
import os
from pathlib import Path
import sys
import time
import numpy as np
import vapoursynth as vs
from common import ROOT,make_source,load_plugin
def environment(core):
    result={'python':sys.version,'os':platform.platform(),'core':str(core),'numpy':np.__version__,
            'plugins':[{'namespace':p.namespace,'path':p.plugin_path,
                        'sha256':hashlib.sha256(Path(p.plugin_path).read_bytes()).hexdigest()}
                       for p in core.plugins() if p.plugin_path and p.namespace in ('krigvk','placebo')],
            'vulkan_devices':core.vulkan_devices}
    if shutil.which('nvidia-smi'):
        result['gpu']=subprocess.check_output(['nvidia-smi','--query-gpu=name,driver_version','--format=csv,noheader'],text=True).strip()
    return result

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--backend',choices=('vk','vk_gpu','placebo','baseline','baseline_gpu','resize_in_gpu','resize_out_gpu'),required=True)
    ap.add_argument('--width',type=int,default=1920);ap.add_argument('--height',type=int,default=1080)
    ap.add_argument('--frames',type=int,default=256);ap.add_argument('--warmup',type=int,default=8)
    ap.add_argument('--prefetch',type=int,default=4);ap.add_argument('--threads',type=int,default=4)
    ap.add_argument('--output',type=Path,required=True)
    ap.add_argument('--raw',type=Path)
    ap.add_argument('--bits',type=int)
    ap.add_argument('--sample-type',choices=('integer','float'))
    ap.add_argument('--dither-type',choices=('none','ordered','random','error_diffusion'),default='none')
    ap.add_argument('--label',default='')
    ap.add_argument('--background-workload',default='',help='Known concurrent GPU activity, recorded with results')
    ap.add_argument('--input-format',choices=('YUV420P16','YUV420PS','YUV444PS'),default='YUV420P16',
                    help='Preconvert held inputs outside timing for conversion ablations')
    args=ap.parse_args()
    if args.output.exists():raise FileExistsError(args.output)
    core=vs.core;core.num_threads=args.threads
    plugin=load_plugin()
    gpu_entry=args.backend.endswith('_gpu')
    wait_path=None
    if gpu_entry:
        wait_path=Path(os.environ.get('KRIGVK_BENCH_WAIT',str(ROOT/'build/bench'/('krig_bench_wait.dll' if os.name=='nt' else 'krig_bench_wait.so'))))
        core.std.LoadPlugin(str(wait_path.resolve()))
    before=time.perf_counter()
    if args.raw:
        if (args.width,args.height)!=(1920,1080):raise ValueError('Raw source is 1920x1080 YUV420P16')
        raw=np.memmap(args.raw,'<u2',mode='r');stride=args.width*args.height*3//2
        blank=core.std.BlankClip(width=args.width,height=args.height,format=vs.YUV420P16,length=8)
        held_indices=[i*(raw.size//stride)//8 for i in range(8)]
        def supply(n,f):
            f=f.copy();off=held_indices[n]*stride
            for p in range(3):
                a=np.asarray(f[p]);length=a.size;a[:]=raw[off:off+length].reshape(a.shape);off+=length
            f.props.update(_Range=0,_Matrix=1,_Transfer=1,_Primaries=1,_ChromaLocation=0,_FieldBased=0)
            return f
        src=core.std.ModifyFrame(blank,blank,supply)
    else:
        held_indices=list(range(8));src=make_source(args.width,args.height,vs.YUV420P16)
    if gpu_entry:
        src=core.std.GPUUpload(src)
        if args.input_format!='YUV420P16':
            src=core.resize.Point(src,format=getattr(vs,args.input_format))
        for n in range(8):
            with core.krigvk_bench.Wait(src).get_frame(n) as completed:
                assert completed[0].tobytes()==bytes([n])
    elif args.input_format!='YUV420P16':
        src=core.resize.Point(src,format=getattr(vs,args.input_format))
    input_format=src.format.name
    held=[src.get_frame(n) for n in range(8)]
    template=core.std.BlankClip(src,length=args.warmup+args.frames+32,gpu=gpu_entry,keep=True)
    src=core.std.ModifyFrame(template,template,lambda n,f:held[n%8])
    options={}
    if args.bits is not None: options['bits']=args.bits
    if args.sample_type is not None: options['sample_type']=vs.FLOAT if args.sample_type=='float' else vs.INTEGER
    # Old releases did not expose dither_type; omitted none is the same default.
    if args.dither_type!='none': options['dither_type']=args.dither_type
    if args.backend=='vk':out=core.krigvk.KrigBilateral(src,**options)
    elif args.backend=='vk_gpu':out=core.krigvk.KrigBilateralGPU(src,**options)
    elif args.backend=='placebo':out=core.placebo.Shader(src,shader=str(ROOT/'reference/KrigBilateral.glsl'))
    elif args.backend=='resize_in_gpu':out=core.resize.Point(src,format=vs.YUV420PS)
    elif args.backend=='resize_out_gpu':out=core.resize.Point(src,format=vs.YUV444P16)
    else:out=src
    output_format=out.format.name
    output_gpu=out.gpu_resident
    assert output_gpu==gpu_entry
    sample_node=core.std.GPUDownload(out) if gpu_entry else out
    with sample_node.get_frame(0) as sample:
        output_sha256=hashlib.sha256(b''.join(sample[p].tobytes() for p in range(len(sample)))).hexdigest()
    if gpu_entry: out=core.krigvk_bench.Wait(out)
    constructed=time.perf_counter()-before
    before=time.perf_counter()
    for f in out[:args.warmup].frames(prefetch=args.prefetch,backlog=args.prefetch):f.close()
    warmed=time.perf_counter()-before
    before=time.perf_counter();count=0
    for f in out[args.warmup:args.warmup+args.frames].frames(prefetch=args.prefetch,backlog=args.prefetch):
        f.close();count+=1
    seconds=time.perf_counter()-before
    assert count==args.frames
    result={'backend':args.backend,'label':args.label,'background_workload':args.background_workload,
            'width':args.width,'height':args.height,
            'plugin_sha256':hashlib.sha256(plugin.read_bytes()).hexdigest(),
            'input_format':input_format,
            'output_format':output_format,'output_sample_sha256':output_sha256,'source_gpu_resident':gpu_entry,
            'output_gpu_resident_before_completion_sink':output_gpu,
            'completion':'waitGPUFrame and 1x1 CPU acknowledgement' if gpu_entry else 'completed full CPU output frame',
            'source_template_keep':True,
            'wait_sink_sha256':hashlib.sha256(wait_path.read_bytes()).hexdigest() if wait_path else None,
            'options':options,
            'frames':args.frames,'warmup':args.warmup,'prefetch':args.prefetch,'threads':args.threads,
            'seconds':seconds,'fps':args.frames/seconds,'construction_seconds':constructed,
            'warmup_seconds':warmed,'source':str(args.raw) if args.raw else 'synthetic eight patterns',
            'source_frame_indices':held_indices,
            'source_sha256':hashlib.file_digest(args.raw.open('rb'),'sha256').hexdigest() if args.raw else None,
            'environment':environment(core)}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(args.backend,result['fps'],flush=True)

if __name__=='__main__':main()
