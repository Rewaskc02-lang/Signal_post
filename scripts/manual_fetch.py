#!/usr/bin/env python3
"""Manual verification script for Signalpost Phase 3: Ingestion, Diffing, and Explanations.

Usage:
    python scripts/manual_fetch.py [ORGNR1 ORGNR2 ...] [--enrich] [--summary] [--llm] [--edit-test ORGNR]

Features:
    - Builds/reconciles profiles from Brreg APIs.
    - Demonstrates diff detection (NEW vs CONFIRMED vs CHANGED).
    - Generates per-fact verifiable explanation notes.
    - Generates natural-language company summary with token cost telemetry.
"""

import asyncio
import json
import sys
from signalpost.brreg_client import InvalidOrgnrError, NotFoundError, BrregError
from signalpost.differ import diff_and_update_profile
from signalpost.explain import generate_company_summary, generate_fact_note
from signalpost.matcher import build_profile
from signalpost.models import CompanyFact, CompanyProfile
from signalpost.storage import Storage

DEFAULT_ORGNRS = [
    "923609016",  # Equinor ASA
    "984851006",  # DNB Bank ASA
    "986228608",  # Yara International ASA
    "910747711",  # Orkla ASA
    "984661185",  # Posten Bring AS
]


async def process_organisation(
    orgnr: str,
    storage: Storage,
    enable_enrichment: bool = False,
    enable_summary: bool = True,
    enable_llm: bool = False,
) -> None:
    print(f"\n{'='*80}")
    print(f"PIPELINE RUN FOR ORGANISATION: {orgnr}")
    print(f"{'='*80}")

    try:
        # 1. Fetch previous stored profile (if any)
        prev_profile = storage.get_profile(orgnr)
        status_label = "EXISTING IN DB" if prev_profile else "NEW INGESTION"
        print(f"[*] State: {status_label}")

        # 2. Build fresh profile from registry APIs
        fresh_profile = await build_profile(
            orgnr=orgnr,
            enable_website_enrichment=enable_enrichment,
        )

        # 3. Diff and update state
        updated_profile, diff_summary = diff_and_update_profile(
            current_profile=fresh_profile,
            previous_profile=prev_profile,
            storage=storage,
        )

        # 4. Print Reconciliation Summary
        print(f"\n--- [Reconciliation Result] ---")
        print(f"  • New facts:       {diff_summary.new_count}")
        print(f"  • Changed facts:   {diff_summary.changed_count}")
        print(f"  • Confirmed facts: {diff_summary.confirmed_count}")
        print(f"  • Last checked:    {diff_summary.last_checked.isoformat()}")
        print(f"  • Last changed:    {diff_summary.last_changed.isoformat() if diff_summary.last_changed else 'None'}")

        if diff_summary.changed_count > 0:
            print("\n  ⚠️ Changed Facts Detected:")
            for chg in diff_summary.changes:
                if chg.status == "changed":
                    print(f"    - {chg.field_name} (as_of={chg.as_of}): {chg.old_value} -> {chg.new_value} {chg.unit or ''}")

        # 5. Print Verified Facts with Explanations
        print(f"\n--- [Fact Lineage & Explanations ({len(updated_profile.facts)} facts)] ---")
        for fact in updated_profile.facts:
            note = generate_fact_note(fact)
            as_of_str = f" [as_of {fact.as_of}]" if fact.as_of else ""
            unit_str = f" {fact.unit}" if fact.unit else ""
            print(f"  ▸ {fact.field_name}{as_of_str}: {fact.value}{unit_str}")
            print(f"    ↳ Note:   {note}")
            print(f"    ↳ Source: {fact.source_name} ({fact.confidence})")
            print(f"    ↳ URL:    {fact.source_url}")

        # 6. Natural Language Summary
        if enable_summary:
            summary_res = await generate_company_summary(
                profile=updated_profile,
                enable_llm=enable_llm,
            )
            llm_tag = "LLM Generated" if summary_res.is_llm_generated else "Deterministic Template"
            print(f"\n--- [Executive Summary ({llm_tag})] ---")
            print(f"{summary_res.summary_text}")
            if summary_res.is_llm_generated:
                print(f"\n  [Telemetry] Prompt Tokens: {summary_res.prompt_tokens} | Completion Tokens: {summary_res.completion_tokens} | Est. Cost: ${summary_res.estimated_cost_usd:.6f}")

    except InvalidOrgnrError as e:
        print(f"❌ Validation Error: {e}")
    except NotFoundError as e:
        print(f"⚠️ NotFound: {e}")
    except BrregError as e:
        print(f"❌ BrregError: {e}")


def simulate_edit_test(orgnr: str, storage: Storage) -> None:
    """Manually modify a stored profile's revenue to demonstrate diff detection."""
    print(f"\n{'='*80}")
    print(f"SIMULATING MANUAL REVENUE EDIT FOR: {orgnr}")
    print(f"{'='*80}")

    profile = storage.get_profile(orgnr)
    if not profile:
        print(f"❌ No stored profile found for {orgnr}. Run regular ingestion first.")
        return

    # Modify revenue or add simulated change
    modified_facts = []
    found_revenue = False
    for f in profile.facts:
        if f.field_name == "revenue":
            found_revenue = True
            # Halve revenue to simulate a change
            new_val = f.value * 0.5 if isinstance(f.value, (int, float)) else 12345.0
            modified_facts.append(
                CompanyFact(
                    field_name=f.field_name,
                    value=new_val,
                    unit=f.unit,
                    source_name=f.source_name,
                    source_url=f.source_url,
                    as_of=f.as_of,
                    confidence=f.confidence,
                )
            )
        else:
            modified_facts.append(f)

    if not found_revenue:
        # If no revenue, add a fake revenue fact
        modified_facts.append(
            CompanyFact(
                field_name="revenue",
                value=999999.0,
                unit="NOK",
                source_name="Manual Simulation",
                source_url="https://test.local",
                confidence="official",
            )
        )

    edited_profile = CompanyProfile(
        orgnr=profile.orgnr,
        facts=modified_facts,
        last_checked=profile.last_checked,
        last_changed=profile.last_changed,
    )
    storage.save_profile(edited_profile)
    print(f"✅ Stored profile modified. Re-running the pipeline will now detect the difference!")


async def main() -> None:
    args = sys.argv[1:]
    enable_enrichment = "--enrich" in args
    enable_summary = "--summary" in args or True
    enable_llm = "--llm" in args

    db_path = "signalpost.db"
    if "--db" in args:
        idx = args.index("--db")
        if idx + 1 < len(args):
            db_path = args[idx + 1]

    storage = Storage(db_path=db_path)

    if "--edit-test" in args:
        idx = args.index("--edit-test")
        orgnr = args[idx + 1] if idx + 1 < len(args) else "923609016"
        simulate_edit_test(orgnr, storage)
        storage.close()
        return

    orgnrs = [a for a in args if not a.startswith("--") and a != db_path] or DEFAULT_ORGNRS

    print(f"Starting Signalpost pipeline for {len(orgnrs)} organisations (db='{db_path}', enrichment={enable_enrichment}, llm={enable_llm})")

    for orgnr in orgnrs:
        await process_organisation(
            orgnr=orgnr,
            storage=storage,
            enable_enrichment=enable_enrichment,
            enable_summary=enable_summary,
            enable_llm=enable_llm,
        )

    storage.close()


if __name__ == "__main__":
    asyncio.run(main())
