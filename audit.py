#!/usr/bin/env python3
"""Zero-install launcher: run the audit straight from a checkout or a Claude Code plugin directory.

    python audit.py targeting.csv [--out audit.md] [--json audit.json] [--target-acos 0.30] ...

Standard library only, Python 3.10+. Equivalent to the `ppc-hindsight` console script after `pip install -e .`.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ppc_hindsight.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
