#!/usr/bin/env python3
"""Script to sample 1,000 active Norwegian organisation numbers and drive batch profile ingestion.

Usage:
    python scripts/generate_submission_sample.py [--sample-only] [--count 1000] [--output-dir profiles/]
"""

import argparse
import asyncio
from pathlib import Path
import sys

from signalpost.cli import run_pipeline
from signalpost.sampling import sample_active_orgnrs


async def async_main() -> None:
    parser = argparse.ArgumentParser(description="Generate 1,000 company sample and drive ingestion.")
    parser.add_argument("--count", type=int, default=1000, help="Number of active organisations to sample.")
    parser.add_argument("--sample-file", type=str, default="data/sample_1000_orgnrs.txt", help="Path to save sampled org numbers.")
    parser.add_argument("--output-dir", type=str, default="profiles", help="Output directory for profile JSONs.")
    parser.add_argument("--sample-only", action="store_true", help="Only sample and save org numbers without running ingestion.")
    parser.add_argument("--batch-size", type=int, default=800, help="Batch size per session to stay strictly within the 2,000 request budget.")
    parser.add_argument("--max-requests", type=int, default=2000, help="Per-session request limit.")
    parser.add_argument("--max-seconds", type=float, default=2400.0, help="Per-session time limit.")
    parser.add_argument("--db", type=str, default="signalpost.db", help="SQLite database path.")
    args = parser.parse_args()

    sample_path = Path(args.sample_file)
    sample_path.parent.mkdir(parents=True, exist_ok=True)

    orgnrs: list[str] = []

    if sample_path.exists():
        print(f"[*] Reading existing sample from {sample_path}...")
        with open(sample_path, "r", encoding="utf-8") as f:
            orgnrs = [line.strip() for line in f if line.strip()]
        print(f"[*] Loaded {len(orgnrs)} organisation numbers.")

    if len(orgnrs) < args.count:
        needed = args.count - len(orgnrs)
        print(f"[*] Downloading Enhetsregisteret bulk export to sample {needed} additional active org numbers...")
        new_orgnrs = await sample_active_orgnrs(count=needed)
        orgnrs.extend(new_orgnrs)
        # Deduplicate while preserving order
        seen = set()
        deduped = []
        for o in orgnrs:
            if o not in seen:
                seen.add(o)
                deduped.append(o)
        orgnrs = deduped

        with open(sample_path, "w", encoding="utf-8") as f:
            for o in orgnrs:
                f.write(f"{o}\n")
        print(f"✅ Saved {len(orgnrs)} active organisation numbers to {sample_path}.")

    if args.sample_only:
        print("Sample generation complete (--sample-only enabled).")
        return

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Drive ingestion across batches respecting the 2,000 request limit per session
    total = len(orgnrs)
    batch_size = args.batch_size
    num_batches = (total + batch_size - 1) // batch_size

    print(f"\n[*] Starting multi-session ingestion across {num_batches} batch sessions (batch_size={batch_size})...")

    for i in range(num_batches):
        batch_orgnrs = orgnrs[i * batch_size : (i + 1) * batch_size]
        print(f"\n{'#'*80}")
        print(f"SESSION {i+1} / {num_batches}: Processing {len(batch_orgnrs)} organisations")
        print(f"{'#'*80}")

        report = await run_pipeline(
            orgnrs=batch_orgnrs,
            output_dir=out_dir,
            max_requests=args.max_requests,
            max_seconds=args.max_seconds,
            db_path=args.db,
        )

        print(f"Session {i+1} Complete: {report.completed} profiles completed using {report.outbound_requests_used} requests.")


if __name__ == "__main__":
    asyncio.run(async_main())
