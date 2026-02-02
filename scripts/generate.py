#!/usr/bin/env python3
"""Script to generate the mosaic manifest."""

import sys
from pathlib import Path

# Add src to path for development
src_path = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(src_path))

from idc_mosaic.generator import main

if __name__ == "__main__":
    main()
