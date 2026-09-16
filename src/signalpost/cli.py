"""Unified command-line interface and execution orchestrator for Signalpost."""

import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time
from typing import Any
import httpx
from pydantic import BaseModel, ConfigDict, Field

from signalpost.brreg_client import BrregClient, InvalidOrgnrError, NotFoundError, BrregError
from signalpost.config import Settings, settings as default_settings
from signalpost.differ import ProfileUpdateSummary, diff_and_update_profile
from signalpost.explain import generate_company_summary, generate_fact_note
from signalpost.matcher import build_profile
from signalpost.models import CompanyProfile
from signalpost.orgnr import sanitize_orgnr, validate_orgnr
from signalpost.storage import Storage


class BudgetExhaustedError(Exception):
    """Raised when request quota or time budget has been reached."""


class BudgetTracker:
    """Thread-safe / async-safe tracker enforcing hard limits on requests, time, and cost."""

    def __init__(
        self,
        max_requests: int = 2000,
        max_seconds: float = 2400.0,
    ) -> None:
        self.max_requests = max_requests
        self.max_seconds = max_seconds
        self.start_time = time.time()
        self.request_count = 0
        self.completed_count = 0
        self.failed_count = 0
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0
        self.total_cost_usd = 0.0
        self.halt_reason: str | None = None
        self._lock = asyncio.Lock()

    @property
    def elapsed_seconds(self) -> float:
        return time.time() - self.start_time

    async def record_request(self) -> None:
        """Check budget and record one outbound request."""
        async with self._lock:
            if self.request_count >= self.max_requests:
                self.halt_reason = f"Outbound request limit reached ({self.max_requests} requests)"
                raise BudgetExhaustedError(self.halt_reason)

            if self.elapsed_seconds >= self.max_seconds:
                self.halt_reason = f"Time budget exceeded ({self.max_seconds:.0f}s elapsed)"
                raise BudgetExhaustedError(self.halt_reason)

            self.request_count += 1

    def is_exhausted(self) -> bool:
        if self.request_count >= self.max_requests:
            self.halt_reason = f"Outbound request limit reached ({self.max_requests} requests)"
            return True
        if self.elapsed_seconds >= self.max_seconds:
            self.halt_reason = f"Time budget exceeded ({self.max_seconds:.0f}s elapsed)"
            return True
        return False

    async def record_profile_success(self, tokens_in: int = 0, tokens_out: int = 0, cost: float = 0.0) -> None:
        async with self._lock:
            self.completed_count += 1
            self.total_prompt_tokens += tokens_in
            self.total_completion_tokens += tokens_out
            self.total_cost_usd += cost

    async def record_profile_failure(self) -> None:
        async with self._lock:
            self.failed_count += 1


class RunReport(BaseModel):
    """Machine-readable summary of pipeline execution."""

    model_config = ConfigDict(extra="forbid")

    timestamp: str
    total_requested: int
    completed: int
    failed: int
    outbound_requests_used: int
    max_requests_cap: int
    elapsed_seconds: float
    max_seconds_cap: float
    total_prompt_tokens: int
    total_completion_tokens: int
    estimated_cost_usd: float
    projected_100_company_cost_usd: float
    halt_reason: str | None = None


async def process_single_orgnr(
    orgnr: str,
    output_dir: Path | None,
    storage: Storage,
    budget: BudgetTracker,
    semaphore: asyncio.Semaphore,
    client: BrregClient,
    enable_enrichment: bool = False,
    enable_summary: bool = True,
    enable_llm: bool = False,
    quiet: bool = False,
) -> tuple[CompanyProfile | None, ProfileUpdateSummary | None]:
    """Process a single organisation through validation, fetch, diffing, and file output."""
    if budget.is_exhausted():
        return None, None

    cleaned_orgnr = sanitize_orgnr(orgnr)

    # 1. Validation check
    if not validate_orgnr(cleaned_orgnr):
        if not quiet:
            sys.stderr.write(f"❌ Invalid orgnr format: {orgnr}\n")
        await budget.record_profile_failure()
        return None, None

    async with semaphore:
        if budget.is_exhausted():
            return None, None

        try:
            prev_profile = storage.get_profile(cleaned_orgnr)

            # Build profile from live registry
            fresh_profile = await build_profile(
                orgnr=cleaned_orgnr,
                enable_website_enrichment=enable_enrichment,
                client=client,
            )

            # Reconcile with previous state
            updated_profile, diff_summary = diff_and_update_profile(
                current_profile=fresh_profile,
                previous_profile=prev_profile,
                storage=storage,
            )

            tokens_in, tokens_out, cost = 0, 0, 0.0
            if enable_summary:
                summary_res = await generate_company_summary(
                    profile=updated_profile,
                    enable_llm=enable_llm,
                )
                tokens_in = summary_res.prompt_tokens
                tokens_out = summary_res.completion_tokens
                cost = summary_res.estimated_cost_usd

            # Save individual JSON profile to output directory if specified
            if output_dir is not None:
                output_dir.mkdir(parents=True, exist_ok=True)
                profile_path = output_dir / f"{cleaned_orgnr}.json"
                profile_dict = updated_profile.model_dump(mode="json")
                if enable_summary and 'summary_res' in locals():
                    profile_dict["executive_summary"] = summary_res.summary_text
                with open(profile_path, "w", encoding="utf-8") as f:
                    json.dump(profile_dict, f, indent=2, ensure_ascii=False)

            await budget.record_profile_success(tokens_in, tokens_out, cost)
            if not quiet:
                status_str = f"new={diff_summary.new_count}, changed={diff_summary.changed_count}, confirmed={diff_summary.confirmed_count}"
                print(f"✓ [{cleaned_orgnr}] Complete ({len(updated_profile.facts)} facts, {status_str})")

            return updated_profile, diff_summary

        except BudgetExhaustedError:
            return None, None
        except NotFoundError:
            if not quiet:
                print(f"⚠️ [{cleaned_orgnr}] Not found in Enhetsregisteret (404)")
            await budget.record_profile_failure()
            return None, None
        except Exception as exc:
            if not quiet:
                print(f"❌ [{cleaned_orgnr}] Error: {exc}")
            await budget.record_profile_failure()
            return None, None


async def run_pipeline(
    orgnrs: list[str],
    output_dir: Path | None = None,
    max_requests: int = 2000,
    max_seconds: float = 2400.0,
    concurrency: int = 15,
    enable_enrichment: bool = False,
    enable_summary: bool = True,
    enable_llm: bool = False,
    db_path: str = "signalpost.db",
    quiet: bool = False,
) -> RunReport:
    """Run the Signalpost ingestion pipeline across a list of organisation numbers."""
    budget = BudgetTracker(max_requests=max_requests, max_seconds=max_seconds)
    storage = Storage(db_path=db_path)
    semaphore = asyncio.Semaphore(concurrency)

    # Wrap httpx client with request counting hook
    async def request_hook(request: httpx.Request) -> None:
        await budget.record_request()

    custom_client = httpx.AsyncClient(
        timeout=httpx.Timeout(default_settings.brreg_request_timeout_seconds),
        limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
        headers={"Accept": "application/json", "User-Agent": "Signalpost/0.4.0"},
        event_hooks={"request": [request_hook]},
    )

    brreg_client = BrregClient(client=custom_client)

    if not quiet:
        print(f"\n{'='*80}")
        print(f"SIGNALPOST INGESTION RUN")
        print(f"  • Organisations:  {len(orgnrs)}")
        print(f"  • Output Directory: {output_dir or '(none)'}")
        print(f"  • Hard Limits:    max_requests={max_requests}, max_seconds={max_seconds:.0f}s")
        print(f"  • Options:        enrichment={enable_enrichment}, summary={enable_summary}, llm={enable_llm}")
        print(f"{'='*80}\n")

    try:
        tasks = [
            process_single_orgnr(
                orgnr=orgnr,
                output_dir=output_dir,
                storage=storage,
                budget=budget,
                semaphore=semaphore,
                client=brreg_client,
                enable_enrichment=enable_enrichment,
                enable_summary=enable_summary,
                enable_llm=enable_llm,
                quiet=quiet,
            )
            for orgnr in orgnrs
        ]
        await asyncio.gather(*tasks, return_exceptions=True)

    finally:
        await brreg_client.close()
        storage.close()

    elapsed = budget.elapsed_seconds
    comp = budget.completed_count
    fail = budget.failed_count
    reqs = budget.request_count
    cost = budget.total_cost_usd

    # Calculate projected 100-company cost
    cost_per_company = (cost / comp) if comp > 0 else 0.0
    projected_100_cost = cost_per_company * 100.0

    report = RunReport(
        timestamp=datetime.now(timezone.utc).isoformat(),
        total_requested=len(orgnrs),
        completed=comp,
        failed=fail,
        outbound_requests_used=reqs,
        max_requests_cap=max_requests,
        elapsed_seconds=round(elapsed, 2),
        max_seconds_cap=max_seconds,
        total_prompt_tokens=budget.total_prompt_tokens,
        total_completion_tokens=budget.total_completion_tokens,
        estimated_cost_usd=round(cost, 6),
        projected_100_company_cost_usd=round(projected_100_cost, 6),
        halt_reason=budget.halt_reason,
    )

    if not quiet:
        print(f"\n{'='*80}")
        print(f"RUN SUMMARY REPORT")
        print(f"{'='*80}")
        print(f"  • Completed Profiles:    {comp} / {len(orgnrs)}")
        print(f"  • Failed / Invalid:      {fail}")
        print(f"  • Outbound Requests:     {reqs} / {max_requests} limit")
        print(f"  • Wall-Clock Time:       {elapsed:.2f}s / {max_seconds:.0f}s cap")
        print(f"  • Estimated Run Cost:    ${cost:.6f} USD")
        print(f"  • Projected 100-Co Cost: ${projected_100_cost:.6f} USD")
        if budget.halt_reason:
            print(f"  ⚠️ Circuit Breaker Halt: {budget.halt_reason}")
        print(f"{'='*80}\n")

    return report


def build_arg_parser() -> argparse.ArgumentParser:
    """Build command line argument parser for run.py."""
    parser = argparse.ArgumentParser(
        prog="run.py",
        description="Signalpost: Norwegian company fact lookup and verification agent.",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--input",
        "-i",
        type=str,
        help="Path to a text file containing organisation numbers (one per line).",
    )
    group.add_argument(
        "--orgnr",
        "-o",
        type=str,
        help="Single 9-digit Norwegian organisation number to look up.",
    )

    parser.add_argument(
        "--output",
        "-out",
        type=str,
        default="profiles",
        help="Directory to write structured JSON company profiles (default: 'profiles').",
    )
    parser.add_argument(
        "--max-requests",
        type=int,
        default=2000,
        help="Hard circuit breaker on outbound HTTP requests (default: 2000).",
    )
    parser.add_argument(
        "--max-seconds",
        type=float,
        default=2400.0,
        help="Hard circuit breaker on wall-clock execution seconds (default: 2400s / 40 min).",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=15,
        help="Maximum concurrent asynchronous requests (default: 15).",
    )
    parser.add_argument(
        "--enrich",
        action="store_true",
        help="Enable secondary source website enrichment.",
    )
    parser.add_argument(
        "--no-summary",
        action="store_true",
        help="Disable executive summary generation.",
    )
    parser.add_argument(
        "--llm",
        action="store_true",
        help="Enable LLM-based summary generation (requires API key).",
    )
    parser.add_argument(
        "--db",
        type=str,
        default="signalpost.db",
        help="Path to SQLite persistence database (default: 'signalpost.db').",
    )
    parser.add_argument(
        "--json-report",
        type=str,
        help="Optional path to write a machine-readable JSON run report.",
    )
    parser.add_argument(
        "--quiet",
        "-q",
        action="store_true",
        help="Suppress progress outputs.",
    )

    return parser


async def async_main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()

    # Collect org numbers
    orgnrs: list[str] = []
    if args.orgnr:
        orgnrs = [args.orgnr]
    elif args.input:
        input_path = Path(args.input)
        if not input_path.exists():
            sys.stderr.write(f"Error: Input file '{args.input}' not found.\n")
            return 1
        with open(input_path, "r", encoding="utf-8") as f:
            orgnrs = [line.strip() for line in f if line.strip() and not line.startswith("#")]

    if not orgnrs:
        sys.stderr.write("Error: No organisation numbers provided.\n")
        return 1

    output_dir = Path(args.output) if args.output else None

    report = await run_pipeline(
        orgnrs=orgnrs,
        output_dir=output_dir,
        max_requests=args.max_requests,
        max_seconds=args.max_seconds,
        concurrency=args.concurrency,
        enable_enrichment=args.enrich,
        enable_summary=not args.no_summary,
        enable_llm=args.llm,
        db_path=args.db,
        quiet=args.quiet,
    )

    if args.json_report:
        report_path = Path(args.json_report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(report.model_dump_json(indent=2))

    return 0 if report.completed > 0 else 1


def main() -> None:
    exit_code = asyncio.run(async_main())
    sys.exit(exit_code)
