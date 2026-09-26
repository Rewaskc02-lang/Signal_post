#!/usr/bin/env python3
"""Signalpost: Top-level entrypoint for the Norwegian company fact extraction agent.

Usage Examples:
    # Single company lookup mode (fast live grading)
    python run.py --orgnr 923609016 --output profiles/

    # Batch lookup mode (1,000-company evaluation)
    python run.py --input orgnumbers.txt --output profiles/ --max-requests 2000 --max-seconds 2400
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# Auto-re-exec inside .venv if dependencies are not found in current environment
try:
    import httpx  # noqa: F401
    import pydantic  # noqa: F401
except ImportError:
    venv_py = ROOT / ".venv" / "bin" / "python"
    if venv_py.exists() and sys.executable != str(venv_py):
        os.execv(str(venv_py), [str(venv_py)] + sys.argv)

sys.path.insert(0, str(ROOT / "src"))

from signalpost.cli import main

if __name__ == "__main__":
    main()
