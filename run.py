#!/usr/bin/env python3
"""Signalpost: Top-level entrypoint for the Norwegian company fact extraction agent.

Usage Examples:
    # Single company lookup mode (fast live grading)
    python run.py --orgnr 923609016 --output profiles/

    # Batch lookup mode (1,000-company evaluation)
    python run.py --input orgnumbers.txt --output profiles/ --max-requests 2000 --max-seconds 2400
"""

from signalpost.cli import main

if __name__ == "__main__":
    main()
