"""Observe unmodified Python locals and Vulkan debug planes to isolate roundoff."""
import json
from pathlib import Path
import sys
import argparse
import numpy as np
import vapoursynth as vs
from compare import ROOT,make_source,metric
from common import load_plugin
sys.path.insert(0,str(ROOT/'reference'))
import KrigBilateral as ref

core=vs.core;core.num_threads=4
load_plugin()
ap=argparse.ArgumentParser();ap.add_argument('--width',type=int,default=32);ap.add_argument('--height',type=int,default=24)
args=ap.parse_args()
src=make_source(args.width,args.height,vs.YUV420P16)
nodes={stage:core.krigvk.KrigBilateral(src,debug=stage) for stage in (1,2,3,4,5,6,7)}
rows=[]
for n in (1,3,6,7):
    with src.get_frame(n) as frame:
        y,u,v=[ref._normalized(np.asarray(frame[p]),frame.format) for p in range(3)]
    vy,vvar=ref._vertical_guide(y,u.shape[0]);g,gvar=ref._horizontal_guide(vy,vvar)
    grabbed=[]
    def trace(frame,event,arg):
        if frame.f_code is ref._solve_rows.__code__ and event=='return':
            grabbed.append({k:frame.f_locals[k].copy() for k in ('local_var','cov','c','samples','target_y')})
        return trace
    sys.settrace(trace)
    ou,ov=ref._solve_rows(y,u,v,g,gvar,0,args.height)
    sys.settrace(None)
    d=grabbed[0]
    expected={1:(np.repeat(vy,2,axis=0),np.repeat(vvar,2,axis=0)),
              2:tuple(np.repeat(np.repeat(a,2,axis=0),2,axis=1) for a in (g,gvar)),
              3:(d['local_var'],y),4:(d['cov'][...,0,1],d['c'][...,0]),5:(ou,ov),
              6:(d['samples'][...,0,0],d['samples'][...,0,1]),
              7:(np.exp(-4*y),ref._rsqrt32(1+y))}
    for stage,node in nodes.items():
        with node.get_frame(n) as frame:
            for p in (1,2):
                actual=np.asarray(frame[p]).copy();e=expected[stage][p-1]
                result=metric(e,actual)
                rows.append({'frame':n,'stage':stage,'component':p,**result})
                print(n,stage,p,result['different'],result['max'],result['rms'],flush=True)
result_path=ROOT/f'results/stage-inspection-{args.width}x{args.height}.json'
result_path.parent.mkdir(parents=True,exist_ok=True)
result_path.write_text(json.dumps(rows,indent=2),encoding='utf-8')
