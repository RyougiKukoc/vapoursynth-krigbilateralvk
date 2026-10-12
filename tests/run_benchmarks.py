"""Serial fresh-process repetitions of the native algorithm and placebo pipeline."""
import argparse
import json
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
from common import ROOT


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--raw',type=Path,required=True)
    parser.add_argument('--results',type=Path,required=True)
    parser.add_argument('--frames',type=int,default=2048)
    args = parser.parse_args()
    if args.results.exists():
        raise FileExistsError(args.results)
    args.results.mkdir(parents=True)
    cases = ['vk','placebo']
    values = {name:[] for name in cases}
    for repeat in range(3):
        order = cases[repeat%2:] + cases[:repeat%2]
        for name in order:
            path = args.results / f'{name}-r{repeat}'
            telemetry = None
            with path.with_suffix('.log').open('w',encoding='utf-8') as log, path.with_suffix('.telemetry.csv').open('w') as gpu:
                if shutil.which('nvidia-smi'):
                    telemetry = subprocess.Popen(['nvidia-smi','--query-gpu=timestamp,name,utilization.gpu,memory.used,temperature.gpu,power.draw,clocks.sm',
                                                  '--format=csv','--loop-ms=500'],stdout=gpu,stderr=subprocess.STDOUT)
                try:
                    completed = subprocess.run([sys.executable,str(ROOT/'tests/benchmark.py'),'--backend',name,
                                                '--frames',str(args.frames),'--warmup','16','--prefetch','4','--threads','4',
                                                '--raw',str(args.raw.resolve()),'--output',str(path.with_suffix('.json'))],
                                               stdout=log,stderr=subprocess.STDOUT)
                    if completed.returncode:
                        raise RuntimeError('Benchmark failed: '+str(path))
                finally:
                    if telemetry:
                        telemetry.terminate()
                        telemetry.wait(timeout=15)
            data = json.loads(path.with_suffix('.json').read_text(encoding='utf-8'))
            values[name].append(data['fps'])
            print(name,repeat,data['fps'],flush=True)
    summary = {name:{'fps':rates,'median_fps':statistics.median(rates),'min_fps':min(rates),'max_fps':max(rates)}
               for name,rates in values.items()}
    (args.results/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')


if __name__ == '__main__':
    main()
