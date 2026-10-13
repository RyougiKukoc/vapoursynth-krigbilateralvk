"""Check fused range/depth conversion against endpoints and independent resize."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import numpy as np
import vapoursynth as vs
from common import ROOT, load_plugin, make_source, metric


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists(): raise FileExistsError(args.output)
    core=vs.core; core.num_threads=4
    plugin=load_plugin()
    oracle=Path(os.environ.get('KRIGVK_ORACLE',str(ROOT/'build/oracle'/('krig_shader_oracle.dll' if os.name=='nt' else 'krig_shader_oracle.so'))))
    core.std.LoadPlugin(str(oracle))
    cases=[]; warnings=[]
    handler=core.add_log_handler(lambda level,message: warnings.append(message) if level==vs.MESSAGE_TYPE_WARNING else None)
    def cpu(node): return core.std.GPUDownload(node) if node.gpu_resident else node
    def invoke(source,gpu=False,**options):
        node=core.krigvk.KrigBilateralGPU(core.std.GPUUpload(source),**options) if gpu else core.krigvk.KrigBilateral(source,**options)
        assert node.gpu_resident==gpu
        return cpu(node)
    def equal(actual,expected,description,frames=(1,3,7)):
        for n in frames:
            with actual.get_frame(n) as a,expected.get_frame(n) as b:
                assert a.format.id==b.format.id
                for p in range(3):
                    result=metric(np.asarray(b[p]),np.asarray(a[p]))
                    assert result['finite'] and result['equal'],(description,n,p,result)
        cases.append({'case':description,'frames':list(frames),'all_planes_bit_exact':True})

    for bits in range(8,17):
        fmt=core.query_video_format(vs.YUV,vs.INTEGER,bits,1,1); shift=1<<(bits-8)
        for full in (0,1):
            peak=(1<<bits)-1; center=1<<(bits-1); span=peak if full else 224*shift
            for y,expected_y in ((0 if full else 16*shift,0.0),(peak if full else 235*shift,1.0)):
                uv=(0,peak) if full else (16*shift,240*shift)
                clip=core.std.SetFrameProps(core.std.BlankClip(width=16,height=12,format=fmt.id,color=[y,*uv]),_Range=full,_ChromaLocation=0)
                for gpu in (False,True):
                    with invoke(clip,gpu,bits=32).get_frame(0) as frame:
                        assert np.allclose(np.asarray(frame[0]),expected_y,rtol=0,atol=1e-7)
                        for p in (1,2): assert np.allclose(np.asarray(frame[p]),(uv[p-1]-center)/span,rtol=0,atol=2e-7)
                        assert frame.props['_Range']==full and '_ChromaLocation' not in frame.props
                    cases.append({'case':'legal-endpoints','input_bits':bits,'range':full,'white':bool(expected_y),'gpu':gpu})
            neutral=core.std.SetFrameProps(core.std.BlankClip(clip,color=[y,center,center]),_Range=full)
            for gpu in (False,True):
                with invoke(neutral,gpu).get_frame(0) as frame:
                    assert frame.format.id==int(vs.YUV444P16)
                    assert np.all(np.asarray(frame[1])==32768) and np.all(np.asarray(frame[2])==32768)
                cases.append({'case':'integer-default-16-and-neutral','input_bits':bits,'range':full,'gpu':gpu})

    formats=[core.query_video_format(vs.YUV,vs.INTEGER,b,0,0) for b in range(8,17)]
    formats += [core.get_video_format(vs.YUV444PH),core.get_video_format(vs.YUV444PS)]
    input_formats=[core.query_video_format(vs.YUV,vs.INTEGER,b,1,1).id for b in range(8,17)]+[vs.YUV420PH,vs.YUV420PS]
    # Original hook bodies + frozen OLD host + real core GPU resize at both
    # boundaries; no conversion helper from the candidate is used by the oracle.
    for source_format in input_formats:
        source=make_source(34,26,source_format)
        for full in (0,1):
            tagged=core.std.SetFrameProps(source,_Range=full,Label='fused-depth')
            for fmt in formats:
                options={'bits':fmt.bits_per_sample,'sample_type':fmt.sample_type}
                expected=core.krigvk_oracle.KrigBilateral(tagged,**options)
                for gpu in (False,True):
                    actual=invoke(tagged,gpu,**options)
                    equal(actual,expected,f'{source.format.name}-range{full}-to-{fmt.name}-gpu{gpu}')
                    with actual.get_frame(1) as frame:
                        assert frame.props['_Range']==full and frame.props['Label']=='fused-depth'
                        assert '_ChromaLocation' not in frame.props
            default=invoke(tagged)
            assert default.format.bits_per_sample==(16 if tagged.format.sample_type==vs.INTEGER else tagged.format.bits_per_sample)
            with default.get_frame(1) as frame,tagged.get_frame(1) as original:
                if default.format.sample_type==tagged.format.sample_type and default.format.bits_per_sample==tagged.format.bits_per_sample:
                    assert frame[0].tobytes()==original[0].tobytes()
            cases.append({'case':'default-format-and-shared-luma','input':source.format.name,'range':full})
            print(source.format.name,'range',full,'passed',flush=True)

    blank=core.std.BlankClip(width=16,height=12,format=vs.YUV420P8,length=8,color=[16,128,128])
    def tag(n,f):
        out=f.copy(); out.props['_Range']=n%2; return out
    variable=core.std.ModifyFrame(blank,blank,tag)
    for gpu in (False,True):
        node=invoke(variable,gpu,bits=32); order=[7,0,6,1,7,2,5,4,3]
        futures=[node.get_frame_async(n) for n in order]
        for n,future in zip(order,futures):
            with future.result() as frame:
                assert np.allclose(np.asarray(frame[0]),16/255 if n%2 else 0,rtol=0,atol=1e-7)
                assert np.all(np.asarray(frame[1])==0) and frame.props['_Range']==n%2
        cases.append({'case':'per-frame-range-concurrent-seeks','gpu':gpu})
    for name,source,full in (('untagged',core.std.RemoveFrameProps(blank,props=['_Range','_ColorRange']),0),
                            ('legacy-limited',core.std.SetFrameProps(blank,_ColorRange=1),0),
                            ('legacy-full',core.std.SetFrameProps(blank,_ColorRange=0),1)):
        for gpu in (False,True):
            with invoke(source,gpu,bits=32).get_frame(0) as frame:
                assert np.allclose(np.asarray(frame[0]),16/255 if full else 0,rtol=0,atol=1e-7)
                # SetFrameProps exercises the C API's legacy setter mapping.
                # Inspect canonical _Range: R81 Python's legacy getter rewrites
                # the key before mapGetInt and does not invert its value.
                assert frame.props['_Range']==full
            cases.append({'case':name,'gpu':gpu})

    headroom=core.std.BlankClip(width=16,height=12,format=vs.YUV420PS,color=[0.25,0,0])
    def extend(n,f):
        out=f.copy(); np.asarray(out[0])[:]=-0.1; np.asarray(out[1])[:]=-0.55; np.asarray(out[2])[:]=0.55; return out
    headroom=core.std.ModifyFrame(headroom,headroom,extend)
    for gpu in (False,True):
        with invoke(headroom,gpu).get_frame(0) as frame:
            assert np.allclose(np.asarray(frame[0]),-0.1,rtol=0,atol=1e-7)
            assert np.allclose(np.asarray(frame[1]),-0.55,rtol=0,atol=1e-7)
            assert np.allclose(np.asarray(frame[2]),0.55,rtol=0,atol=1e-7)
        cases.append({'case':'float-headroom-retained','gpu':gpu})
    for entry in ('KrigBilateral','KrigBilateralGPU'):
        for options in ({'bits':7},{'bits':17},{'bits':2**63-1},{'bits':-2**63},{'bits':32,'sample_type':vs.INTEGER},
                        {'bits':8,'sample_type':vs.FLOAT},{'sample_type':99},{'dither_type':'none'},{'dither_type':'ordered'},
                        {'dither_type':'random'},{'dither_type':'error_diffusion'},{'dither_type':'none\x00ordered'},{'debug':0}):
            try: getattr(core.krigvk,entry)(blank,**options)
            except vs.Error: cases.append({'case':'invalid-options','entry':entry,'options':options})
            else: raise AssertionError(('invalid option accepted',entry,options))
        with cpu(getattr(core.krigvk,entry)(blank,bits=None,sample_type=None)).get_frame(0) as frame:
            assert frame.format.id==int(vs.YUV444P16)
        cases.append({'case':'None-defaults-to-16','entry':entry})
        for invalid in (-1,2,'invalid',[0,1]):
            bad=core.std.SetFrameProps(blank,_Range=invalid)
            try: cpu(getattr(core.krigvk,entry)(bad)).get_frame(0).close()
            except vs.Error as error:
                assert '_Range must be one integer' in str(error),str(error)
                cases.append({'case':'invalid-range','entry':entry,'value':invalid})
            else: raise AssertionError('invalid range accepted')
    for source_format in (vs.YUV420P8,vs.YUV420P16,vs.YUV420PS):
        source=core.std.BlankClip(blank,format=source_format)
        for gpu in (False,True):
            for bits in range(8,16):
                warnings.clear(); actual=invoke(source,gpu,bits=bits,sample_type=vs.INTEGER)
                assert len(warnings)==1 and 'no dithering' in warnings[0] and 'banding' in warnings[0],warnings
                actual.get_frame(0).close(); actual.get_frame(1).close(); assert len(warnings)==1
                cases.append({'case':'low-depth-warning-once','input':source.format.name,'bits':bits,'gpu':gpu})
            for options in ({},{'bits':16,'sample_type':vs.INTEGER},{'bits':32},{'bits':16,'sample_type':vs.FLOAT}):
                warnings.clear(); invoke(source,gpu,**options); assert not warnings,warnings
                cases.append({'case':'safe-depth-no-warning','input':source.format.name,'gpu':gpu,'options':options})
    core.remove_log_handler(handler)
    result={'plugin_sha256':hashlib.sha256(plugin.read_bytes()).hexdigest(),'oracle_sha256':hashlib.sha256(oracle.read_bytes()).hexdigest(),
            'cases':cases,'all_passed':True,'integer_default_bits':16,'dither':'none',
            'hardware':{k:v for k,v in core.vulkan_device_info.items() if k in ('name','api_version','type')}}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print('Passed',len(cases),'fused range/depth cases')


if __name__=='__main__': main()
