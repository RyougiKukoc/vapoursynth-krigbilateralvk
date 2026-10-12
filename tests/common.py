"""Locate a test build or an explicitly selected Release library."""
import os
import hashlib
import numpy as np
from pathlib import Path
import vapoursynth as vs

ROOT = Path(__file__).resolve().parents[1]


def plugin_path():
    override = os.environ.get("KRIGVK_PLUGIN")
    if override:
        path = Path(override).resolve()
        if not path.is_file():
            raise FileNotFoundError(path)
        return path
    suffix = ".dll" if os.name == "nt" else ".so"
    candidates = [ROOT / folder / ("krigbilateralvk" + suffix) for folder in ("build", "build-cmake")]
    for path in candidates:
        if path.is_file():
            return path
    raise FileNotFoundError("Build the plugin first or set KRIGVK_PLUGIN to a native library")


def load_plugin():
    path = plugin_path()
    vs.core.std.LoadPlugin(str(path))
    return path


def make_source(w,h,fmt,length=8):
    core=vs.core
    blank=core.std.BlankClip(width=w,height=h,format=fmt,length=length)
    def fill(n,f):
        f=f.copy()
        for p in range(3):
            a=np.asarray(f[p]); hh,ww=a.shape
            yy,xx=np.mgrid[:hh,:ww]
            rng=np.random.default_rng(7319+n*3+p)
            if n==0: value=np.full(a.shape,0.5 if p else 0.25)
            elif n==1: value=0.1+0.8*xx/max(ww-1,1)
            elif n==2: value=0.1+0.8*(xx+yy>=ww)
            elif n==3: value=rng.random(a.shape)
            elif n==4: value=0.49999+rng.normal(0,0.00001,a.shape)
            elif n==5: value=0.5+0.4*np.sin((xx+yy)/4)
            elif n==6: value=((xx+yy)%2).astype(float)
            else: value=0.1+0.8*xx/max(ww,1)+rng.normal(0,0.01,a.shape)
            if f.format.sample_type==vs.INTEGER:
                value=np.rint(np.clip(value,0,1)*((1<<f.format.bits_per_sample)-1))
            a[:]=value
        f.props.update(_Range=0,_Matrix=1,_Transfer=1,_Primaries=1,_ChromaLocation=0,_FieldBased=0)
        return f
    return core.std.ModifyFrame(blank,blank,fill)

def metric(a,b):
    a=np.ascontiguousarray(a)
    b=np.ascontiguousarray(b)
    if a.shape!=b.shape or a.dtype!=b.dtype:
        raise ValueError('Mismatched sample layout')
    raw_a=a.view(np.uint8).reshape(a.shape+(a.dtype.itemsize,))
    raw_b=b.view(np.uint8).reshape(b.shape+(b.dtype.itemsize,))
    different=np.any(raw_a!=raw_b,axis=-1)
    delta=a.astype(np.float64)-b.astype(np.float64)
    return {'equal':bool(not different.any()),'different':int(np.count_nonzero(different)),
            'max':float(np.max(np.abs(delta))), 'rms':float(np.sqrt(np.mean(delta*delta))),
            'finite':bool(np.isfinite(b).all()),'sha256':hashlib.sha256(b.tobytes()).hexdigest(),
            'reference_sha256':hashlib.sha256(a.tobytes()).hexdigest()}
