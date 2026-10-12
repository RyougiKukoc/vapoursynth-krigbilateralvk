"""Serialize and rotate independent process repetitions; keep logs and GPU telemetry."""
import argparse
import json
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
from compare import ROOT

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--raw',type=Path,required=True)
    ap.add_argument('--results',type=Path,required=True)
    args=ap.parse_args()
    if args.results.exists():raise FileExistsError(args.results)
    args.results.mkdir(parents=True)
    cases=[('vk','python'),('placebo','python'),('vk','shader'),('python','python')]
    values={f'{backend}-{reference}':[] for backend,reference in cases}
    for repeat in range(3):
        order=cases[repeat:]+cases[:repeat]
        for backend,reference in order:
            name=f'{backend}-{reference}';path=args.results/f'{name}-r{repeat}'
            telemetry=None
            with path.with_suffix('.log').open('w',encoding='utf-8') as log, path.with_suffix('.telemetry.csv').open('w') as gpu:
                if shutil.which('nvidia-smi'):
                    telemetry=subprocess.Popen(['nvidia-smi','--query-gpu=timestamp,name,utilization.gpu,memory.used,temperature.gpu,power.draw,clocks.sm',
                                                '--format=csv','--loop-ms=500'],stdout=gpu,stderr=subprocess.STDOUT)
                try:
                    frames='4' if backend=='python' else '1024'
                    warmup='1' if backend=='python' else '16'
                    completed=subprocess.run([sys.executable,str(ROOT/'tests/benchmark.py'),'--backend',backend,
                                              '--reference',reference,'--frames',frames,'--warmup',warmup,
                                              '--prefetch','4','--threads','4','--raw',str(args.raw.resolve()),
                                              '--output',str(path.with_suffix('.json'))],stdout=log,stderr=subprocess.STDOUT)
                    if completed.returncode:raise RuntimeError('Benchmark failed: '+str(path))
                finally:
                    if telemetry:telemetry.terminate();telemetry.wait(timeout=15)
            result=json.loads(path.with_suffix('.json').read_text(encoding='utf-8'))
            values[name].append(result['fps'])
            print(name,repeat,result['fps'],flush=True)
    summary={name:{'fps':rates,'median_fps':statistics.median(rates),'min_fps':min(rates),'max_fps':max(rates)}
             for name,rates in values.items()}
    (args.results/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')

if __name__=='__main__':main()
