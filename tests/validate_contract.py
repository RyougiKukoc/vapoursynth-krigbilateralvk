"""Exercise node/frame rejection and signed-float/output metadata contracts."""
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np
import vapoursynth as vs
from common import ROOT, load_plugin


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    core=vs.core
    core.num_threads=4
    dll=load_plugin()
    checks=[]
    source=core.std.BlankClip(width=16,height=12,format=vs.YUV420P16,length=2)

    def rejected(name,clip,options,substring,frame=False,entry='KrigBilateral'):
        try:
            output=getattr(core.krigvk,entry)(clip,**options)
            if frame:
                output.get_frame(0).close()
        except vs.Error as error:
            assert substring in str(error),(name,str(error))
            checks.append({'case':name,'phase':'frame' if frame else 'node','rejected':True,'reason':str(error)})
        else:
            raise AssertionError('Invalid input accepted: '+name)

    for fmt in (vs.GRAY8,vs.RGB24,vs.YUV444P16,vs.YUV422P10):
        rejected('wrong-format-'+str(fmt),core.std.BlankClip(width=16,height=12,format=fmt),{},'YUV420')
    variable_format=core.std.Splice([source,core.std.BlankClip(source,format=vs.YUV420P8)],mismatch=True)
    assert variable_format.format is None or variable_format.format.id==0
    rejected('variable-format',variable_format,{},'constant-format')
    variable_size=core.std.Splice([source,core.std.BlankClip(source,width=32,height=24)],mismatch=True)
    assert variable_size.width==0 or variable_size.height==0
    rejected('variable-size',variable_size,{},'fixed, positive, even')
    for entry in ('KrigBilateral','KrigBilateralGPU'):
        assert 'debug:' not in str(getattr(core.krigvk,entry))
        assert 'dither_type:' not in str(getattr(core.krigvk,entry))
        for value in ('none','ordered'):
            rejected('removed-dither-'+entry+'-'+value,source,{'dither_type':value},'dither_type',entry=entry)
        for value in (0,1):
            rejected('removed-debug-'+entry+'-'+str(value),source,{'debug':value},'debug',entry=entry)
    gpu_source=core.std.GPUUpload(source)
    # R81 adapts residency from the registered vnode signature before invoking
    # a plugin. Cross-residency calls therefore have valid automatic transport.
    adapted_cpu=core.krigvk.KrigBilateral(gpu_source)
    adapted_gpu=core.krigvk.KrigBilateralGPU(source)
    assert not adapted_cpu.gpu_resident and adapted_gpu.gpu_resident
    with adapted_cpu.get_frame(0) as cpu,core.std.GPUDownload(adapted_gpu).get_frame(0) as gpu:
        assert all(cpu[p].tobytes()==gpu[p].tobytes() for p in range(3))
    checks.append({'case':'core-residency-adapters','passed':True})
    for location in range(1,6):
        tagged=core.std.SetFrameProps(source,_ChromaLocation=location)
        rejected('unsupported-chroma-placement-'+str(location),tagged,{},'only left chroma',frame=True)
    for field in (1,2):
        tagged=core.std.SetFrameProps(source,_FieldBased=field)
        rejected('interlaced-'+str(field),tagged,{},'only progressive',frame=True)
    for key in ('_ChromaLocation','_FieldBased'):
        malformed=core.std.SetFrameProps(source,**{key:'invalid'})
        rejected('malformed-'+key,malformed,{},key+' must be one integer',frame=True)

    # A custom property and timestamps survive; only obsolete siting is removed.
    for range_value in (0,1):
        tagged=core.std.SetFrameProps(source,_Range=range_value,_Matrix=1,_Primaries=1,_Transfer=1,
                                      _ChromaLocation=0,_FieldBased=0,_DurationNum=1,_DurationDen=24,Label='contract-test')
        with tagged.get_frame(0) as original,core.krigvk.KrigBilateral(tagged).get_frame(0) as output:
            assert '_ChromaLocation' not in output.props
            for key in ('_Range','_Matrix','_Primaries','_Transfer','_FieldBased','_DurationNum','_DurationDen','Label'):
                assert output.props[key]==original.props[key]
        checks.append({'case':'integer-properties-range-'+str(range_value),'passed':True})

    # All claimed integer bit depths, including non-preset 11/13/15, really load.
    for bits in range(8,17):
        fmt=core.query_video_format(vs.YUV,vs.INTEGER,bits,1,1)
        clip=core.std.BlankClip(width=16,height=12,format=fmt.id,color=[(1<<bits)//4,(1<<bits)//2,(1<<bits)//2])
        with core.krigvk.KrigBilateral(clip).get_frame(0) as output:
            assert output.format.bits_per_sample==16
            for plane in (1,2):
                assert np.all(np.asarray(output[plane])==32768)
        checks.append({'case':'integer-bit-depth-'+str(bits),'passed':True})

    for name in ('YUV420PH','YUV420PS'):
        for chroma in (-0.5,-0.25,0.0,0.25,0.5):
            clip=core.std.BlankClip(width=16,height=12,format=getattr(vs,name),color=[0.25,chroma,-chroma])
            normal=core.krigvk.KrigBilateral(clip)
            resident=core.std.GPUDownload(core.krigvk.KrigBilateralGPU(core.std.GPUUpload(clip)))
            with clip.get_frame(0) as original,normal.get_frame(0) as output,resident.get_frame(0) as gpu:
                for plane in range(3):
                    assert output[plane].tobytes()==gpu[plane].tobytes()
                    if plane==0:
                        assert output[plane].tobytes()==original[plane].tobytes()
                    else:
                        assert np.all(np.asarray(output[plane])==(chroma if plane==1 else -chroma))
            checks.append({'case':name+'-signed-chroma-'+str(chroma),'passed':True})
    result={'dll_sha256':hashlib.sha256(dll.read_bytes()).hexdigest(),'checks':checks,'all_passed':True}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2),encoding='utf-8')
    print('Passed',len(checks),'input/output contract cases')


if __name__=='__main__':
    main()
