"""Execute Shiandow's original hook bodies with a buffer-based test harness.

Only the mpv texture/position macros and output-frame transport are adapted.
The moments, covariance expressions, packed matrix and solver stay unchanged.
Intermediate storage is FP32: the original GLSL does not prescribe FBO formats.
"""
from pathlib import Path
import argparse
import os
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]

TRANSPORT = r'''
#extension GL_EXT_shader_8bit_storage : require
#extension GL_EXT_shader_16bit_storage : require
#extension GL_EXT_shader_explicit_arithmetic_types_int8 : require
#extension GL_EXT_shader_explicit_arithmetic_types_int16 : require
#extension GL_EXT_shader_explicit_arithmetic_types_float16 : require
layout(local_size_x=8,local_size_y=8) in;
layout(push_constant) uniform Params { int sy; int su; int sv; int du; int dv; } p;
layout(binding=0,std430) readonly buffer SrcY { SAMPLE yy[]; };
layout(binding=1,std430) readonly buffer SrcU { SAMPLE uu[]; };
layout(binding=2,std430) readonly buffer SrcV { SAMPLE vv[]; };
layout(binding=3,std430) buffer Vertical { vec2 vert[]; };
layout(binding=4,std430) buffer Guide { vec2 guide[]; };
layout(binding=5,std430) writeonly buffer DstU { OUTPUT outU[]; };
layout(binding=6,std430) writeonly buffer DstV { OUTPUT outV[]; };
#if DEBUG_STAGE
layout(binding=7,std430) writeonly buffer DstY { float outY[]; };
#endif
ivec2 q;
float norm(SAMPLE value) { return float(value)/MAXVAL; }
float luma(ivec2 c) { c=clamp(c,ivec2(0),ivec2(WIDTH-1,HEIGHT-1)); return norm(yy[c.y*p.sy+c.x]); }
vec2 verticalAt(ivec2 c) { c=clamp(c,ivec2(0),ivec2(WIDTH-1,HEIGHT/2-1));return vert[c.y*WIDTH+c.x]; }
vec2 guideAt(ivec2 c) { c=clamp(c,ivec2(0),ivec2(WIDTH/2-1,HEIGHT/2-1));return guide[c.y*(WIDTH/2)+c.x]; }
vec2 chromaAt(ivec2 c) { c=clamp(c,ivec2(0),ivec2(WIDTH/2-1,HEIGHT/2-1));return vec2(norm(uu[c.y*p.su+c.x]),norm(vv[c.y*p.sv+c.x])); }
OUTPUT storeValue(float value) {
#if DEBUG_STAGE
    return value;
#elif INTEGER
    return OUTPUT(roundEven(clamp(value,0.0,1.0)*MAXVAL));
#else
    return OUTPUT(clamp(value,0.0,1.0));
#endif
}
'''


def hook_sections():
    source = (ROOT / 'reference/KrigBilateral.glsl').read_text(encoding='utf-8')
    sections = source.split('//!DESC ')[1:]
    assert len(sections) == 3
    return [section.split('\n', 1)[1].split('//!HOOK')[0] for section in sections]


def shaders():
    vertical, horizontal, reconstruction = hook_sections()
    vertical_macros = r'''
#define LUMA_size vec2(WIDTH,HEIGHT)
#define LUMA_pt (1.0/LUMA_size)
#define LUMA_pos ((vec2(q)+0.5)/vec2(WIDTH,HEIGHT/2))
#define CHROMA_size vec2(WIDTH/2,HEIGHT/2)
#define CHROMA_pt (1.0/CHROMA_size)
#define LUMA_mul 1.0
#define textureLod(tex,position,lod) vec4(luma(ivec2(q.x,int(k))))
'''
    horizontal_macros = r'''
#define LOWRES_Y_size vec2(WIDTH,HEIGHT/2)
#define LOWRES_Y_pt (1.0/LOWRES_Y_size)
#define LOWRES_Y_pos ((vec2(q)+0.5)/vec2(WIDTH/2,HEIGHT/2))
#define CHROMA_size vec2(WIDTH/2,HEIGHT/2)
#define CHROMA_pt (1.0/CHROMA_size)
#define LOWRES_Y_mul 1.0
#define textureLod(tex,position,lod) vec4(verticalAt(ivec2(int(k),q.y)).x)
#define LOWRES_Y_texOff(offset) vec4((verticalAt(ivec2(q.x*2,q.y))+verticalAt(ivec2(q.x*2+1,q.y)))*0.5,0.0,0.0)
'''
    reconstruction_macros = r'''
#define LOWRES_Y_pt vec2(1.0/float(WIDTH/2),1.0/float(HEIGHT/2))
#define CHROMA_pt LOWRES_Y_pt
#define LOWRES_Y_tex(position) vec4(guideAt(ivec2(floor((position)*vec2(WIDTH/2,HEIGHT/2)))),0.0,0.0)
#define CHROMA_tex(position) vec4(chromaAt(ivec2(floor((position)*vec2(WIDTH/2,HEIGHT/2)))),0.0,0.0)
#define LUMA_texOff(offset) vec4(luma(q))
'''
    # Fix common sample coordinates without reproducing renderer interpolation.
    reconstruction = reconstruction.replace(
        'CHROMA_pos * HOOKED_size - chromaOffset - vec2(0.5)',
        'vec2(float(q.x)*0.5,(float(q.y)+0.5)*0.5-0.5)')
    reconstruction = reconstruction.replace('float radius = 1.0;', '''float radius = 1.0;
#if DEBUG_STAGE == 3
    return vec4(localVar,y,0.0,0.0);
#elif DEBUG_STAGE == 4
    return vec4(C(0,1),c(0),0.0,0.0);
#elif DEBUG_STAGE == 6
    return vec4(X[0].xy,0.0,0.0);
#elif DEBUG_STAGE == 7
    return vec4(exp(-4.0*y),inversesqrt(1.0+y),0.0,0.0);
#endif''')
    footer = r'''
void main() {
    q=ivec2(gl_GlobalInvocationID.xy);
    if(q.x>=WIDTH || q.y>=HEIGHT) return;
    vec2 value;
#if DEBUG_STAGE == 1
    value=verticalAt(ivec2(q.x,q.y/2));
#elif DEBUG_STAGE == 2
    value=guideAt(q/2);
#elif DEBUG_STAGE == 3 || DEBUG_STAGE == 4 || DEBUG_STAGE == 6 || DEBUG_STAGE == 7
    value=hook().xy;
#else
    value=hook().xy;
#endif
    outU[q.y*p.du+q.x]=storeValue(value.x);
    outV[q.y*p.dv+q.x]=storeValue(value.y);
#if DEBUG_STAGE
    outY[q.y*p.du+q.x]=luma(q);
#endif
}
'''
    return {
        'vertical': TRANSPORT + vertical_macros + vertical + '''
void main(){q=ivec2(gl_GlobalInvocationID.xy);if(q.x>=WIDTH||q.y>=HEIGHT/2)return;vert[q.y*WIDTH+q.x]=hook().xy;}
''',
        'horizontal': TRANSPORT + horizontal_macros + horizontal + '''
void main(){q=ivec2(gl_GlobalInvocationID.xy);if(q.x>=WIDTH/2||q.y>=HEIGHT/2)return;guide[q.y*(WIDTH/2)+q.x]=hook().xy;}
''',
        'reconstruct': TRANSPORT + reconstruction_macros + reconstruction + footer,
    }


def build_oracle(cxx):
    folder = ROOT / 'build/oracle'
    folder.mkdir(parents=True, exist_ok=True)
    header = '#pragma once\nnamespace shader {\n'
    for name, source in shaders().items():
        header += f'inline constexpr const char* {name}=R"KRIG({source})KRIG";\n'
    (folder / 'oracle_shaders.h').write_text(header + '}\n', encoding='utf-8')
    host = (ROOT / 'src/krigbilateralvk.cpp').read_text(encoding='utf-8')
    host = host.replace('"shaders.h"', '"oracle_shaders.h"')
    host = host.replace('com.ryougikukoc.krigbilateralvk', 'com.ryougikukoc.krigbilateralvk.oracle')
    host = host.replace('"krigvk"', '"krigvk_oracle"')
    (folder / 'oracle.cpp').write_text(host, encoding='utf-8')
    import vapoursynth as vs
    suffix = '.dll' if os.name == 'nt' else '.so'
    native_flags = ['-static'] if os.name == 'nt' else ['-fPIC']
    command = [cxx, '-std=c++20', '-O3', '-ffp-contract=off', *native_flags, '-shared', '-DVS_USE_API_43',
               '-I' + vs.get_include(), '-I' + str(ROOT / 'dependencies/vulkan-headers/include'),
               str(folder / 'oracle.cpp'), '-o', str(folder / ('krig_shader_oracle' + suffix))]
    subprocess.run(command, check=True)
    return folder / ('krig_shader_oracle' + suffix)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cxx', default=shutil.which('g++'))
    args = parser.parse_args()
    if not args.cxx:
        parser.error('Supply --cxx or put g++ on PATH')
    print(build_oracle(args.cxx))
