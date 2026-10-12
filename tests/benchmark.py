"""Fresh-process full-frame throughput; preloaded inputs and disjoint frame numbers."""
import argparse
import hashlib
import platform
import shutil
import subprocess
import json
from pathlib import Path
import sys
import time
import numpy as np
import vapoursynth as vs
from compare import ROOT,make_source
from common import load_plugin
sys.path.insert(0,str(ROOT/'reference'))
from KrigBilateral import krig_bilateral
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
    ap.add_argument('--backend',choices=('vk','python','placebo','baseline'),required=True)
    ap.add_argument('--reference',default='python',choices=('python','shader'))
    ap.add_argument('--width',type=int,default=1920);ap.add_argument('--height',type=int,default=1080)
    ap.add_argument('--frames',type=int,default=256);ap.add_argument('--warmup',type=int,default=8)
    ap.add_argument('--prefetch',type=int,default=4);ap.add_argument('--threads',type=int,default=4)
    ap.add_argument('--output',type=Path,required=True)
    ap.add_argument('--raw',type=Path)
    args=ap.parse_args()
    if args.output.exists():raise FileExistsError(args.output)
    core=vs.core;core.num_threads=args.threads
    load_plugin()
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
    held=[src.get_frame(n) for n in range(8)]
    template=core.std.BlankClip(src,length=args.warmup+args.frames+32)
    src=core.std.ModifyFrame(template,template,lambda n,f:held[n%8])
    if args.backend=='vk':out=core.krigvk.KrigBilateral(src,reference=args.reference)
    elif args.backend=='python':out=krig_bilateral(src)
    elif args.backend=='placebo':out=core.placebo.Shader(src,shader=str(ROOT/'reference/KrigBilateral.glsl'))
    else:out=src
    constructed=time.perf_counter()-before
    before=time.perf_counter()
    for f in out[:args.warmup].frames(prefetch=args.prefetch,backlog=args.prefetch):f.close()
    warmed=time.perf_counter()-before
    before=time.perf_counter();count=0
    for f in out[args.warmup:args.warmup+args.frames].frames(prefetch=args.prefetch,backlog=args.prefetch):
        f.close();count+=1
    seconds=time.perf_counter()-before
    assert count==args.frames
    result={'backend':args.backend,'reference':args.reference,'width':args.width,'height':args.height,
            'frames':args.frames,'warmup':args.warmup,'prefetch':args.prefetch,'threads':args.threads,
            'seconds':seconds,'fps':args.frames/seconds,'construction_seconds':constructed,
            'warmup_seconds':warmed,'source':str(args.raw) if args.raw else 'synthetic eight patterns',
            'source_frame_indices':held_indices,
            'source_sha256':hashlib.file_digest(args.raw.open('rb'),'sha256').hexdigest() if args.raw else None,
            'environment':environment(core)}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(args.backend,args.reference,result['fps'],flush=True)

if __name__=='__main__':main()
