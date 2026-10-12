"""Compare complete real-video frames, including all borders and all pixels."""
import argparse
import hashlib
import json
import sys
from pathlib import Path
import numpy as np
import vapoursynth as vs
from compare import ROOT,metric
from common import load_plugin
sys.path.insert(0,str(ROOT/'reference'))
from KrigBilateral import krig_bilateral

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--raw',type=Path,required=True)
    ap.add_argument('--output',type=Path,required=True);ap.add_argument('--frames',type=int,default=3)
    args=ap.parse_args()
    if args.output.exists():raise FileExistsError(args.output)
    core=vs.core;core.num_threads=4
    dll=load_plugin()
    width,height=1920,1080;stride=width*height*3//2
    raw=np.memmap(args.raw,'<u2',mode='r')
    assert raw.size>=stride*args.frames
    indices=[i*((raw.size//stride)-1)//max(args.frames-1,1) for i in range(args.frames)]
    blank=core.std.BlankClip(width=width,height=height,format=vs.YUV420P16,length=args.frames)
    def fill(n,f):
        f=f.copy();off=indices[n]*stride
        for p in range(3):
            a=np.asarray(f[p]);a[:]=raw[off:off+a.size].reshape(a.shape);off+=a.size
        f.props.update(_Range=0,_Matrix=1,_Transfer=1,_Primaries=1,_ChromaLocation=0,_FieldBased=0)
        return f
    source=core.std.ModifyFrame(blank,blank,fill)
    nodes={'vk':core.krigvk.KrigBilateral(source),
           'python':krig_bilateral(source),
           'placebo':core.placebo.Shader(source,shader=str(ROOT/'reference/KrigBilateral.glsl'))}
    rows=[]
    for n in range(args.frames):
        with nodes['vk'].get_frame(n) as f:target=[np.asarray(f[p]).copy() for p in range(3)]
        for name in ('python','placebo'):
            with nodes[name].get_frame(n) as f:
                for p in range(3):
                    row=metric(np.asarray(f[p]),target[p]);assert row['finite']
                    row|={'frame':n,'plane':p,'reference':name,'max_8bit':row['max']*255/65535,
                          'rms_8bit':row['rms']*255/65535}
                    rows.append(row)
            print(n,name,'different',sum(x['different'] for x in rows if x['frame']==n and x['reference']==name),flush=True)
    result={'width':width,'height':height,'frames':args.frames,
            'source_frame_indices':indices,
            'source_sha256':hashlib.file_digest(args.raw.open('rb'),'sha256').hexdigest(),
            'dll_sha256':hashlib.sha256(dll.read_bytes()).hexdigest(),'rows':rows}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2),encoding='utf-8')

if __name__=='__main__':main()
