"""Compare external 1080p YUV420P16 against the original shader algorithm."""
import sys
from compare_shader import main

if __name__ == '__main__':
    if '--width' not in sys.argv:
        sys.argv += ['--width', '1920', '--height', '1080']
    main()
