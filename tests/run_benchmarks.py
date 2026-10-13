"""Serial fresh-process repetitions of the native algorithm and placebo pipeline."""
import argparse
import json
import os
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
    parser.add_argument('--cases',type=Path,help='JSON list of named backend/plugin/output configurations')
    args = parser.parse_args()
    if args.results.exists():
        raise FileExistsError(args.results)
    args.results.mkdir(parents=True)
    cases=json.loads(args.cases.read_text(encoding='utf-8')) if args.cases else [
        {'name':'vk-cpu','backend':'vk'},{'name':'vk-gpu','backend':'vk_gpu'},{'name':'placebo','backend':'placebo'}]
    assert len({case['name'] for case in cases})==len(cases)
    values = {case['name']:[] for case in cases}
    outputs={}
    for repeat in range(3):
        order = cases[repeat:] + cases[:repeat]
        for case in order:
            name=case['name']
            path = args.results / f'{name}-r{repeat}'
            telemetry = None
            with path.with_suffix('.log').open('w',encoding='utf-8') as log, path.with_suffix('.telemetry.csv').open('w') as gpu:
                if shutil.which('nvidia-smi'):
                    telemetry = subprocess.Popen(['nvidia-smi','--query-gpu=timestamp,name,utilization.gpu,memory.used,temperature.gpu,power.draw,clocks.sm',
                                                  '--format=csv','--loop-ms=500'],stdout=gpu,stderr=subprocess.STDOUT)
                try:
                    env=os.environ.copy()
                    if case.get('plugin'): env['KRIGVK_PLUGIN']=str(Path(case['plugin']).resolve())
                    command=[sys.executable,str(ROOT/'tests/benchmark.py'),'--backend',case['backend'],
                             '--label',name,'--frames',str(case.get('frames',args.frames)),'--warmup','16',
                             '--prefetch','4','--threads','4','--raw',str(args.raw.resolve()),'--output',str(path.with_suffix('.json'))]
                    for key in ('bits','sample_type','dither_type'):
                        if key in case: command.extend(['--'+key.replace('_','-'),str(case[key])])
                    completed = subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,env=env)
                    if completed.returncode:
                        raise RuntimeError('Benchmark failed: '+str(path))
                finally:
                    if telemetry:
                        telemetry.terminate()
                        telemetry.wait(timeout=15)
            data = json.loads(path.with_suffix('.json').read_text(encoding='utf-8'))
            values[name].append(data['fps'])
            outputs.setdefault(name,set()).add(data['output_sample_sha256'])
            print(name,repeat,data['fps'],flush=True)
    assert all(len(hashes)==1 for hashes in outputs.values()),'Output changed across repetitions'
    parity_names=('v031-cpu16','v040-cpu16','v040-gpu16')
    if all(name in outputs for name in parity_names):
        assert len(set.union(*(outputs[name] for name in parity_names)))==1,'Removing debug changed normal output'
    summary = {name:{'fps':rates,'median_fps':statistics.median(rates),'min_fps':min(rates),'max_fps':max(rates),
                     'output_sample_sha256':next(iter(outputs[name]))}
               for name,rates in values.items()}
    (args.results/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')


if __name__ == '__main__':
    main()
