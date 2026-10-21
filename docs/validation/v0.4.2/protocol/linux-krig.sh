set -eu
cmake -S /src -B /out/build -G Ninja -DCMAKE_BUILD_TYPE=Release \
  -DVAPOURSYNTH_INCLUDE_DIR=/src/dependencies/vapoursynth/include \
  -DVULKAN_HEADERS=/src/dependencies/vulkan-headers/include
cmake --build /out/build --parallel 2
export KRIGVK_PLUGIN=/out/build/krigbilateralvk.so
mkdir -p /out/source
cp -r /src/tests /src/reference /out/source/
export KRIGVK_VULKAN_HEADERS=/src/dependencies/vulkan-headers/include
export KRIGVK_ORACLE=/out/source/build/oracle/krig_shader_oracle.so
python /out/source/tests/shader_reference.py --cxx /usr/bin/g++
python /out/source/tests/run_isolated.py /out/source/tests/validate_matrix.py --output /out/matrix.json
python /out/source/tests/run_isolated.py /out/source/tests/validate_fused_depth.py --output /out/depth.json
python /out/source/tests/run_isolated.py /out/source/tests/validate_contract.py --output /out/contract.json
