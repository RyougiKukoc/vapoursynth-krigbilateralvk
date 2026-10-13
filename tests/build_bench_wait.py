"""Build the test-only GPU completion sink used for resident throughput measurements."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import vapoursynth as vs

ROOT=Path(__file__).resolve().parents[1]

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cxx',default=shutil.which('g++'))
    args=parser.parse_args()
    if not args.cxx: parser.error('Supply --cxx or put g++ on PATH')
    output=ROOT/'build/bench'/('krig_bench_wait.dll' if os.name=='nt' else 'krig_bench_wait.so')
    output.parent.mkdir(parents=True,exist_ok=True)
    flags=['-static'] if os.name=='nt' else ['-fPIC']
    subprocess.run([args.cxx,'-std=c++20','-O2','-shared','-DVS_USE_API_43',*flags,
                    '-I'+vs.get_include(),'-I'+os.environ.get('KRIGVK_VULKAN_HEADERS',str(ROOT/'dependencies/vulkan-headers/include')),
                    str(ROOT/'tests/bench_wait.cpp'),'-o',str(output)],check=True)
    print(output)

if __name__=='__main__': main()
