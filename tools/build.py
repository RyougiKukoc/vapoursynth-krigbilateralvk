"""Build the Windows API4.3 DLL with MinGW; use CMake for other toolchains."""
import argparse
from pathlib import Path
import subprocess
import sys
import shutil
import vapoursynth as vs

ROOT=Path(__file__).resolve().parents[1]
ap=argparse.ArgumentParser()
ap.add_argument('--cxx',default=shutil.which('g++'))
ap.add_argument('--headers',type=Path,default=ROOT/'dependencies/vulkan-headers/include')
args=ap.parse_args()
if not args.cxx:
    ap.error('g++ is not on PATH; supply --cxx /path/to/g++.exe')
if sys.platform != 'win32':
    ap.error('Use the CMake build on non-Windows platforms')
if not (args.headers/'vulkan/vulkan_core.h').is_file():
    ap.error('Supply --headers to a Vulkan-Headers include directory or run fetch_dependencies.py')
subprocess.run([sys.executable,str(ROOT/'tools/generate_shaders.py')],check=True)
(ROOT/'build').mkdir(exist_ok=True)
command=[args.cxx,'-std=c++20','-O3','-ffp-contract=off','-shared','-static','-DVS_USE_API_43',
         '-I'+vs.get_include(),'-I'+str(args.headers),str(ROOT/'src/krigbilateralvk.cpp'),
         '-o',str(ROOT/'build/krigbilateralvk.dll')]
subprocess.run(command,check=True)
print(ROOT/'build/krigbilateralvk.dll')
