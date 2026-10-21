"""Embed the original KrigBilateral hooks with Vulkan buffer transport."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

COMMON = r'''
layout(local_size_x=32,local_size_y=2) in;
layout(push_constant) uniform Params { int sy; int su; int sv; int dy; int du; int dv; int full; } p;
layout(binding=0,std430) readonly buffer SrcY { SRC_T yy[]; };
layout(binding=1,std430) readonly buffer SrcU { SRC_T uu[]; };
layout(binding=2,std430) readonly buffer SrcV { SRC_T vv[]; };
layout(binding=3,std430) buffer Vertical { vec2 vert[]; };
layout(binding=4,std430) buffer Guide { vec2 guide[]; };
layout(binding=5,std430) writeonly buffer DstU { DST_T outU[]; };
layout(binding=6,std430) writeonly buffer DstV { DST_T outV[]; };
layout(binding=7,std430) writeonly buffer DstY { DST_T outY[]; };
ivec2 q;
// Same sample numbering as resize/zimg. The flag is a per-frame value;
// depth and storage types are compile-time constants for each filter instance.
#define SRC_PEAK float((1 << SRC_BITS) - 1)
#define DST_PEAK float((1 << DST_BITS) - 1)
#define SRC_SHIFT (1 << (SRC_BITS - 8))
#define DST_SHIFT (1 << (DST_BITS - 8))
float normalizeY(float value) {
#if SRC_INT
    float scale=p.full!=0 ? 1.0/SRC_PEAK : 1.0/(219.0*SRC_SHIFT);
    float offset=p.full!=0 ? 0.0 : -16.0/219.0;
    return value*scale+offset;
#else
    return value;
#endif
}
float normalizeUV(float value) {
#if SRC_INT
    float scale=p.full!=0 ? 1.0/SRC_PEAK : 1.0/(224.0*SRC_SHIFT);
    float offset=p.full!=0 ? -float(1 << (SRC_BITS-1))/SRC_PEAK : -128.0/224.0;
    return value*scale+offset;
#else
    return value;
#endif
}
float luma(ivec2 c) { c=clamp(c,ivec2(0),ivec2(WIDTH-1,HEIGHT-1)); return normalizeY(float(yy[c.y*p.sy+c.x])); }
vec2 verticalAt(ivec2 c) { c=clamp(c,ivec2(0),ivec2(WIDTH-1,HEIGHT/2-1));return vert[c.y*WIDTH+c.x]; }
vec2 guideAt(ivec2 c) { c=clamp(c,ivec2(0),ivec2(WIDTH/2-1,HEIGHT/2-1));return guide[c.y*(WIDTH/2)+c.x]; }
vec2 chromaAt(ivec2 c) {
    c=clamp(c,ivec2(0),ivec2(WIDTH/2-1,HEIGHT/2-1));
    // Preserve the separate canonical-to-shader addition. Without this bound,
    // a driver may contract it into the range affine and change the last bit.
    // The affine itself retains the same contraction policy as core resize.
#if SRC_INT
    precise vec2 adapted=vec2(normalizeUV(float(uu[c.y*p.su+c.x])),normalizeUV(float(vv[c.y*p.sv+c.x])))+vec2(0.5);
#else
    vec2 adapted=vec2(float(uu[c.y*p.su+c.x]),float(vv[c.y*p.sv+c.x]))+vec2(0.5);
#endif
    return adapted;
}
DST_T storeValue(float value) {
    value-=0.5; // signed canonical chroma, with headroom intact
#if DST_INT
    float scale=p.full!=0 ? DST_PEAK : 224.0*DST_SHIFT;
    value=value*scale+float(1 << (DST_BITS-1));
    value=clamp(roundEven(value),0.0,DST_PEAK); // no dither
#endif
    return DST_T(value);
}
DST_T storeY(float value) {
#if SRC_INT && DST_INT
    // Direct depth affine avoids unnecessary FP32 normalization/quantization
    // and makes limited-range integer widening an exact left shift.
    float scale=p.full!=0 ? DST_PEAK/SRC_PEAK : float(DST_SHIFT)/float(SRC_SHIFT);
    value*=scale;
#else
    value=normalizeY(value);
#if DST_INT
    float scale=p.full!=0 ? DST_PEAK : 219.0*DST_SHIFT;
    float offset=p.full!=0 ? 0.0 : 16.0*DST_SHIFT;
    value=value*scale+offset;
#endif
#endif
#if DST_INT
    value=clamp(roundEven(value),0.0,DST_PEAK);
#endif
    return DST_T(value);
}
'''

VERTICAL_BINDINGS = r'''
#define LUMA_size vec2(WIDTH,HEIGHT)
#define LUMA_pt (1.0/LUMA_size)
#define LUMA_pos ((vec2(q)+0.5)/vec2(WIDTH,HEIGHT/2))
#define CHROMA_size vec2(WIDTH/2,HEIGHT/2)
#define CHROMA_pt (1.0/CHROMA_size)
#define LUMA_mul 1.0
#define textureLod(tex,position,lod) vec4(luma(ivec2(q.x,int(k))))
'''

HORIZONTAL_BINDINGS = r'''
#define LOWRES_Y_size vec2(WIDTH,HEIGHT/2)
#define LOWRES_Y_pt (1.0/LOWRES_Y_size)
#define LOWRES_Y_pos ((vec2(q)+0.5)/vec2(WIDTH/2,HEIGHT/2))
#define CHROMA_size vec2(WIDTH/2,HEIGHT/2)
#define CHROMA_pt (1.0/CHROMA_size)
#define LOWRES_Y_mul 1.0
#define textureLod(tex,position,lod) vec4(verticalAt(ivec2(int(k),q.y)).x)
#define LOWRES_Y_texOff(offset) vec4((verticalAt(ivec2(q.x*2,q.y))+verticalAt(ivec2(q.x*2+1,q.y)))*0.5,0.0,0.0)
'''

RECONSTRUCT_BINDINGS = r'''
#define LOWRES_Y_pt vec2(1.0/float(WIDTH/2),1.0/float(HEIGHT/2))
#define CHROMA_pt LOWRES_Y_pt
#define LOWRES_Y_tex(position) vec4(guideAt(ivec2(floor((position)*vec2(WIDTH/2,HEIGHT/2)))),0.0,0.0)
#define CHROMA_tex(position) vec4(chromaAt(ivec2(floor((position)*vec2(WIDTH/2,HEIGHT/2)))),0.0,0.0)
#define LUMA_texOff(offset) vec4(luma(q))
'''


def main():
    source = (ROOT / 'reference/KrigBilateral.glsl').read_text(encoding='utf-8')
    sections = source.split('//!DESC ')[1:]
    assert len(sections) == 3
    vertical, horizontal, reconstruct = [part.split('\n', 1)[1].split('//!HOOK')[0] for part in sections]
    reconstruct = reconstruct.replace('CHROMA_pos * HOOKED_size - chromaOffset - vec2(0.5)',
                                      'vec2(float(q.x)*0.5,(float(q.y)+0.5)*0.5-0.5)')
    footer = r'''
void main() {
    q=ivec2(gl_GlobalInvocationID.xy);
    if(q.x>=WIDTH || q.y>=HEIGHT) return;
    vec2 value=hook().xy;
    outU[q.y*p.du+q.x]=storeValue(value.x);
    outV[q.y*p.dv+q.x]=storeValue(value.y);
#if WRITE_Y
    outY[q.y*p.dy+q.x]=storeY(float(yy[q.y*p.sy+q.x]));
#endif
}
'''
    kernels = {
        'vertical': COMMON + VERTICAL_BINDINGS + vertical + '''
void main(){q=ivec2(gl_GlobalInvocationID.xy);if(q.x>=WIDTH||q.y>=HEIGHT/2)return;vert[q.y*WIDTH+q.x]=hook().xy;}
''',
        'horizontal': COMMON + HORIZONTAL_BINDINGS + horizontal + '''
void main(){q=ivec2(gl_GlobalInvocationID.xy);if(q.x>=WIDTH/2||q.y>=HEIGHT/2)return;guide[q.y*(WIDTH/2)+q.x]=hook().xy;}
''',
        'reconstruct': COMMON + RECONSTRUCT_BINDINGS + reconstruct + footer,
    }
    header = '#pragma once\n// Generated by tools/generate_shaders.py; LGPL-3.0-or-later.\nnamespace shader {\n'
    for name, text in kernels.items():
        text = '// Adapted from KrigBilateral by Shiandow. LGPL-3.0-or-later.\n' + text
        text = '\n'.join(line.rstrip() for line in text.splitlines()) + '\n'
        (ROOT / 'src' / (name + '.comp')).write_text(text, encoding='utf-8', newline='\n')
        header += f'inline constexpr const char* {name}=R"KRIG({text})KRIG";\n'
    (ROOT / 'src/shaders.h').write_text(header + '}\n', encoding='utf-8', newline='\n')


if __name__ == '__main__':
    main()
