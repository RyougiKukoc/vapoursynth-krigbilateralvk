"""Inspect all original-shader and native intermediate stages."""
import sys
from pathlib import Path
from compare_shader import main

if __name__ == '__main__':
    if '--output' not in sys.argv:
        sys.argv += ['--output', str(Path(__file__).resolve().parents[1] / 'results/stage-inspection.json')]
    main()
