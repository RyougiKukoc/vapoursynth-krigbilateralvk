// Test-only completion sink. It is not linked into the production plugin.
#include <VapourSynth4.h>
#include <VSVulkan4.h>

struct WaitData { VSNode* node; VSVideoInfo vi; const VSVULKANAPI* gpu; };

static const VSFrame* VS_CC waitFrame(int n,int reason,void* instance,void**,VSFrameContext* ctx,VSCore* core,const VSAPI* api) {
    auto* d=static_cast<WaitData*>(instance);
    if(reason==arInitial) {api->requestFrameFilter(n,d->node,ctx);return nullptr;}
    if(reason!=arAllFramesReady) return nullptr;
    const VSFrame* source=api->getFrameFilter(n,d->node,ctx);
    if(!source) {api->setFilterError("BenchmarkWait: input frame unavailable",ctx);return nullptr;}
    char error[4096]{};
    int result=d->gpu->waitGPUFrame(source,error,sizeof(error));
    api->freeFrame(source);
    if(result) {api->setFilterError(error,ctx);return nullptr;}
    VSFrame* output=api->newVideoFrame(&d->vi.format,1,1,nullptr,core);
    if(!output) {api->setFilterError("BenchmarkWait: acknowledgement allocation failed",ctx);return nullptr;}
    api->getWritePtr(output,0)[0]=static_cast<unsigned char>(n);
    return output;
}

static void VS_CC freeWait(void* instance,VSCore*,const VSAPI* api) {
    auto* d=static_cast<WaitData*>(instance);api->freeNode(d->node);delete d;
}

static void VS_CC createWait(const VSMap* in,VSMap* out,void*,VSCore* core,const VSAPI* api) {
    auto* d=new WaitData{};
    d->node=api->mapGetNode(in,"clip",0,nullptr);
    d->vi=*api->getVideoInfo(d->node);
    d->vi.width=1;d->vi.height=1;
    api->queryVideoFormat(&d->vi.format,cfGray,stInteger,8,0,0,core);
    d->gpu=api->getVulkanAPI();
    VSFilterDependency dependency{d->node,rpStrictSpatial};
    api->createVideoFilter(out,"BenchmarkWait",&d->vi,waitFrame,freeWait,fmParallel,&dependency,1,d,core);
}

VS_EXTERNAL_API(void) VapourSynthPluginInit2(VSPlugin* plugin,const VSPLUGINAPI* api) {
    api->configPlugin("com.ryougikukoc.krigbilateralvk.benchmark","krigvk_bench","KrigBilateral test completion sink",
        VS_MAKE_VERSION(1,0),VAPOURSYNTH_API_VERSION,0,plugin);
    api->registerFunction("Wait","clip:vnode:gpu;","clip:vnode;",createWait,nullptr,plugin);
}
