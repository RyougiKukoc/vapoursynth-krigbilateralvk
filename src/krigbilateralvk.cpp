// KrigBilateral Vulkan host, LGPL-3.0-or-later.
#include <VapourSynth4.h>
#include <VSVulkan4.h>
#include <array>
#include <cstdint>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <limits>
#include <memory>
#include <stdexcept>
#include <string>
#include "shaders.h"

namespace {
constexpr int errSize=8192;
[[noreturn]] void fail(const std::string& s) { throw std::runtime_error("KrigBilateral: "+s); }
void check(VkResult r, const char* operation) { if(r!=VK_SUCCESS) fail(std::string(operation)+" failed ("+std::to_string(r)+")"); }
struct Kernel { VkDescriptorSetLayout set{}; VkPipelineLayout layout{}; VkPipeline pipeline{}; };

void validateInput(const VSVideoInfo& vi) {
    const auto& format=vi.format;
    if(format.colorFamily!=cfYUV || format.numPlanes!=3 || format.subSamplingW!=1 || format.subSamplingH!=1)
        fail("expected constant-format, three-plane YUV420 input");
    if(vi.width<=0 || vi.height<=0 || vi.width%2 || vi.height%2)
        fail("expected fixed, positive, even width and height");
    const bool integer=format.sampleType==stInteger && format.bitsPerSample>=8 && format.bitsPerSample<=16 &&
        format.bytesPerSample==(format.bitsPerSample==8 ? 1 : 2);
    const bool floating=format.sampleType==stFloat && (format.bitsPerSample==16 || format.bitsPerSample==32) &&
        format.bytesPerSample==format.bitsPerSample/8;
    if(!integer && !floating)
        fail("supported sample types are integer 8..16-bit, FP16 and FP32");
    if(uint64_t(vi.width)*vi.height>uint64_t(std::numeric_limits<int32_t>::max()))
        fail("dimensions exceed the shader's signed 32-bit indexing limit");
}

int parseDebug(const VSMap* in,const VSAPI* api) {
    int count=api->mapNumElements(in,"debug");
    if(count<0) return 0;
    if(count!=1) fail("debug must be one integer in 0..7");
    int error{};
    int64_t value=api->mapGetInt(in,"debug",0,&error);
    if(error || value<0 || value>7) fail("debug must be one integer in 0..7");
    return static_cast<int>(value);
}

void validateFrameProperties(const VSFrame* frame,const VSAPI* api) {
    const VSMap* properties=api->getFramePropertiesRO(frame);
    for(const char* key:{"_ChromaLocation","_FieldBased"}) {
        int count=api->mapNumElements(properties,key);
        if(count<0) continue; // untagged input assumes left placement and progressive frames
        int error{};
        int64_t value=api->mapGetInt(properties,key,0,&error);
        if(count!=1 || error) fail(std::string(key)+" must be one integer");
        if(value!=0) {
            if(std::string(key)=="_ChromaLocation")
                fail("only left chroma placement (_ChromaLocation=0) is supported; resite chroma before filtering");
            fail("only progressive frames (_FieldBased=0) are supported; deinterlace or separate fields before filtering");
        }
    }
}

struct Filter {
    const VSAPI* api{};
    const VSVULKANAPI* gpu{};
    const VSVulkanFunctions* vk{};
    VSVulkanCoreHandles handles{};
    VSNode* node{};
    VSVideoInfo vi{};
    VSVideoFormat inputFormat{};
    int debug{};
    VkDeviceSize maxStorageRange{};
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

VSVulkanPlaneInfo checkedPlane(const Filter& filter,const VSFrame* frame,int plane,int width,int height,int bytes,int32_t& stride) {
    if(filter.api->getFrameWidth(frame,plane)!=width || filter.api->getFrameHeight(frame,plane)!=height)
        fail("frame plane dimensions do not match the declared video format");
    int64_t byteStride=filter.api->getStride(frame,plane);
    if(byteStride<int64_t(width)*bytes || byteStride%bytes ||
       uint64_t(byteStride/bytes)*height>uint64_t(std::numeric_limits<int32_t>::max()))
        fail("frame stride exceeds supported shader addressing");
    VSVulkanPlaneInfo info{};
    if(filter.gpu->getGPUPlane(frame,plane,&info)) fail("frame must be GPU resident");
    VkDeviceSize required=VkDeviceSize(byteStride)*height;
    if(info.bufferSize<required || info.bufferSize>filter.maxStorageRange)
        fail("frame plane buffer is too small or exceeds the device storage-buffer range");
    stride=static_cast<int32_t>(byteStride/bytes);
    return info;
}
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
        if(!src) fail("failed to obtain input frame");
        const auto& actual=*api->getVideoFrameFormat(src.get());
        const auto& declared=f->inputFormat;
        if(actual.colorFamily!=declared.colorFamily || actual.sampleType!=declared.sampleType ||
           actual.bitsPerSample!=declared.bitsPerSample || actual.bytesPerSample!=declared.bytesPerSample ||
           actual.numPlanes!=declared.numPlanes || actual.subSamplingW!=declared.subSamplingW || actual.subSamplingH!=declared.subSamplingH)
            fail("frame format does not match the declared fixed input format");
        validateFrameProperties(src.get(),api);
        const VSFrame* planes[3]={src.get(),nullptr,nullptr}; int indices[3]={0,1,2};
        VSFrame* raw=f->debug ? f->gpu->newGPUVideoFrame(&f->vi.format,f->vi.width,f->vi.height,src.get(),core) :
            api->newVideoFrame2(&f->vi.format,f->vi.width,f->vi.height,planes,indices,src.get(),core);
        if(!raw) fail("output allocation failed");
        Frame dst{raw,{api}};
        VSMap* properties=api->getFramePropertiesRW(raw);
        api->mapDeleteKey(properties,"_ChromaLocation"); // full-resolution chroma has no subsampling siting
        if(f->debug) {
            for(const char* key:{"_Matrix","_Primaries","_Transfer","_Range","_ColorRange"})
                api->mapDeleteKey(properties,key);
            api->mapSetInt(properties,"KrigBilateralDebug",f->debug,maReplace);
        }
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
            auto info=checkedPlane(*f,src.get(),plane,f->vi.width/(plane ? 2 : 1),f->vi.height/(plane ? 2 : 1),
                f->inputFormat.bytesPerSample,push[plane]);
            buffers[plane]={info.buffer,0,info.bufferSize};
        }
        if(f->debug) {
            int32_t stride{};
            auto info=checkedPlane(*f,dst.get(),0,f->vi.width,f->vi.height,4,stride);
            if(api->getStride(dst.get(),0)!=api->getStride(dst.get(),1)) fail("diagnostic output planes must have equal strides");
            buffers[7]={info.buffer,0,info.bufferSize};
        } else buffers[7]=buffers[0];
        for(int plane=1;plane<3;++plane) {
            auto info=checkedPlane(*f,dst.get(),plane,f->vi.width,f->vi.height,f->vi.format.bytesPerSample,push[2+plane]);
            buffers[4+plane]={info.buffer,0,info.bufferSize};
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
        validateInput(f->vi);
        if(api->getNodeResidency(f->node)!=nrGPU) fail("GPU entry requires GPU-resident input");
        f->inputFormat=f->vi.format;
        f->debug=parseDebug(in,api);
        const auto& fmt=f->vi.format;
        char err[errSize]{};
        if(f->gpu->getVulkanHandles(core,&f->handles,err,errSize)) fail(err);
        f->vk=f->gpu->getVulkanFunctions(core,err,errSize);if(!f->vk) fail(err);
        VkPhysicalDeviceProperties2 properties{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PROPERTIES_2};
        f->vk->vkGetPhysicalDeviceProperties2(f->handles.physicalDevice,&properties);
        const auto& limits=properties.properties.limits;
        f->maxStorageRange=limits.maxStorageBufferRange;
        if(fmt.sampleType==stFloat && fmt.bitsPerSample==16) {
            VkPhysicalDeviceVulkan12Features features12{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_2_FEATURES};
            VkPhysicalDeviceFeatures2 features{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2};features.pNext=&features12;
            f->vk->vkGetPhysicalDeviceFeatures2(f->handles.physicalDevice,&features);
            if(!features12.shaderFloat16) fail("FP16 input requires the device's shaderFloat16 feature; use FP32 or integer input");
        }
        if(limits.maxComputeWorkGroupInvocations<64 || limits.maxComputeWorkGroupSize[0]<8 || limits.maxComputeWorkGroupSize[1]<8 ||
           limits.maxPerStageDescriptorStorageBuffers<8 || limits.maxDescriptorSetStorageBuffers<8 || limits.maxPushConstantsSize<20 ||
           (uint64_t(f->vi.width)+7)/8>limits.maxComputeWorkGroupCount[0] ||
           (uint64_t(f->vi.height)+7)/8>limits.maxComputeWorkGroupCount[1] ||
           uint64_t(f->vi.width)*f->vi.height*4>limits.maxStorageBufferRange)
            fail("dimensions or scratch storage exceed the device's compute limits");
        f->pool=f->gpu->createGPUExecPool(core,vqCompute,err,errSize);if(!f->pool) fail(err);
        f->kernel(f->kernels[0],"vertical",shader::vertical,core);f->kernel(f->kernels[1],"horizontal",shader::horizontal,core);
        f->kernel(f->kernels[2],"reconstruct",shader::reconstruct,core);
        if(!api->queryVideoFormat(&f->vi.format,cfYUV,f->debug ? stFloat : fmt.sampleType,f->debug ? 32 : fmt.bitsPerSample,0,0,core))
            fail("could not construct the requested YUV444 output format");
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
        validateInput(*api->getVideoInfo(node));
        parseDebug(in,api);
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
