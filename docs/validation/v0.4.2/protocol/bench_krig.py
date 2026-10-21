"""Serial rotated A/B measurements using completed CPU and resident outputs."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys

ap = argparse.ArgumentParser()
ap.add_argument('--results', type=Path, required=True)
ap.add_argument('--frames', type=int, default=512)
ap.add_argument('--candidate', default='row32')
args = ap.parse_args()
root = Path('D:/WorkSpace-bm3d/vapoursynth-krigbilateralvk')
args.results.mkdir(parents=True, exist_ok=False)
cases = [(v, backend) for v in ('baseline', args.candidate) for backend in ('vk', 'vk_gpu')]
rows = []
for repeat in range(3):
    for variant, backend in cases[repeat:] + cases[:repeat]:
        prefix = args.results / f'{variant}-{backend}-r{repeat}'
        env = os.environ.copy()
        env['KRIGVK_PLUGIN'] = str(root / f'build/r2-study/{variant}.dll')
        command = [sys.executable, str(Path(__file__).with_name('run_isolated.py')),
                   str(root / 'tests/benchmark.py'), '--backend', backend,
                   '--frames', str(args.frames), '--warmup', '16', '--threads', '4',
                   '--prefetch', '4', '--raw', str(root.parent / 'sample-1080p.yuv'),
                   '--label', variant, '--output', str(prefix.with_suffix('.json')),
                   '--background-workload', 'Ordinary desktop active; other GPU applications not controlled']
        with prefix.with_suffix('.log').open('w', encoding='utf-8') as log, prefix.with_suffix('.telemetry.csv').open('w') as telem:
            telemetry = subprocess.Popen(['nvidia-smi', '--query-gpu=timestamp,utilization.gpu,memory.used,temperature.gpu,power.draw,clocks.sm',
                                          '--format=csv', '--loop-ms=500'], stdout=telem, stderr=subprocess.STDOUT)
            try:
                subprocess.run(command, cwd=root, env=env, stdout=log, stderr=subprocess.STDOUT, check=True)
            finally:
                telemetry.terminate()
                telemetry.wait(timeout=15)
        data = json.loads(prefix.with_suffix('.json').read_text(encoding='utf-8'))
        rows.append({'variant': variant, 'backend': backend, 'repeat': repeat, 'fps': data['fps'],
                     'sha256': data['output_sample_sha256']})
        print(variant, backend, repeat, data['fps'], flush=True)
summary = {}
for v, backend in cases:
    selected = [r for r in rows if r['variant'] == v and r['backend'] == backend]
    rates = [r['fps'] for r in selected]
    summary[v + '-' + backend] = {'fps': rates, 'median': statistics.median(rates), 'min': min(rates), 'max': max(rates)}
assert len({r['sha256'] for r in rows}) == 1, 'Sample output differs'
(args.results / 'summary.json').write_text(json.dumps({'rows': rows, 'summary': summary}, indent=2), encoding='utf-8')
print(json.dumps(summary, indent=2))
