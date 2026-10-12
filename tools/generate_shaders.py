"""Generate fixed-layout compute kernels; preserve Shiandow's solve ordering."""
from pathlib import Path
import re

def scalarize(body, python):
    total_start=body.index('    for (int i=0; i<N+1; i++) {')
    total_end=body.index('\n    }',total_start)+len('\n    }')
    loop=body[total_start:total_end]
    statements=loop[loop.index('{')+1:loop.rindex('}')]
    body=body[:total_start]+'\n'.join('    {\n'+re.sub(r'\bi\b',str(i),statements)+'\n    }' for i in range(9))+body[total_end:]
    solve_start=body.index('    for (int i=0; i<N; i++) {')
    solve_end=body.index('\n    }',solve_start)+len('\n    }')
    statements=[]
    for i in range(7,-1,-1):
        for j in range(i+1,8):statements.append(f'    b[{i}] -= M({i}, {j}) * b[{j}];')
        statements.append(f'    b[{i}] = divRound(b[{i}],M({i}, {i}));' if python else f'    b[{i}] /= M({i}, {i});')
        statements.append(f'    interp += b[{i}] * (X[{i}] - X[N]);')
    body=body[:solve_start]+'\n'.join(statements)+body[solve_end:]
    body=re.sub(r'#define M\(i,j\).*\n','',body)
    body=body.replace('float Mx[N*(N+1)/2];','float '+','.join(f'm{i}{j}' for i in range(8) for j in range(i,8))+';')
    body=re.sub(r'M\((\d+),\s*(\d+)\)',lambda m:f'm{min(int(m[1]),int(m[2]))}{max(int(m[1]),int(m[2]))}',body)
    body=body.replace('float b[N];','float '+','.join(f'b{i}' for i in range(8))+';')
    body=re.sub(r'b\[(\d+)\]',lambda m:'b'+m[1],body)
    return body

ROOT = Path(__file__).resolve().parents[1]

COMMON = r'''
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
layout(binding=8,std430) readonly buffer RowWeights { vec2 weights[]; };
float divRound(float a,float b) {
    precise float q=a/b;
    precise float residual=fma(-q,b,a);
    precise float corrected=fma(residual,1.0/b,q);
    return (isnan(corrected)||isinf(q)) ? q : corrected;
}
float norm(SAMPLE x) {
#if INTEGER
#if REFERENCE_PYTHON
    precise float scaled=float(x)*(1.0/(MAXVAL+1.0));
    precise float result=fma(scaled,1.0/MAXVAL,scaled);
    return result;
#else
    return float(x)/MAXVAL;
#endif
#else
    return float(x);
#endif
}
uint halfTruncate(float x) {
    uint raw=floatBitsToUint(x), sign=(raw>>16)&32768u;
    int exponent=int((raw>>23)&255u)-127;
    uint mantissa=raw&8388607u;
    if(exponent==128) return sign|(mantissa==0u ? 31744u : 32256u);
    if(exponent>15) return sign|31744u;
    if(exponent>=-14) return sign|(uint(exponent+15)<<10)|(mantissa>>13);
    if(exponent<-24) return sign;
    return sign|((mantissa|8388608u)>>uint(-exponent-1));
}
float halfDown(float x) {
    uint b=halfTruncate(x);
    float h=unpackHalf2x16(b).x;
    if(h>x) b+=1u;
    return unpackHalf2x16(b).x;
}
float halfNearestUp(float x) {
    float lower=halfDown(x);
    uint b=halfTruncate(lower);
    b += (b & 32768u)!=0u ? (b==32768u ? 1u : 0xffffffffu) : 1u;
    float upper=unpackHalf2x16(b).x;
    precise float midpoint=(lower+upper)*0.5;
    return x>=midpoint ? upper : lower;
}
float halfNearestEven(float x) {
    float lower=halfDown(x);
    uint bits=halfTruncate(lower);
    uint next=bits+((bits&32768u)!=0u ? (bits==32768u ? 1u : 0xffffffffu) : 1u);
    float upper=unpackHalf2x16(next).x;
    precise float midpoint=(lower+upper)*0.5;
    return x==midpoint ? (((bits&1u)==0u) ? lower : upper) : (x>midpoint ? upper : lower);
}
OUTPUT storeValue(float x) {
#if DEBUG_STAGE
    return x;
#else
    x=clamp(x,0.0,1.0);
#if INTEGER
    return SAMPLE(roundEven(halfDown(x)*MAXVAL));
#elif HALF_OUTPUT
    return SAMPLE(halfNearestEven(x));
#else
    return SAMPLE(x);
#endif
#endif
}
'''

VERTICAL = r'''
void main() {
    ivec2 q=ivec2(gl_GlobalInvocationID.xy);
    if(q.x>=WIDTH || q.y>=HEIGHT/2) return;
    float rows=float(q.y);
    float pos=divRound(rows+0.5,float(HEIGHT/2));
    float pt=1.0/float(HEIGHT);
    float low=pt*(2.0*rows+0.5);
    float high=pt*(2.0*rows+1.5);
    float a=1.0-abs((low-pos)*float(HEIGHT/2));
    float b=1.0-abs((high-pos)*float(HEIGHT/2));
#if REFERENCE_PYTHON
    a=weights[q.y].x;
    b=weights[q.y].y;
#endif
    float top=norm(yy[(2*q.y)*p.sy+q.x]);
    float bottom=norm(yy[(2*q.y+1)*p.sy+q.x]);
    float mean=divRound(a*top+b*bottom,a+b);
    float second=divRound(a*(top*top)+b*(bottom*bottom),a+b);
    precise float variance=abs(second-mean*mean);
    vert[q.y*WIDTH+q.x]=vec2(halfDown(mean),halfDown(variance));
}
'''

HORIZONTAL = r'''
void main() {
    ivec2 q=ivec2(gl_GlobalInvocationID.xy);
    if(q.x>=WIDTH/2 || q.y>=HEIGHT/2) return;
    vec2 left=vert[q.y*WIDTH+2*q.x], right=vert[q.y*WIDTH+2*q.x+1];
    float mean=(left.x+right.x)*0.5;
    float second=(left.x*left.x+right.x*right.x)*0.5;
    float variance=abs(second-mean*mean)+halfNearestUp((left.y+right.y)*0.5);
    guide[q.y*(WIDTH/2)+q.x]=vec2(halfDown(mean),halfDown(variance));
}
'''

VERTICAL_SHADER = VERTICAL.replace('divRound(rows+0.5,float(HEIGHT/2))','(rows+0.5)/float(HEIGHT/2)')
VERTICAL_SHADER = VERTICAL_SHADER.replace('divRound(a*top+b*bottom,a+b)','(a*top+b*bottom)/(a+b)')
VERTICAL_SHADER = VERTICAL_SHADER.replace('divRound(a*(top*top)+b*(bottom*bottom),a+b)',
                                         '(a*(top*top)+b*(bottom*bottom))/(a+b)')
HORIZONTAL_SHADER = HORIZONTAL.replace('(left.x+right.x)*0.5','(0.75*left.x+0.75*right.x)/1.5')
HORIZONTAL_SHADER = HORIZONTAL_SHADER.replace('(left.x*left.x+right.x*right.x)*0.5',
                                             '(0.75*left.x*left.x+0.75*right.x*right.x)/1.5')

def main():
    # The original shader is LGPL-3.0-or-later by Shiandow. Retain the
    # complete source beside these generated adaptations.
    source = (ROOT/'reference/KrigBilateral.glsl').read_text(encoding='utf-8')
    body = source[source.index('// -- Convenience --'):]
    body = body.replace('#define GetY(coord)  LOWRES_Y_tex(LOWRES_Y_pt*(pos+coord+vec2(0.5))).xy',
                        '#define GetY(coord) getGuide(ivec2(pos+coord))')
    body = body.replace('#define GetUV(coord) CHROMA_tex(CHROMA_pt*(pos+coord+vec2(0.5))).xy',
                        '#define GetUV(coord) getUV(ivec2(pos+coord))')
    body = body.replace('CHROMA_pos * HOOKED_size - chromaOffset - vec2(0.5)',
                        'vec2(float(q.x)*0.5,(float(q.y)+0.5)*0.5-0.5)')
    body = body.replace('vec4 hook() {', 'vec2 reconstruct(ivec2 q) {')
    body = body.replace('LUMA_texOff(0).x', 'norm(yy[q.y*p.sy+q.x])')
    body = body.replace('return interp.zwxx;', 'return interp.zw;')
    original_body = body
    body = body.replace('round(pos)', 'roundEven(pos)')
    body = body.replace('total += w.x*w.y*vec4(X[i].x, pow(X[i].x, 2.0), X[i].y, 1.0);',
                        'total = fma(vec4(w.x*w.y),vec4(X[i].x,X[i].x*X[i].x,X[i].y,1.0),total);')
    body = body.replace('sqr(noise)', '0.0025')
    body = body.replace('1.0 + X[i].y/localVar', 'fma(X[i].y,1.0/localVar,1.0)')
    body = body.replace('inversesqrt(', 'reciprocalSqrt(')
    body = re.sub(r'(#define C\(i,j\)).*',r'\1 (reciprocalSqrt(1.0 + divRound(X[i].y + X[j].y,localVar)) * exp(-0.5*(divRound(sqr(X[i].x - X[j].x),localVar + X[i].y + X[j].y) + sqr(coords[i] - coords[j]))) + divRound((X[i].x - y) * (X[j].x - y),localVar))',body)
    body = re.sub(r'(#define C\(i,j\)).*',r'\1 covariance(X[i],X[j],coords[i]-coords[j],localVar,y)',body)
    body = re.sub(r'(#define c\(i\)).*',r'\1 (reciprocalSqrt(fma(X[i].y,divRound(1.0,localVar),1.0)) * exp(-0.5*(divRound(sqr(X[i].x - y),localVar + X[i].y) + sqr(coords[i] - offset))))',body)
    body = re.sub(r'(b\[\d+\]|M\(\d+, \d+\)) -= (b\[\d+\]|M\(\d+, \d+\)) \* (M\(\d+, \d+\)) / (M\(\d+, \d+\));',
                  r'\1 -= divRound(\2 * \3,\4);',body)
    body = body.replace('b[N-1-i] /= M(N-1-i, N-1-i);','b[N-1-i] = divRound(b[N-1-i],M(N-1-i, N-1-i));')
    body = body.replace('total.xyz /= total.w;','total.xyz = vec3(divRound(total.x,total.w),divRound(total.y,total.w),divRound(total.z,total.w));')
    body = body.replace('exp(', 'numpyExp(')
    debug_radius = '''float radius = 1.0;
#if DEBUG_STAGE == 3
    return vec2(localVar,y);
#elif DEBUG_STAGE == 4
    return vec2(C(0,1),c(0));
#elif DEBUG_STAGE == 6
    return vec2(X[0].x,X[0].y);
#elif DEBUG_STAGE == 7
    return vec2(numpyExp(-4.0*y),reciprocalSqrt(1.0+y));
#endif'''
    body = body.replace('float radius = 1.0;',debug_radius)
    original_body = original_body.replace('float radius = 1.0;',debug_radius)
    body=scalarize(body,True)
    original_body=scalarize(original_body,False)
    # Python NumPy rounds every arithmetic operation except these two explicit
    # fused operations. Preserve that contract while measuring GPU intrinsics.
    for key in ('float','vec2','vec4'):
        body = re.sub(r'(?m)^(\s*)'+key+r' (?!reconstruct)',r'\1precise '+key+' ',body)
    helpers = r'''
// NumPy's AVX2/FMA expf range reduction and rational coefficients.
// Copyright NumPy Developers, BSD-3-Clause; see licenses/NUMPY.txt.
float numpyExp(float x) {
    if(isnan(x)) return x;
    if(x<=-103.97208404541015625) return 0.0;
    if(x>=88.72283935546875) return uintBitsToFloat(0x7f800000u);
    precise float quadrant=x*1.4426950408889634074;
    quadrant=(quadrant+12582912.0)-12582912.0;
    precise float reduced=fma(quadrant,-6.93145752e-1,x);
    reduced=fma(quadrant,-1.42860677e-6,reduced);
    precise float numerator=fma(5.082762527590693718096e-4,reduced,6.757896990527504603057e-3);
    numerator=fma(numerator,reduced,5.114512081637298353406e-2);
    numerator=fma(numerator,reduced,2.473615434895520810817e-1);
    numerator=fma(numerator,reduced,7.257664613233124478488e-1);
    numerator=fma(numerator,reduced,9.999999999980870924916e-1);
    precise float denominator=fma(2.159509375685829852307e-2,reduced,-2.742335390411667452936e-1);
    denominator=fma(denominator,reduced,1.0);
    precise float ratio=divRound(numerator,denominator);
    return ldexp(ratio,int(quadrant));
}
float reciprocalSqrt(float x) {
    precise float root=sqrt(x);
    precise float residual=fma(-root,root,x);
    precise float corrected=fma(residual,0.5/root,root);
    return divRound(1.0,(isnan(corrected)||isinf(root)||root==0.0) ? root : corrected);
}
float covariance(vec4 a,vec4 b,vec2 offset,float variance,float y) {
    precise float difference=a.x-b.x;
    precise float square=difference*difference;
    precise float denominator=variance+a.y;
    denominator=denominator+b.y;
    precise float fraction=divRound(square,denominator);
    precise float spatial=dot(offset,offset);
    precise float argument=-0.5*(fraction+spatial);
    precise float sum=a.y+b.y;
    precise float inverseArgument=1.0+divRound(sum,variance);
    precise float e=numpyExp(argument);
    precise float factor=reciprocalSqrt(inverseArgument);
    precise float result=e*factor;
    precise float da=a.x-y;
    precise float db=b.x-y;
    precise float product=da*db;
    precise float linear=divRound(product,variance);
    result=result+linear;
    return result;
}
ivec2 clampCoord(ivec2 q) { return clamp(q,ivec2(0),ivec2(WIDTH/2-1,HEIGHT/2-1)); }
vec2 getGuide(ivec2 q) { q=clampCoord(q); return guide[q.y*(WIDTH/2)+q.x]; }
vec2 getUV(ivec2 q) { q=clampCoord(q); return vec2(norm(uu[q.y*p.su+q.x]),norm(vv[q.y*p.sv+q.x])); }
'''
    footer = r'''
void main() {
    ivec2 q=ivec2(gl_GlobalInvocationID.xy);
    if(q.x>=WIDTH || q.y>=HEIGHT) return;
    vec2 uv;
#if DEBUG_STAGE == 1
    uv=vert[(q.y/2)*WIDTH+q.x];
#elif DEBUG_STAGE == 2
    uv=guide[(q.y/2)*(WIDTH/2)+q.x/2];
#else
    uv=reconstruct(q);
#endif
    outU[q.y*p.du+q.x]=storeValue(uv.x);
    outV[q.y*p.dv+q.x]=storeValue(uv.y);
#if DEBUG_STAGE
    outY[q.y*p.du+q.x]=norm(yy[q.y*p.sy+q.x]);
#endif
}
'''
    vertical = re.sub(r'(?m)^(\s*)(float|vec2) ',r'\1precise \2 ',VERTICAL)
    horizontal = re.sub(r'(?m)^(\s*)(float|vec2) ',r'\1precise \2 ',HORIZONTAL)
    kernels = {'vertical': COMMON+'\n#if REFERENCE_PYTHON\n'+vertical+'\n#else\n'+VERTICAL_SHADER+'\n#endif\n',
               'horizontal': COMMON+'\n#if REFERENCE_PYTHON\n'+horizontal+'\n#else\n'+HORIZONTAL_SHADER+'\n#endif\n',
               'reconstruct': COMMON+helpers+'\n#if REFERENCE_PYTHON\n'+body+'\n#else\n'+original_body+'\n#endif\n'+footer}
    header = '#pragma once\n// Generated by tools/generate_shaders.py; LGPL-3.0-or-later.\nnamespace shader {\n'
    for name, text in kernels.items():
        text = '// Adapted from KrigBilateral by Shiandow. LGPL-3.0-or-later.\n' + text
        text = '\n'.join(line.rstrip() for line in text.splitlines()) + '\n'
        (ROOT/'src'/(name+'.comp')).write_text(text, encoding='utf-8', newline='\n')
        header += f'inline constexpr const char* {name} = R"KRIG({text})KRIG";\n'
    (ROOT/'src/shaders.h').write_text(header+'}\n', encoding='utf-8', newline='\n')

if __name__ == '__main__':
    main()
