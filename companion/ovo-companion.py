#!/usr/bin/env python3
"""Run the OVO companion from a source checkout, without installing it.

    python companion/ovo-companion.py run --device mochi.local

The installer writes this file's absolute path into Claude Code settings when
the package is not pip-installed, so hooks keep working from any directory.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from ovo_companion.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
