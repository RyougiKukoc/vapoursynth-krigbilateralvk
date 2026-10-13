"""Check range endpoints, per-frame metadata, output formats and core resize dithering."""
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np
import vapoursynth as vs
from common import load_plugin, make_source


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists(): raise FileExistsError(args.output)
    core=vs.core
    core.num_threads=4
    plugin=load_plugin()
    cases=[]

    def invoke(source,gpu=False,**options):
        result=core.krigvk.KrigBilateralGPU(core.std.GPUUpload(source),**options) if gpu else core.krigvk.KrigBilateral(source,**options)
        assert result.gpu_resident==gpu
        return result

    def cpu(node): return core.std.GPUDownload(node) if node.gpu_resident else node

    # Check the meaning of normalization against legal integer endpoints, not
    # another invocation of our host/transport code. Float samples are canonical.
    for bits in (8,10,16):
        fmt=core.query_video_format(vs.YUV,vs.INTEGER,bits,1,1)
        scale=1<<(bits-8)
        for full in (0,1):
            black=0 if full else 16*scale
            white=(1<<bits)-1 if full else 235*scale
            chroma_step=((1<<bits)-1)/4 if full else 56*scale
            center=1<<(bits-1)
            for y,expected_y in ((black,0.0),(white,1.0)):
                clip=core.std.BlankClip(width=16,height=12,format=fmt.id,length=2,
                                        color=[y,round(center-chroma_step),round(center+chroma_step)])
                clip=core.std.SetFrameProps(clip,_Range=full,_ChromaLocation=0)
                uv_den=(1<<bits)-1 if full else 224*scale
                expected_uv=[(round(center-chroma_step)-center)/uv_den,(round(center+chroma_step)-center)/uv_den]
                for gpu in (False,True):
                    with cpu(invoke(clip,gpu,bits=32)).get_frame(0) as frame:
                        assert frame.format.id==int(vs.YUV444PS)
                        assert np.allclose(np.asarray(frame[0]),expected_y,rtol=0,atol=1e-7)
                        for p in (1,2):
                            assert np.allclose(np.asarray(frame[p]),expected_uv[p-1],rtol=0,atol=2e-7)
                        assert frame.props['_Range']==full and '_ChromaLocation' not in frame.props
                    cases.append({'case':'range-endpoints','bits':bits,'full':full,'white':bool(expected_y),'gpu':gpu})

    blank=core.std.BlankClip(width=16,height=12,format=vs.YUV420P8,length=4,color=[16,128,128])
    def tag(n,f):
        out=f.copy();out.props['_Range']=n%2
        return out
    variable=core.std.ModifyFrame(blank,blank,tag)
    for gpu in (False,True):
        node=cpu(invoke(variable,gpu,bits=32))
        for n in (3,0,2,1):
            with node.get_frame(n) as frame:
                assert np.allclose(np.asarray(frame[0]),16/255 if n%2 else 0,rtol=0,atol=1e-7)
                assert np.all(np.asarray(frame[1])==0) and frame.props['_Range']==n%2
        cases.append({'case':'per-frame-range-random-access','gpu':gpu})
    # Untagged YUV follows resize's limited-range default; the legacy property
    # alias follows its inverse numbering and is linked by the VapourSynth core.
    for name,source in (('untagged',core.std.RemoveFrameProps(blank,props=['_Range','_ColorRange'])),
                        ('legacy-limited',core.std.SetFrameProps(blank,_ColorRange=1))):
        with invoke(source,bits=32).get_frame(0) as frame:
            assert np.all(np.asarray(frame[0])==0) and frame.props['_Range']==0
        cases.append({'case':name})

    formats=[core.query_video_format(vs.YUV,vs.INTEGER,b,0,0) for b in range(8,17)]
    formats += [core.get_video_format(vs.YUV444PH),core.get_video_format(vs.YUV444PS)]
    # Quantization must match a separate core resize of the unquantized FP32
    # result. Its source has fractional chroma; dithering must actually affect it.
    dither_changes={}
    for source_format in (vs.YUV420P8,vs.YUV420P16,vs.YUV420PH,vs.YUV420PS):
        source=make_source(34,26,source_format,length=8)
        for full in (0,1):
            tagged=core.std.SetFrameProps(source,_Range=full,Label='depth-test')
            for gpu in (False,True):
                original=core.std.GPUUpload(tagged) if gpu else tagged
                floating=invoke(tagged,gpu,bits=32,sample_type=vs.FLOAT)
                default=invoke(tagged,gpu)
                assert default.format.bits_per_sample==tagged.format.bits_per_sample
                assert default.format.sample_type==tagged.format.sample_type
                with cpu(default).get_frame(1) as frame,tagged.get_frame(1) as src:
                    assert frame[0].tobytes()==src[0].tobytes()
                cases.append({'case':'default-output-format-and-exact-luma','source':str(tagged.format.name),'full':full,'gpu':gpu})
                for fmt in formats:
                    modes=('none','ordered','random','error_diffusion') if fmt.sample_type==vs.INTEGER and fmt.bits_per_sample in (8,16) else ('none',)
                    for dither in modes:
                        result=cpu(invoke(tagged,gpu,bits=fmt.bits_per_sample,sample_type=fmt.sample_type,dither_type=dither))
                        ref_source=cpu(floating) if dither=='error_diffusion' else floating
                        expected=cpu(core.resize.Point(ref_source,format=fmt.id,dither_type=dither))
                        y_source=cpu(original) if dither=='error_diffusion' else original
                        y_format=core.query_video_format(vs.YUV,fmt.sample_type,fmt.bits_per_sample,1,1)
                        y_expected=cpu(core.resize.Point(y_source,format=y_format.id,dither_type=dither))
                        with result.get_frame(1) as frame,expected.get_frame(1) as ref,y_expected.get_frame(1) as y_ref:
                            assert frame.format.id==fmt.id
                            for p in (1,2):
                                assert frame[p].tobytes()==ref[p].tobytes(),(source_format,full,gpu,fmt.name,dither,p)
                            # At unchanged depth the original Y is shared, including
                            # float samples outside nominal range and their raw bits.
                            if fmt.sample_type!=tagged.format.sample_type or fmt.bits_per_sample!=tagged.format.bits_per_sample:
                                assert frame[0].tobytes()==y_ref[0].tobytes()
                            assert frame.props['_Range']==full and frame.props['Label']=='depth-test',dict(frame.props)
                            assert '_ChromaLocation' not in frame.props
                            if source_format==vs.YUV420PS and full==0 and not gpu and fmt.id==int(vs.YUV444P8):
                                dither_changes[dither]=np.asarray(frame[1]).copy()
                        cases.append({'case':'resize-output-equivalence','source':str(tagged.format.name),'full':full,'gpu':gpu,
                                      'output':str(fmt.name),'dither':dither})
    for mode in ('ordered','random','error_diffusion'):
        assert np.any(dither_changes['none']!=dither_changes[mode]),mode+' did not affect the fractional gradient'
    for source_format in (vs.YUV420P8,vs.YUV420P10,vs.YUV420P16,vs.YUV420PH,vs.YUV420PS):
        for full in (0,1):
            source=core.std.SetFrameProps(make_source(34,26,source_format),_Range=full)
            for bits in (8,16,32):
                sample_type=vs.FLOAT if bits==32 else vs.INTEGER
                for dither in ('none','ordered','random','error_diffusion') if bits==8 else ('none',):
                    options={'bits':bits,'sample_type':sample_type,'dither_type':dither}
                    normal=invoke(source,**options)
                    resident=cpu(invoke(source,True,**options))
                    for n in (1,3,7):
                        with normal.get_frame(n) as a,resident.get_frame(n) as b:
                            assert all(a[p].tobytes()==b[p].tobytes() for p in range(3)),(source_format,full,options,n)
                    cases.append({'case':'cpu-gpu-entry-parity','source':source.format.name,'full':full,**options})
    # Output type inference and floating output's documented dither no-op.
    source=make_source(34,26,vs.YUV420P8)
    for options,expected_format in (({'bits':32},vs.YUV444PS),({'bits':16},vs.YUV444P16),
                                    ({'bits':16,'sample_type':vs.FLOAT},vs.YUV444PH)):
        assert invoke(source,**options).format.id==int(expected_format)
        cases.append({'case':'output-type-inference','options':options,'format':str(expected_format)})
    floating=core.resize.Point(source,format=vs.YUV420PS)
    for bits,expected_format in ((8,vs.YUV444P8),(16,vs.YUV444PH),(32,vs.YUV444PS)):
        assert invoke(floating,bits=bits).format.id==int(expected_format)
        cases.append({'case':'float-input-output-type-inference','bits':bits,'format':str(expected_format)})
    for gpu in (False,True):
        baseline=cpu(invoke(source,gpu,bits=32))
        for mode in ('ordered','random','error_diffusion'):
            result=cpu(invoke(source,gpu,bits=32,dither_type=mode))
            with baseline.get_frame(1) as a,result.get_frame(1) as b:
                assert all(a[p].tobytes()==b[p].tobytes() for p in range(3))
            cases.append({'case':'float-output-ignores-dithering','gpu':gpu,'dither':mode})
    # A real signed-float headroom sample must not be clipped by the transport.
    headroom=core.std.BlankClip(width=16,height=12,format=vs.YUV420PS,color=[0.25,0,0])
    def extend(n,f):
        out=f.copy();np.asarray(out[1])[:]=-0.55;np.asarray(out[2])[:]=0.55
        return out
    headroom=core.std.ModifyFrame(headroom,headroom,extend)
    for gpu in (False,True):
        with cpu(invoke(headroom,gpu)).get_frame(0) as frame:
            assert np.allclose(np.asarray(frame[1]),-0.55,rtol=0,atol=1e-7)
            assert np.allclose(np.asarray(frame[2]),0.55,rtol=0,atol=1e-7)
        cases.append({'case':'float-headroom-retained','gpu':gpu})
    for entry in ('KrigBilateral','KrigBilateralGPU'):
        for options in ({'bits':7},{'bits':17},{'bits':2**63-1},{'bits':-2**63},{'bits':32,'sample_type':vs.INTEGER},
                        {'bits':8,'sample_type':vs.FLOAT},{'sample_type':99},{'dither_type':'unknown'},
                        {'dither_type':'none\x00ordered'},{'debug':1,'bits':32},{'debug':1,'dither_type':'ordered'}):
            try:
                getattr(core.krigvk,entry)(blank,**options)
            except vs.Error:
                cases.append({'case':'invalid-output-options','entry':entry,'options':options})
            else: raise AssertionError(('invalid option accepted',entry,options))
        node=getattr(core.krigvk,entry)(blank,bits=None,sample_type=None,dither_type=None)
        with cpu(node).get_frame(0) as frame: assert frame.format.id==int(vs.YUV444P8)
        cases.append({'case':'None-means-defaults','entry':entry})
        for invalid in (-1,2,'invalid'):
            bad=core.std.SetFrameProps(blank,_Range=invalid)
            try:
                cpu(getattr(core.krigvk,entry)(bad)).get_frame(0).close()
            except vs.Error as error:
                assert '_Range must be one integer' in str(error),str(error)
                cases.append({'case':'invalid-range-rejected','entry':entry,'value':invalid})
            else: raise AssertionError('invalid range accepted')
    result={'plugin_sha256':hashlib.sha256(plugin.read_bytes()).hexdigest(),'cases':cases,'all_passed':True,
            'dither_changes_fractional_output':True,'hardware':{k:v for k,v in core.vulkan_device_info.items() if k in ('name','api_version','type')}}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print('Passed',len(cases),'range/depth/dither cases')


if __name__=='__main__': main()
