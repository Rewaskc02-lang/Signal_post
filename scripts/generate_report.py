#!/usr/bin/env python3
"""HTML Report Generator for Signalpost: generates a visual inspection dashboard for judges."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from signalpost.viewer import generate_html_report


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate visual HTML evaluation report from Signalpost envelopes.")
    parser.add_argument("--input", "-i", type=str, default="envelopes.jsonl", help="Path to envelopes.jsonl file.")
    parser.add_argument("--output", "-o", type=str, default="reports/report.html", help="Path to output HTML report.")
    args = parser.parse_args()

    in_path = Path(args.input)
    if not in_path.exists():
        sys.stderr.write(f"Error: Input file {in_path} not found.\n")
        sys.exit(1)

    envelopes = []
    with open(in_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    envelopes.append(json.loads(line))
                except Exception:
                    pass

    out_path = Path(args.output)
    generate_html_report(envelopes, out_path)
    print(f"Generated HTML report with {len(envelopes)} companies at: {out_path.resolve()}")


if __name__ == "__main__":
    main()
