// KrigBilateral Vulkan host, LGPL-3.0-or-later.
#include <VapourSynth4.h>
#include <VSVulkan4.h>
#include <array>
#include <cstdint>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <functional>
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
    int count=api->mapNumElements(properties,"_Range");
    if(count>=0) {
        int error{};
        int64_t range=api->mapGetInt(properties,"_Range",0,&error);
        if(count!=1 || error || (range!=0 && range!=1))
            fail("_Range must be one integer: 0 (limited) or 1 (full)");
    }
}

struct NodeDel { const VSAPI* api; void operator()(VSNode* node)const {if(node) api->freeNode(node);} };
using Node=std::unique_ptr<VSNode,NodeDel>;
struct MapDel { const VSAPI* api; void operator()(VSMap* map)const {if(map) api->freeMap(map);} };
using Map=std::unique_ptr<VSMap,MapDel>;

int64_t optionalInt(const VSMap* in,const char* key,int64_t fallback,const VSAPI* api) {
    int count=api->mapNumElements(in,key);
    if(count<0) return fallback;
    int error{};
    int64_t value=api->mapGetInt(in,key,0,&error);
    if(count!=1 || error) fail(std::string(key)+" must be one integer");
    return value;
}

struct OutputOptions {
    VSVideoFormat format{};
};
OutputOptions outputOptions(const VSMap* in,const VSVideoFormat& input,VSCore* core,const VSAPI* api) {
    OutputOptions options;
    int count=api->mapNumElements(in,"dither_type");
    if(count>=0) {
        int error{};
        const char* value=api->mapGetData(in,"dither_type",0,&error);
        if(count!=1 || error) fail("dither_type must be one string");
        std::string dither(value,api->mapGetDataSize(in,"dither_type",0,nullptr));
        if(dither!="none") fail("fused output does not implement dithering; dither_type must be none; use core.resize after 16-bit or FP32 output for dithering");
    }
    int64_t bits=optionalInt(in,"bits",input.sampleType==stInteger ? 16 : input.bitsPerSample,api);
    int64_t defaultType=bits==32 ? stFloat : (bits<16 ? stInteger : input.sampleType);
    int64_t type=optionalInt(in,"sample_type",defaultType,api);
    if((type!=stInteger && type!=stFloat) ||
       (type==stInteger && (bits<8 || bits>16)) ||
       (type==stFloat && bits!=16 && bits!=32))
        fail("output requires integer 8..16-bit or float 16/32-bit; set bits and sample_type accordingly");
    if(!api->queryVideoFormat(&options.format,cfYUV,static_cast<int>(type),static_cast<int>(bits),0,0,core))
        fail("could not construct the requested YUV444 output format");
    if(type==stInteger && bits<16)
        api->logMessage(mtWarning,"KrigBilateral: integer output below 16 bits uses no dithering and may cause banding; use 16-bit or FP32 output followed by core.resize with dithering.",core);
    return options;
}

struct Filter {
    const VSAPI* api{};
    const VSVULKANAPI* gpu{};
    const VSVulkanFunctions* vk{};
    VSVulkanCoreHandles handles{};
    VSNode* node{};
    VSVideoInfo vi{};
    VSVideoFormat inputFormat{};
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
        VkPushConstantRange range{VK_SHADER_STAGE_COMPUTE_BIT,0,28};
        VkPipelineLayoutCreateInfo layout{VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO};
        layout.setLayoutCount=1; layout.pSetLayouts=&k.set; layout.pushConstantRangeCount=1; layout.pPushConstantRanges=&range;
        check(vk->vkCreatePipelineLayout(handles.device,&layout,nullptr,&k.layout),"pipeline layout");
        auto type=[](const VSVideoFormat& fmt) {
            return fmt.sampleType==stFloat ? (fmt.bitsPerSample==16 ? "float16_t" : "float") :
                (fmt.bitsPerSample==8 ? "uint8_t" : "uint16_t");
        };
        std::string text="#version 460\n"
            "#extension GL_EXT_shader_8bit_storage : require\n"
            "#extension GL_EXT_shader_16bit_storage : require\n"
            "#extension GL_EXT_shader_explicit_arithmetic_types_int8 : require\n"
            "#extension GL_EXT_shader_explicit_arithmetic_types_int16 : require\n";
        if((inputFormat.sampleType==stFloat && inputFormat.bitsPerSample==16) ||
           (vi.format.sampleType==stFloat && vi.format.bitsPerSample==16))
            text+="#extension GL_EXT_shader_explicit_arithmetic_types_float16 : require\n";
        text+="#define WIDTH "+std::to_string(vi.width)+"\n#define HEIGHT "+std::to_string(vi.height)+
            "\n#define SRC_T "+type(inputFormat)+"\n#define DST_T "+type(vi.format)+
            "\n#define SRC_INT "+std::to_string(inputFormat.sampleType==stInteger)+
            "\n#define DST_INT "+std::to_string(vi.format.sampleType==stInteger)+
            "\n#define SRC_BITS "+std::to_string(inputFormat.bitsPerSample)+
            "\n#define DST_BITS "+std::to_string(vi.format.bitsPerSample)+
            "\n#define WRITE_Y "+std::to_string(inputFormat.sampleType!=vi.format.sampleType || inputFormat.bitsPerSample!=vi.format.bitsPerSample)+"\n"+source;
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
        const VSMap* inputProperties=api->getFramePropertiesRO(src.get());
        int rangeError{};
        int64_t full=api->mapGetInt(inputProperties,"_Range",0,&rangeError);
        if(rangeError) full=0; // untagged YUV uses resize's limited-range default
        bool writeY=f->inputFormat.sampleType!=f->vi.format.sampleType || f->inputFormat.bitsPerSample!=f->vi.format.bitsPerSample;
        const VSFrame* planes[3]={writeY ? nullptr : src.get(),nullptr,nullptr}; int indices[3]={0,1,2};
        VSFrame* raw=writeY ? f->gpu->newGPUVideoFrame(&f->vi.format,f->vi.width,f->vi.height,src.get(),core) :
            api->newVideoFrame2(&f->vi.format,f->vi.width,f->vi.height,planes,indices,src.get(),core);
        if(!raw) fail("output allocation failed");
        Frame dst{raw,{api}};
        VSMap* properties=api->getFramePropertiesRW(raw);
        api->mapDeleteKey(properties,"_ChromaLocation"); // full-resolution chroma has no subsampling siting
        api->mapSetInt(properties,"_Range",full,maReplace);
        char err[errSize]{};
        Recording record{f->gpu,f->gpu->gpuExecAcquire(f->pool,err,errSize)};
        if(!record.context) fail(err);
        f->gpu->gpuExecReadsFrame(record.context,src.get());
        f->gpu->gpuExecWritesPlane(record.context,raw,1);
        f->gpu->gpuExecWritesPlane(record.context,raw,2);
        if(writeY) f->gpu->gpuExecWritesPlane(record.context,raw,0);
        std::array<VkDescriptorBufferInfo,8> buffers{};
        std::array<int32_t,7> push{};
        push[6]=static_cast<int32_t>(full);
        for(int plane=0;plane<3;++plane) {
            auto info=checkedPlane(*f,src.get(),plane,f->vi.width/(plane ? 2 : 1),f->vi.height/(plane ? 2 : 1),
                f->inputFormat.bytesPerSample,push[plane]);
            buffers[plane]={info.buffer,0,info.bufferSize};
        }
        for(int plane=writeY ? 0 : 1;plane<3;++plane) {
            auto info=checkedPlane(*f,dst.get(),plane,f->vi.width,f->vi.height,f->vi.format.bytesPerSample,push[3+plane]);
            buffers[plane==0 ? 7 : 4+plane]={info.buffer,0,info.bufferSize};
        }
        if(!writeY) buffers[7]=buffers[5]; // unused binding has a valid writable buffer
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
            info.stageFlags=VK_SHADER_STAGE_COMPUTE_BIT; info.size=sizeof(push); info.pValues=push.data();
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
Node createNative(VSNode* input,const VSVideoFormat& outputFormat,VSCore* core,const VSAPI* api) {
        auto f=std::make_unique<Filter>();f->api=api;f->gpu=api->getVulkanAPI();
        f->node=api->addNodeRef(input); f->vi=*api->getVideoInfo(f->node);
        validateInput(f->vi);
        if(api->getNodeResidency(f->node)!=nrGPU) fail("GPU entry requires GPU-resident input");
        f->inputFormat=f->vi.format;
        f->vi.format=outputFormat;
        char err[errSize]{};
        if(f->gpu->getVulkanHandles(core,&f->handles,err,errSize)) fail(err);
        f->vk=f->gpu->getVulkanFunctions(core,err,errSize);if(!f->vk) fail(err);
        VkPhysicalDeviceProperties2 properties{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PROPERTIES_2};
        f->vk->vkGetPhysicalDeviceProperties2(f->handles.physicalDevice,&properties);
        const auto& limits=properties.properties.limits;
        f->maxStorageRange=limits.maxStorageBufferRange;
        if(limits.maxComputeWorkGroupInvocations<64 || limits.maxComputeWorkGroupSize[0]<8 || limits.maxComputeWorkGroupSize[1]<8 ||
           limits.maxPerStageDescriptorStorageBuffers<8 || limits.maxDescriptorSetStorageBuffers<8 || limits.maxPushConstantsSize<28 ||
           (uint64_t(f->vi.width)+7)/8>limits.maxComputeWorkGroupCount[0] ||
           (uint64_t(f->vi.height)+7)/8>limits.maxComputeWorkGroupCount[1] ||
           uint64_t(f->vi.width)*f->vi.height*4>limits.maxStorageBufferRange)
            fail("dimensions or scratch storage exceed the device's compute limits");
        f->pool=f->gpu->createGPUExecPool(core,vqCompute,err,errSize);if(!f->pool) fail(err);
        f->kernel(f->kernels[0],"vertical",shader::vertical,core);f->kernel(f->kernels[1],"horizontal",shader::horizontal,core);
        f->kernel(f->kernels[2],"reconstruct",shader::reconstruct,core);
        VSFilterDependency dependency{f->node,rpStrictSpatial};
        VSNode* result=api->createVideoFilterEx2("KrigBilateralGPU",&f->vi,getFrame,freeFilter,fmParallel,
            ffGPUOutput,&dependency,1,f.get(),core);
        if(!result) fail("filter creation failed");
        f.release();return Node{result,{api}};
}

struct Graph {
    VSCore* core;
    const VSAPI* api;
    Node call(const char* id,const char* name,const std::function<void(VSMap*)>& fill)const {
        auto* plugin=api->getPluginByID(id,core);
        if(!plugin) fail(std::string("required core plugin is unavailable: ")+id);
        Map args{api->createMap(),{api}};
        fill(args.get());
        Map result{api->invoke(plugin,name,args.get()),{api}};
        if(const char* error=api->mapGetError(result.get())) fail(error);
        int error{};
        VSNode* node=api->mapGetNode(result.get(),"clip",0,&error);
        if(error || !node) fail(std::string("core filter returned no clip: ")+name);
        return Node{node,{api}};
    }
    Node transfer(VSNode* clip,bool gpu)const {
        if((api->getNodeResidency(clip)==nrGPU)==gpu) return Node{api->addNodeRef(clip),{api}};
        return call("com.vapoursynth.std",gpu ? "GPUUpload" : "GPUDownload",[&](VSMap* args){
            api->mapSetNode(args,"clip",clip,maReplace);
        });
    }

};

void VS_CC create(const VSMap* in,VSMap* out,void* userData,VSCore* core,const VSAPI* api) {
    try {
        bool gpu=userData!=nullptr;
        Node original{api->mapGetNode(in,"clip",0,nullptr),{api}};
        const auto& vi=*api->getVideoInfo(original.get());
        validateInput(vi);
        OutputOptions options=outputOptions(in,vi.format,core,api);
        Graph graph{core,api};
        Node uploaded=graph.transfer(original.get(),true);
        Node filtered=createNative(uploaded.get(),options.format,core,api);
        Node result=graph.transfer(filtered.get(),gpu);
        api->mapConsumeNode(out,"clip",result.release(),maReplace);
    } catch(const std::exception& e){api->mapSetError(out,e.what());}
}
}
VS_EXTERNAL_API(void) VapourSynthPluginInit2(VSPlugin* plugin,const VSPLUGINAPI* api) {
    api->configPlugin("com.ryougikukoc.krigbilateralvk","krigvk","KrigBilateral Vulkan",VS_MAKE_VERSION(0,4),VAPOURSYNTH_API_VERSION,0,plugin);
    api->registerFunction("KrigBilateralGPU","clip:vnode:gpu;bits:int:opt;sample_type:int:opt;dither_type:data:opt;","clip:vnode:gpu;",create,reinterpret_cast<void*>(1),plugin);
    api->registerFunction("KrigBilateral","clip:vnode;bits:int:opt;sample_type:int:opt;dither_type:data:opt;","clip:vnode;",create,nullptr,plugin);
}
