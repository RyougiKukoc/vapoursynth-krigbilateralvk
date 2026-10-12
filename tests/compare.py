"""Deterministic full-plane comparisons to the unchanged Python and placebo references."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import time
import numpy as np
import vapoursynth as vs
from common import load_plugin

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'reference'))
from KrigBilateral import krig_bilateral

def make_source(w,h,fmt,length=8):
    core=vs.core
    blank=core.std.BlankClip(width=w,height=h,format=fmt,length=length)
    def fill(n,f):
        f=f.copy()
        for p in range(3):
            a=np.asarray(f[p]); hh,ww=a.shape
            yy,xx=np.mgrid[:hh,:ww]
            rng=np.random.default_rng(7319+n*3+p)
            if n==0: value=np.full(a.shape,0.5 if p else 0.25)
            elif n==1: value=0.1+0.8*xx/max(ww-1,1)
            elif n==2: value=0.1+0.8*(xx+yy>=ww)
            elif n==3: value=rng.random(a.shape)
            elif n==4: value=0.49999+rng.normal(0,0.00001,a.shape)
            elif n==5: value=0.5+0.4*np.sin((xx+yy)/4)
            elif n==6: value=((xx+yy)%2).astype(float)
            else: value=0.1+0.8*xx/max(ww,1)+rng.normal(0,0.01,a.shape)
            if f.format.sample_type==vs.INTEGER:
                value=np.rint(np.clip(value,0,1)*((1<<f.format.bits_per_sample)-1))
            a[:]=value
        f.props.update(_Range=0,_Matrix=1,_Transfer=1,_Primaries=1,_ChromaLocation=0,_FieldBased=0)
        return f
    return core.std.ModifyFrame(blank,blank,fill)

def metric(a,b):
    delta=a.astype(np.float64)-b.astype(np.float64)
    return {'equal':bool(np.array_equal(a,b)),'different':int(np.count_nonzero(a!=b)),
            'max':float(np.max(np.abs(delta))), 'rms':float(np.sqrt(np.mean(delta*delta))),
            'finite':bool(np.isfinite(b).all()),'sha256':hashlib.sha256(b.tobytes()).hexdigest()}

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--width',type=int,default=32);ap.add_argument('--height',type=int,default=24)
    ap.add_argument('--output',type=Path,required=True)
    ap.add_argument('--frames',type=int,default=8)
    ap.add_argument('--format',default='YUV420P16')
    ap.add_argument('--no-python',action='store_true')
    ap.add_argument('--reference',default='python',choices=('python','shader'))
    args=ap.parse_args()
    if args.output.exists():raise FileExistsError(args.output)
    core=vs.core; core.num_threads=4
    dll=load_plugin()
    src=make_source(args.width,args.height,getattr(vs,args.format),args.frames)
    nodes={'vk':core.krigvk.KrigBilateral(src,reference=args.reference)}
    if not args.no_python: nodes['python']=krig_bilateral(src)
    if args.format=='YUV420P16' and hasattr(core,'placebo'):
        nodes['placebo']=core.placebo.Shader(src,shader=str(ROOT/'reference/KrigBilateral.glsl'))
    rows=[]
    for n in range(args.frames):
        data={}
        for name,node in nodes.items():
            before=time.perf_counter()
            with node.get_frame(n) as f:data[name]=[np.asarray(f[p]).copy() for p in range(3)]
            print(name,n,f'{time.perf_counter()-before:.4f}s',flush=True)
        with src.get_frame(n) as f:
            assert np.array_equal(np.asarray(f[0]),data['vk'][0])
        for reference in nodes:
            if reference=='vk':continue
            for p in range(3):rows.append({'frame':n,'plane':p,'reference':reference,**metric(data[reference][p],data['vk'][p])})
    result={'format':args.format,'width':args.width,'height':args.height,'frames':args.frames,
            'reference_mode':args.reference,
            'dll_sha256':hashlib.sha256(dll.read_bytes()).hexdigest(),'core':str(core),'rows':rows}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2),encoding='utf-8')
    for reference in nodes:
        if reference=='vk':continue
        r=[x for x in rows if x['reference']==reference]
        print(reference,'different',sum(x['different'] for x in r),'max',max(x['max'] for x in r))

if __name__=='__main__':main()
