// KrigBilateral Vulkan host, LGPL-3.0-or-later.
#include <VapourSynth4.h>
#include <VSVulkan4.h>
#include <array>
#include <cstdint>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <memory>
#include <stdexcept>
#include <string>
#include "shaders.h"

namespace {
constexpr int errSize=8192;
[[noreturn]] void fail(const std::string& s) { throw std::runtime_error("KrigBilateral: "+s); }
void check(VkResult r, const char* operation) { if(r!=VK_SUCCESS) fail(std::string(operation)+" failed ("+std::to_string(r)+")"); }
struct Kernel { VkDescriptorSetLayout set{}; VkPipelineLayout layout{}; VkPipeline pipeline{}; };
struct Filter {
    const VSAPI* api{};
    const VSVULKANAPI* gpu{};
    const VSVulkanFunctions* vk{};
    VSVulkanCoreHandles handles{};
    VSNode* node{};
    VSVideoInfo vi{};
    VSVideoFormat inputFormat{};
    int debug{};
    VSGPUExecPool* pool{};
    std::array<Kernel,3> kernels{};
    ~Filter() {
        if(pool) gpu->freeGPUExecPool(pool); // drains all submissions before pipelines die
        if(vk) for(auto& k:kernels) {
            if(k.pipeline) vk->vkDestroyPipeline(handles.device,k.pipeline,nullptr);
            if(k.layout) vk->vkDestroyPipelineLayout(handles.device,k.layout,nullptr);
            if(k.set) vk->vkDestroyDescriptorSetLayout(handles.device,k.set,nullptr);
        }
        if(node) api->freeNode(node);
    }
    void kernel(Kernel& k,const char* name,const char* source,VSCore* core) {
        std::array<VkDescriptorSetLayoutBinding,8> bindings{};
        uint32_t bindingCount=8;
        for(uint32_t i=0;i<bindingCount;++i) bindings[i]={i,VK_DESCRIPTOR_TYPE_STORAGE_BUFFER,1,VK_SHADER_STAGE_COMPUTE_BIT,nullptr};
        VkDescriptorSetLayoutCreateInfo set{VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO};
        set.flags=VK_DESCRIPTOR_SET_LAYOUT_CREATE_PUSH_DESCRIPTOR_BIT; set.bindingCount=bindingCount; set.pBindings=bindings.data();
        check(vk->vkCreateDescriptorSetLayout(handles.device,&set,nullptr,&k.set),"descriptor layout");
        VkPushConstantRange range{VK_SHADER_STAGE_COMPUTE_BIT,0,20};
        VkPipelineLayoutCreateInfo layout{VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO};
        layout.setLayoutCount=1; layout.pSetLayouts=&k.set; layout.pushConstantRangeCount=1; layout.pPushConstantRanges=&range;
        check(vk->vkCreatePipelineLayout(handles.device,&layout,nullptr,&k.layout),"pipeline layout");
        const auto& f=inputFormat;
        std::string type=f.sampleType==stFloat ? (f.bitsPerSample==16 ? "float16_t" : "float") :
                         (f.bytesPerSample==1 ? "uint8_t" : "uint16_t");
        std::string text="#version 460\n#define SAMPLE "+type+"\n#define INTEGER "+std::to_string(f.sampleType==stInteger)+
            "\n#define MAXVAL "+(f.sampleType==stFloat ? "1.0" : std::to_string((1<<f.bitsPerSample)-1)+".0")+
            "\n#define OUTPUT "+(debug ? "float" : type)+"\n#define DEBUG_STAGE "+std::to_string(debug)+
            "\n#define WIDTH "+std::to_string(vi.width)+"\n#define HEIGHT "+std::to_string(vi.height)+"\n"+source;
        char err[errSize]{};
        VSGPUShader* shader=gpu->compileGPUShader(core,slGLSL,text.c_str(),err,errSize);
        if(!shader) fail(err);
        auto releaseShader=[this](VSGPUShader* object){gpu->freeGPUShader(object);};
        std::unique_ptr<VSGPUShader,decltype(releaseShader)> ownedShader(shader,releaseShader);
        size_t size{}; const uint32_t* code=gpu->getGPUShaderCode(shader,&size);
        if(const char* directory=std::getenv("KRIGVK_DUMP_SPIRV")) {
            std::filesystem::create_directories(directory);
            auto path=std::filesystem::path(directory)/(std::string(name)+".spv");
            std::ofstream stream(path,std::ios::binary);
            stream.write(reinterpret_cast<const char*>(code),size);
            if(!stream) fail("cannot save diagnostic SPIR-V");
        }
        VkShaderModuleCreateInfo module{VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO}; module.codeSize=size; module.pCode=code;
        VkComputePipelineCreateInfo pipeline{VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO};
        pipeline.stage.sType=VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO;
        pipeline.stage.pNext=&module; pipeline.stage.stage=VK_SHADER_STAGE_COMPUTE_BIT; pipeline.stage.pName="main";
        pipeline.layout=k.layout;
        VkResult result=vk->vkCreateComputePipelines(handles.device,VK_NULL_HANDLE,1,&pipeline,nullptr,&k.pipeline);
        check(result,"compute pipeline");
    }
};
struct FrameDel { const VSAPI* api; void operator()(const VSFrame* f)const {if(f) api->freeFrame(f);} };
using Frame=std::unique_ptr<const VSFrame,FrameDel>;
struct Recording {
    const VSVULKANAPI* gpu;
    VSGPUExecContext* context;
    ~Recording(){if(context) gpu->gpuExecAbandon(context);}
};
const VSFrame* VS_CC getFrame(int n,int reason,void* instance,void**,VSFrameContext* ctx,VSCore* core,const VSAPI* api) {
    auto* f=static_cast<Filter*>(instance);
    if(reason==arInitial) {api->requestFrameFilter(n,f->node,ctx); return nullptr;}
    if(reason!=arAllFramesReady) return nullptr;
    try {
        Frame src{api->getFrameFilter(n,f->node,ctx),{api}};
        const VSFrame* planes[3]={src.get(),nullptr,nullptr}; int indices[3]={0,1,2};
        VSFrame* raw=f->debug ? f->gpu->newGPUVideoFrame(&f->vi.format,f->vi.width,f->vi.height,src.get(),core) :
            api->newVideoFrame2(&f->vi.format,f->vi.width,f->vi.height,planes,indices,src.get(),core);
        if(!raw) fail("output allocation failed");
        Frame dst{raw,{api}};
        char err[errSize]{};
        Recording record{f->gpu,f->gpu->gpuExecAcquire(f->pool,err,errSize)};
        if(!record.context) fail(err);
        f->gpu->gpuExecReadsFrame(record.context,src.get());
        f->gpu->gpuExecWritesPlane(record.context,raw,1);
        f->gpu->gpuExecWritesPlane(record.context,raw,2);
        if(f->debug) f->gpu->gpuExecWritesPlane(record.context,raw,0);
        std::array<VkDescriptorBufferInfo,8> buffers{};
        std::array<int32_t,5> push{};
        for(int plane=0;plane<3;++plane) {
            VSVulkanPlaneInfo info{};
            if(f->gpu->getGPUPlane(src.get(),plane,&info)) fail("input must be GPU resident");
            buffers[plane]={info.buffer,0,VK_WHOLE_SIZE};
            push[plane]=int32_t(api->getStride(src.get(),plane)/f->inputFormat.bytesPerSample);
        }
        if(f->debug) {
            VSVulkanPlaneInfo info{};
            if(f->gpu->getGPUPlane(dst.get(),0,&info)) fail("debug output is not GPU resident");
            buffers[7]={info.buffer,0,VK_WHOLE_SIZE};
        } else buffers[7]=buffers[0];
        for(int plane=1;plane<3;++plane) {
            VSVulkanPlaneInfo info{};
            if(f->gpu->getGPUPlane(dst.get(),plane,&info)) fail("output is not GPU resident");
            buffers[4+plane]={info.buffer,0,VK_WHOLE_SIZE};
            push[2+plane]=int32_t(api->getStride(dst.get(),plane)/f->vi.format.bytesPerSample);
        }
        for(int i=0;i<2;++i) {
            VSVulkanBufferInfo info{};
            VkDeviceSize size=VkDeviceSize(f->vi.width)*f->vi.height*(i==0?4:2);
            auto* owner=f->gpu->createGPUBuffer(core,size,VK_BUFFER_USAGE_STORAGE_BUFFER_BIT,
                VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT,0,&info,err,errSize);
            if(!owner) fail(err);
            buffers[3+i]={info.buffer,0,size};
            f->gpu->gpuExecUsesBuffer(record.context,owner);
        }
        uint32_t bindingCount=8;
        std::array<VkWriteDescriptorSet,8> writes{};
        for(uint32_t i=0;i<bindingCount;++i) { auto& w=writes[i]; w.sType=VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET;
            w.dstBinding=i; w.descriptorCount=1; w.descriptorType=VK_DESCRIPTOR_TYPE_STORAGE_BUFFER; w.pBufferInfo=&buffers[i]; }
        VkCommandBuffer cmd=f->gpu->gpuExecCommandBuffer(record.context);
        for(int i=0;i<3;++i) {
            auto& k=f->kernels[i];
            f->vk->vkCmdBindPipeline(cmd,VK_PIPELINE_BIND_POINT_COMPUTE,k.pipeline);
            f->vk->vkCmdPushDescriptorSet(cmd,VK_PIPELINE_BIND_POINT_COMPUTE,k.layout,0,bindingCount,writes.data());
            VkPushConstantsInfo info{VK_STRUCTURE_TYPE_PUSH_CONSTANTS_INFO}; info.layout=k.layout;
            info.stageFlags=VK_SHADER_STAGE_COMPUTE_BIT; info.size=20; info.pValues=push.data();
            f->vk->vkCmdPushConstants2(cmd,&info);
            int w=f->vi.width/(i==1?2:1), h=f->vi.height/(i==2?1:2);
            f->vk->vkCmdDispatch(cmd,(w+7)/8,(h+7)/8,1);
            if(i<2) {
                VkMemoryBarrier2 barrier{VK_STRUCTURE_TYPE_MEMORY_BARRIER_2};
                barrier.srcStageMask=VK_PIPELINE_STAGE_2_COMPUTE_SHADER_BIT; barrier.srcAccessMask=VK_ACCESS_2_SHADER_STORAGE_WRITE_BIT;
                barrier.dstStageMask=VK_PIPELINE_STAGE_2_COMPUTE_SHADER_BIT; barrier.dstAccessMask=VK_ACCESS_2_SHADER_STORAGE_READ_BIT;
                VkDependencyInfo dep{VK_STRUCTURE_TYPE_DEPENDENCY_INFO};dep.memoryBarrierCount=1;dep.pMemoryBarriers=&barrier;
                f->vk->vkCmdPipelineBarrier2(cmd,&dep);
            }
        }
        auto* recording=record.context; record.context=nullptr;
        if(f->gpu->gpuExecSubmit(recording,nullptr,err,errSize)) fail(err);
        return dst.release();
    } catch(const std::exception& e) {api->setFilterError(e.what(),ctx);return nullptr;}
}
void VS_CC freeFilter(void* data,VSCore*,const VSAPI*) {delete static_cast<Filter*>(data);}
void VS_CC createGPU(const VSMap* in,VSMap* out,void*,VSCore* core,const VSAPI* api) {
    try {
        auto f=std::make_unique<Filter>();f->api=api;f->gpu=api->getVulkanAPI();
        f->node=api->mapGetNode(in,"clip",0,nullptr); f->vi=*api->getVideoInfo(f->node);
        f->inputFormat=f->vi.format;
        int mapError{};f->debug=int(api->mapGetInt(in,"debug",0,&mapError));
        if(mapError) f->debug=0;
        if(f->debug<0 || f->debug>7) fail("debug must be in 0..7");
        const auto& fmt=f->vi.format;
        if(fmt.colorFamily!=cfYUV || fmt.subSamplingW!=1 || fmt.subSamplingH!=1 || f->vi.width%2 || f->vi.height%2)
            fail("expected constant-format even-sized YUV420");
        if((fmt.sampleType==stInteger && (fmt.bitsPerSample<8 || fmt.bitsPerSample>16)) ||
           (fmt.sampleType==stFloat && fmt.bitsPerSample!=16 && fmt.bitsPerSample!=32)) fail("unsupported pixel type");
        char err[errSize]{};
        if(f->gpu->getVulkanHandles(core,&f->handles,err,errSize)) fail(err);
        f->vk=f->gpu->getVulkanFunctions(core,err,errSize);if(!f->vk) fail(err);
        VkPhysicalDeviceProperties2 properties{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PROPERTIES_2};
        f->vk->vkGetPhysicalDeviceProperties2(f->handles.physicalDevice,&properties);
        const auto& limits=properties.properties.limits;
        if(limits.maxComputeWorkGroupInvocations<64 || limits.maxComputeWorkGroupSize[0]<8 || limits.maxComputeWorkGroupSize[1]<8 ||
           (uint64_t(f->vi.width)+7)/8>limits.maxComputeWorkGroupCount[0] ||
           (uint64_t(f->vi.height)+7)/8>limits.maxComputeWorkGroupCount[1] ||
           uint64_t(f->vi.width)*f->vi.height*4>limits.maxStorageBufferRange)
            fail("dimensions or scratch storage exceed the device's compute limits");
        f->pool=f->gpu->createGPUExecPool(core,vqCompute,err,errSize);if(!f->pool) fail(err);
        f->kernel(f->kernels[0],"vertical",shader::vertical,core);f->kernel(f->kernels[1],"horizontal",shader::horizontal,core);
        f->kernel(f->kernels[2],"reconstruct",shader::reconstruct,core);
        api->queryVideoFormat(&f->vi.format,cfYUV,f->debug ? stFloat : fmt.sampleType,f->debug ? 32 : fmt.bitsPerSample,0,0,core);
        VSFilterDependency dependency{f->node,rpStrictSpatial};
        VSNode* result=api->createVideoFilterEx2("KrigBilateralGPU",&f->vi,getFrame,freeFilter,fmParallel,
            ffGPUOutput,&dependency,1,f.get(),core);
        if(!result) fail("filter creation failed");
        f.release();api->mapConsumeNode(out,"clip",result,maReplace);
    }catch(const std::exception& e){api->mapSetError(out,e.what());}
}
// CPU entry builds the transfer graph once; callers with a resident GPU chain use the native entry.
void VS_CC createCPU(const VSMap* in,VSMap* out,void*,VSCore* core,const VSAPI* api) {
    auto invoke=[&](VSPlugin* plugin,const char* name,VSNode* node)->VSNode* {
        VSMap* args=api->createMap(); api->mapSetNode(args,"clip",node,maReplace);
        if(plugin==api->getPluginByID("com.ryougikukoc.krigbilateralvk",core)) {
            int error{};int64_t debug=api->mapGetInt(in,"debug",0,&error);
            if(!error) api->mapSetInt(args,"debug",debug,maReplace);

        }
        VSMap* result=api->invoke(plugin,name,args);api->freeMap(args);
        const char* error=api->mapGetError(result);
        if(error){std::string message=error;api->freeMap(result);fail(message);}
        VSNode* r=api->mapGetNode(result,"clip",0,nullptr);api->freeMap(result);return r;
    };
    VSNode* node=nullptr;
    try {
        node=api->mapGetNode(in,"clip",0,nullptr);
        if(api->getNodeResidency(node)!=nrCPU) fail("CPU entry expects a CPU clip; use KrigBilateralGPU for resident input");
        auto* stdPlugin=api->getPluginByID("com.vapoursynth.std",core);
        auto* self=api->getPluginByID("com.ryougikukoc.krigbilateralvk",core);
        for(auto [plugin,name]:std::array<std::pair<VSPlugin*,const char*>,3>{{{stdPlugin,"GPUUpload"},{self,"KrigBilateralGPU"},{stdPlugin,"GPUDownload"}}}) {
            VSNode* next=invoke(plugin,name,node); api->freeNode(node);node=next;
        }
        api->mapConsumeNode(out,"clip",node,maReplace);node=nullptr;
    } catch(const std::exception& e){if(node)api->freeNode(node);api->mapSetError(out,e.what());}
}
}
VS_EXTERNAL_API(void) VapourSynthPluginInit2(VSPlugin* plugin,const VSPLUGINAPI* api) {
    api->configPlugin("com.ryougikukoc.krigbilateralvk","krigvk","KrigBilateral Vulkan",VS_MAKE_VERSION(0,2),VAPOURSYNTH_API_VERSION,0,plugin);
    api->registerFunction("KrigBilateralGPU","clip:vnode:gpu;debug:int:opt;","clip:vnode:gpu;",createGPU,nullptr,plugin);
    api->registerFunction("KrigBilateral","clip:vnode;debug:int:opt;","clip:vnode;",createCPU,nullptr,plugin);
}
