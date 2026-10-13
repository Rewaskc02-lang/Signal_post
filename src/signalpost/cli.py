"""Unified command-line interface and execution orchestrator for Signalpost."""

from __future__ import annotations

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

from signalpost.brreg_client import (
    BrregClient,
    InvalidOrgnrError,
    NotFoundError,
    BrregError,
    set_log_stream,
)
from signalpost.config import Settings, settings as default_settings
from signalpost.differ import ProfileUpdateSummary, diff_and_update_profile
from signalpost.envelope import build_terminal_envelope, validate_envelope
from signalpost.explain import generate_company_summary, generate_fact_note
from signalpost.matcher import build_profile
from signalpost.models import CompanyProfile
from signalpost.orgnr import sanitize_orgnr, validate_orgnr
from signalpost.storage import Storage
from signalpost.viewer import render_terminal_profile


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
    emitted_envelopes: int = 0
    outbound_requests_used: int
    max_requests_cap: int
    elapsed_seconds: float
    max_seconds_cap: float
    total_prompt_tokens: int
    total_completion_tokens: int
    estimated_cost_usd: float
    projected_100_company_cost_usd: float
    halt_reason: str | None = None


def read_organisation_inputs(path: str | Path) -> list[str]:
    """Read organisation numbers from .txt, .csv, .json, or .jsonl files."""
    source = Path(path)
    if not source.exists():
        raise FileNotFoundError(f"Input file '{path}' not found.")
    text = source.read_text(encoding="utf-8-sig")
    values: list[Any] = []
    suffix = source.suffix.lower()

    if suffix == ".json":
        body = json.loads(text)
        if isinstance(body, list):
            values = body
        elif isinstance(body, dict):
            for k in (
                "organisation_numbers",
                "organisations",
                "companies",
                "orgnrs",
                "orgs",
                "items",
                "data",
                "records",
                "entities",
                "results",
                "inputs",
                "payload",
            ):
                if k in body and isinstance(body[k], list):
                    values = body[k]
                    break
            else:
                values = list(body.values())
    elif suffix == ".jsonl":
        for line in text.splitlines():
            line_str = line.strip()
            if line_str and not line_str.startswith("#"):
                try:
                    values.append(json.loads(line_str))
                except Exception:
                    values.append(line_str)
    else:
        for line in text.splitlines():
            line_str = line.strip()
            if not line_str or line_str.startswith("#"):
                continue
            delimiter = ";" if ";" in line_str else ("," if "," in line_str else None)
            if delimiter:
                for part in line_str.split(delimiter):
                    if part.strip():
                        values.append(part.strip())
            else:
                values.append(line_str)

    # Flatten nested lists if any
    flat_values: list[Any] = []
    for v in values:
        if isinstance(v, list):
            flat_values.extend(v)
        else:
            flat_values.append(v)

    orgnrs = []
    for value in flat_values:
        if isinstance(value, dict):
            org = (
                value.get("organisation_number")
                or value.get("orgnr")
                or value.get("org_number")
                or value.get("id")
                or ""
            )
        else:
            org = str(value)
        org_str = str(org).strip().strip('"').strip("'").strip("`")
        if org_str:
            orgnrs.append(org_str)
    return orgnrs


async def process_single_orgnr(
    orgnr: str,
    run_id: str,
    output_dir: Path | None,
    storage: Storage,
    budget: BudgetTracker,
    semaphore: asyncio.Semaphore,
    client: BrregClient,
    enable_enrichment: bool = True,
    enable_summary: bool = True,
    enable_llm: bool = False,
    quiet: bool = False,
) -> tuple[CompanyProfile | None, ProfileUpdateSummary | None, dict[str, Any]]:
    """Process a single organisation through validation, fetch, diffing, and envelope emission."""
    start_dt = datetime.now(timezone.utc)
    t0 = time.time()
    cleaned_orgnr = sanitize_orgnr(orgnr)

    # 1. Budget pre-check
    if budget.is_exhausted():
        end_dt = datetime.now(timezone.utc)
        envelope = build_terminal_envelope(
            orgnr=cleaned_orgnr or orgnr,
            run_id=run_id,
            started_at=start_dt,
            completed_at=end_dt,
            request_count=0,
            runtime_ms=(time.time() - t0) * 1000,
            error=budget.halt_reason or "Budget exhausted",
            terminal_state="budget_exhausted",
        )
        sys.stdout.write(json.dumps(envelope, ensure_ascii=False) + "\n")
        sys.stdout.flush()
        return None, None, envelope

    # 2. Validation check
    if not validate_orgnr(cleaned_orgnr):
        if not quiet:
            sys.stderr.write(f"❌ Invalid orgnr format/checksum: {orgnr}\n")
        await budget.record_profile_failure()
        end_dt = datetime.now(timezone.utc)
        envelope = build_terminal_envelope(
            orgnr=cleaned_orgnr or orgnr,
            run_id=run_id,
            started_at=start_dt,
            completed_at=end_dt,
            request_count=0,
            runtime_ms=(time.time() - t0) * 1000,
            error="Invalid Norwegian organisation number format or checksum",
            terminal_state="failed",
        )
        sys.stdout.write(json.dumps(envelope, ensure_ascii=False) + "\n")
        sys.stdout.flush()
        return None, None, envelope

    reqs_before = budget.request_count
    async with semaphore:
        if budget.is_exhausted():
            end_dt = datetime.now(timezone.utc)
            envelope = build_terminal_envelope(
                orgnr=cleaned_orgnr,
                run_id=run_id,
                started_at=start_dt,
                completed_at=end_dt,
                request_count=0,
                runtime_ms=(time.time() - t0) * 1000,
                error=budget.halt_reason or "Budget exhausted",
                terminal_state="budget_exhausted",
            )
            sys.stdout.write(json.dumps(envelope, ensure_ascii=False) + "\n")
            sys.stdout.flush()
            return None, None, envelope

        try:
            prev_profile = storage.get_profile(cleaned_orgnr)
            if prev_profile is None and output_dir is not None:
                existing_file = output_dir / f"{cleaned_orgnr}.json"
                if existing_file.exists():
                    try:
                        with open(existing_file, "r", encoding="utf-8") as f:
                            data = json.load(f)
                        prev_profile = CompanyProfile.model_validate(data)
                    except Exception:
                        pass

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
            summary_res = None
            if enable_summary:
                summary_res = await generate_company_summary(
                    profile=updated_profile,
                    enable_llm=enable_llm,
                )
                tokens_in = summary_res.prompt_tokens
                tokens_out = summary_res.completion_tokens
                cost = summary_res.estimated_cost_usd

            # Persist captured raw HTTP response snapshots for evaluator claim verification
            org_snapshots = client.get_snapshots_for_org(cleaned_orgnr) if hasattr(client, "get_snapshots_for_org") else []
            for snap in org_snapshots:
                storage.save_snapshot(
                    url=snap["url"],
                    content_hash=snap["content_hash"],
                    status_code=snap["status_code"],
                    content_type=snap.get("content_type", "application/json"),
                    response_body=snap["response_body"],
                    retrieved_at=snap["retrieved_at"],
                )

            # Save individual JSON profile to output directory if specified as a directory
            if output_dir is not None and not str(output_dir).endswith(".jsonl"):
                output_dir.mkdir(parents=True, exist_ok=True)
                profile_path = output_dir / f"{cleaned_orgnr}.json"
                profile_dict = updated_profile.model_dump(mode="json")
                if summary_res is not None:
                    profile_dict["executive_summary"] = summary_res.summary_text
                with open(profile_path, "w", encoding="utf-8") as f:
                    json.dump(profile_dict, f, indent=2, ensure_ascii=False)

            reqs_used = budget.request_count - reqs_before
            await budget.record_profile_success(tokens_in, tokens_out, cost)

            end_dt = datetime.now(timezone.utc)
            envelope = build_terminal_envelope(
                orgnr=cleaned_orgnr,
                run_id=run_id,
                started_at=start_dt,
                completed_at=end_dt,
                profile=updated_profile,
                diff_summary=diff_summary,
                request_count=reqs_used,
                runtime_ms=(time.time() - t0) * 1000,
                cost_usd=cost,
                terminal_state="available",
                snapshots=org_snapshots,
            )
            sys.stdout.write(json.dumps(envelope, ensure_ascii=False) + "\n")
            sys.stdout.flush()

            if not quiet:
                status_str = f"new={diff_summary.new_count}, changed={diff_summary.changed_count}, confirmed={diff_summary.confirmed_count}"
                sys.stderr.write(f"✓ [{cleaned_orgnr}] Complete ({len(updated_profile.facts)} facts, {status_str})\n")

            return updated_profile, diff_summary, envelope

        except BudgetExhaustedError:
            reqs_used = budget.request_count - reqs_before
            end_dt = datetime.now(timezone.utc)
            envelope = build_terminal_envelope(
                orgnr=cleaned_orgnr,
                run_id=run_id,
                started_at=start_dt,
                completed_at=end_dt,
                request_count=reqs_used,
                runtime_ms=(time.time() - t0) * 1000,
                error=budget.halt_reason or "Budget exhausted",
                terminal_state="failed",
            )
            sys.stdout.write(json.dumps(envelope, ensure_ascii=False) + "\n")
            sys.stdout.flush()
            return None, None, envelope

        except NotFoundError:
            reqs_used = budget.request_count - reqs_before
            if not quiet:
                sys.stderr.write(f"⚠️ [{cleaned_orgnr}] Not found in Enhetsregisteret (404)\n")
            await budget.record_profile_failure()
            end_dt = datetime.now(timezone.utc)
            envelope = build_terminal_envelope(
                orgnr=cleaned_orgnr,
                run_id=run_id,
                started_at=start_dt,
                completed_at=end_dt,
                request_count=reqs_used,
                runtime_ms=(time.time() - t0) * 1000,
                error="Organisasjonsnummer not found in Enhetsregisteret (404)",
                terminal_state="not_available",
            )
            sys.stdout.write(json.dumps(envelope, ensure_ascii=False) + "\n")
            sys.stdout.flush()
            return None, None, envelope

        except Exception as exc:
            reqs_used = budget.request_count - reqs_before
            if not quiet:
                sys.stderr.write(f"❌ [{cleaned_orgnr}] Error: {exc}\n")
            await budget.record_profile_failure()
            end_dt = datetime.now(timezone.utc)
            envelope = build_terminal_envelope(
                orgnr=cleaned_orgnr,
                run_id=run_id,
                started_at=start_dt,
                completed_at=end_dt,
                request_count=reqs_used,
                runtime_ms=(time.time() - t0) * 1000,
                error=str(exc),
                terminal_state="failed",
            )
            sys.stdout.write(json.dumps(envelope, ensure_ascii=False) + "\n")
            sys.stdout.flush()
            return None, None, envelope


async def run_pipeline(
    orgnrs: list[str],
    output_dir: Path | None = None,
    envelopes_output: Path | None = None,
    run_id: str | None = None,
    max_requests: int = 2000,
    max_seconds: float = 2400.0,
    concurrency: int = 15,
    enable_enrichment: bool = True,
    enable_summary: bool = True,
    enable_llm: bool = False,
    db_path: str = "signalpost.db",
    quiet: bool = False,
) -> RunReport:
    """Run the Signalpost ingestion pipeline across a list of organisation numbers."""
    run_id = run_id or f"run-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
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
        follow_redirects=True,
    )

    brreg_client = BrregClient(client=custom_client)

    if not quiet:
        sys.stderr.write(f"\n{'='*80}\n")
        sys.stderr.write("SIGNALPOST INGESTION RUN\n")
        sys.stderr.write(f"  • Run ID:           {run_id}\n")
        sys.stderr.write(f"  • Organisations:    {len(orgnrs)}\n")
        sys.stderr.write(f"  • Output Path:      {output_dir or '(stdout)'}\n")
        sys.stderr.write(f"  • Hard Limits:      max_requests={max_requests}, max_seconds={max_seconds:.0f}s\n")
        sys.stderr.write(f"  • Options:          enrichment={enable_enrichment}, summary={enable_summary}, llm={enable_llm}\n")
        sys.stderr.write(f"{'='*80}\n\n")

    set_log_stream(sys.stderr)
    try:
        tasks = [
            process_single_orgnr(
                orgnr=orgnr,
                run_id=run_id,
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
        results = await asyncio.gather(*tasks, return_exceptions=True)

    finally:
        set_log_stream(None)
        await brreg_client.close()
        storage.close()

    # Collect envelopes in original input order (guarantee zero silent drops)
    envelopes = []
    for idx, res in enumerate(results):
        if isinstance(res, tuple) and len(res) == 3 and res[2] is not None:
            envelopes.append(res[2])
        elif isinstance(res, Exception):
            failed_org = orgnrs[idx] if idx < len(orgnrs) else "unknown"
            fallback_envelope = build_terminal_envelope(
                orgnr=failed_org,
                run_id=run_id,
                started_at=datetime.now(timezone.utc),
                completed_at=datetime.now(timezone.utc),
                error=f"Worker exception: {res}",
                terminal_state="failed",
            )
            envelopes.append(fallback_envelope)
            sys.stdout.write(json.dumps(fallback_envelope, ensure_ascii=False) + "\n")
            sys.stdout.flush()

    # Write envelopes to file targets
    target_files: list[Path] = []
    if output_dir is not None:
        out_str = str(output_dir)
        if out_str.endswith(".jsonl"):
            target_files.append(output_dir)
        else:
            output_dir.mkdir(parents=True, exist_ok=True)
            target_files.append(output_dir / "envelopes.jsonl")
    if envelopes_output is not None:
        target_files.append(envelopes_output)

    for target_path in target_files:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        with open(target_path, "w", encoding="utf-8") as f:
            for env in envelopes:
                f.write(json.dumps(env, ensure_ascii=False) + "\n")

    elapsed = budget.elapsed_seconds
    comp = budget.completed_count
    fail = budget.failed_count
    reqs = budget.request_count
    cost = budget.total_cost_usd

    cost_per_company = (cost / comp) if comp > 0 else 0.0
    projected_100_cost = cost_per_company * 100.0

    report = RunReport(
        timestamp=datetime.now(timezone.utc).isoformat(),
        total_requested=len(orgnrs),
        completed=comp,
        failed=fail,
        emitted_envelopes=len(envelopes),
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
        sys.stderr.write(f"\n{'='*80}\n")
        sys.stderr.write("RUN SUMMARY REPORT\n")
        sys.stderr.write(f"{'='*80}\n")
        sys.stderr.write(f"  • Completed Profiles:    {comp} / {len(orgnrs)}\n")
        sys.stderr.write(f"  • Emitted Envelopes:     {len(envelopes)} / {len(orgnrs)}\n")
        sys.stderr.write(f"  • Failed / Invalid:      {fail}\n")
        sys.stderr.write(f"  • Outbound Requests:     {reqs} / {max_requests} limit\n")
        sys.stderr.write(f"  • Wall-Clock Time:       {elapsed:.2f}s / {max_seconds:.0f}s cap\n")
        sys.stderr.write(f"  • Estimated Run Cost:    ${cost:.6f} USD\n")
        sys.stderr.write(f"  • Projected 100-Co Cost: ${projected_100_cost:.6f} USD\n")
        if budget.halt_reason:
            sys.stderr.write(f"  ⚠️ Circuit Breaker Halt: {budget.halt_reason}\n")
        sys.stderr.write(f"{'='*80}\n\n")

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
        "--organisations",
        type=str,
        dest="input",
        help="Path to a text/JSON/JSONL file containing organisation numbers.",
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
        help="Output JSONL file or directory to write company profiles & envelopes (default: 'profiles').",
    )
    parser.add_argument(
        "--envelopes-output",
        type=str,
        default=None,
        help="Explicit file path to write terminal envelopes JSONL.",
    )
    parser.add_argument(
        "--run-id",
        type=str,
        default=None,
        help="Evaluation run identifier (default: auto-generated timestamp).",
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
        default=True,
        help="Enable secondary source website enrichment (default: enabled).",
    )
    parser.add_argument(
        "--no-enrich",
        action="store_true",
        help="Disable secondary source website enrichment.",
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
        "--view",
        action="store_true",
        help="Render a human-readable, rich terminal profile view for inspection.",
    )
    parser.add_argument(
        "--html-report",
        type=str,
        default=None,
        help="Optional path to write a visual HTML dashboard report (e.g. 'reports/report.html').",
    )
    parser.add_argument(
        "--quiet",
        "-q",
        action="store_true",
        help="Suppress progress outputs on stderr.",
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
        try:
            orgnrs = read_organisation_inputs(args.input)
        except Exception as exc:
            sys.stderr.write(f"Error reading input: {exc}\n")
            return 1

    if not orgnrs:
        sys.stderr.write("Error: No organisation numbers provided.\n")
        return 1

    output_dir = Path(args.output) if args.output else None
    envelopes_out = Path(args.envelopes_output) if args.envelopes_output else None

    report = await run_pipeline(
        orgnrs=orgnrs,
        output_dir=output_dir,
        envelopes_output=envelopes_out,
        run_id=args.run_id,
        max_requests=args.max_requests,
        max_seconds=args.max_seconds,
        concurrency=args.concurrency,
        enable_enrichment=not args.no_enrich,
        enable_summary=not args.no_summary,
        enable_llm=args.llm,
        db_path=args.db,
        quiet=args.quiet,
    )

    # If --view was requested for a single company or batch, render readable profile
    if args.view and output_dir is not None:
        env_file = (output_dir if str(output_dir).endswith(".jsonl") else output_dir / "envelopes.jsonl")
        if env_file.exists():
            with open(env_file, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        try:
                            env_obj = json.loads(line)
                            rendered = render_terminal_profile(env_obj)
                            sys.stderr.write("\n" + rendered + "\n")
                        except Exception:
                            pass

    # If --html-report was requested, generate visual dashboard
    if args.html_report:
        from scripts.generate_report import generate_html_report
        env_file = (output_dir if str(output_dir).endswith(".jsonl") else output_dir / "envelopes.jsonl") if output_dir else None
        if env_file and env_file.exists():
            envs = []
            with open(env_file, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        try:
                            envs.append(json.loads(line))
                        except Exception:
                            pass
            generate_html_report(envs, Path(args.html_report))
            if not args.quiet:
                sys.stderr.write(f"✓ Visual HTML dashboard generated at: {args.html_report}\n")

    if args.json_report:
        report_path = Path(args.json_report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(report.model_dump_json(indent=2))

    return 0 if report.emitted_envelopes > 0 else 1


def main() -> None:
    exit_code = asyncio.run(async_main())
    sys.exit(exit_code)
