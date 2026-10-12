cmake_minimum_required(VERSION 3.20)
project(krigbilateralvk VERSION 0.1.0 LANGUAGES CXX)

set(VAPOURSYNTH_INCLUDE_DIR "" CACHE PATH "VapourSynth R81 include directory")
if(NOT VAPOURSYNTH_INCLUDE_DIR)
    find_package(Python3 REQUIRED COMPONENTS Interpreter)
    execute_process(
        COMMAND "${Python3_EXECUTABLE}" -c "import vapoursynth as vs; print(vs.get_include())"
        RESULT_VARIABLE VS_INCLUDE_RESULT
        OUTPUT_VARIABLE VS_INCLUDE_PATH OUTPUT_STRIP_TRAILING_WHITESPACE
    )
    if(NOT VS_INCLUDE_RESULT EQUAL 0)
        message(FATAL_ERROR "Install VapourSynth R81 in the selected Python, or set -DVAPOURSYNTH_INCLUDE_DIR=/path/to/include")
    endif()
    set(VAPOURSYNTH_INCLUDE_DIR "${VS_INCLUDE_PATH}" CACHE PATH "VapourSynth R81 include directory" FORCE)
endif()
foreach(VS_HEADER VapourSynth4.h VSVulkan4.h)
    if(NOT EXISTS "${VAPOURSYNTH_INCLUDE_DIR}/${VS_HEADER}")
        message(FATAL_ERROR "${VS_HEADER} is missing from VAPOURSYNTH_INCLUDE_DIR; use the R81 GPU SDK")
    endif()
endforeach()

set(VULKAN_HEADERS "${CMAKE_CURRENT_SOURCE_DIR}/dependencies/vulkan-headers/include" CACHE PATH "Vulkan-Headers include directory")
if(NOT EXISTS "${VULKAN_HEADERS}/vulkan/vulkan_core.h")
    message(FATAL_ERROR "Run python tools/fetch_dependencies.py, or set -DVULKAN_HEADERS=/path/to/include")
endif()

add_library(krigbilateralvk MODULE src/krigbilateralvk.cpp)
target_compile_features(krigbilateralvk PRIVATE cxx_std_20)
target_compile_definitions(krigbilateralvk PRIVATE VS_USE_API_43)
target_include_directories(krigbilateralvk PRIVATE "${VAPOURSYNTH_INCLUDE_DIR}" "${VULKAN_HEADERS}")
set_target_properties(krigbilateralvk PROPERTIES PREFIX "" CXX_VISIBILITY_PRESET hidden VISIBILITY_INLINES_HIDDEN YES)
if(MSVC)
    target_compile_options(krigbilateralvk PRIVATE /O2 /fp:strict)
else()
    target_compile_options(krigbilateralvk PRIVATE -O3 -ffp-contract=off)
    if(MINGW)
        target_link_options(krigbilateralvk PRIVATE -static)
    endif()
endif()

install(TARGETS krigbilateralvk LIBRARY DESTINATION lib/vapoursynth RUNTIME DESTINATION lib/vapoursynth)
