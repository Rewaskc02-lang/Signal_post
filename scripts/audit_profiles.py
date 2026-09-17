#!/usr/bin/env python3
"""Standalone automated quality audit for Signalpost profiles.

Verifies:
a) every fact has non-null source_url, as_of or retrieved_at, and content_hash
b) executive_summary (if present) contains no numeric value that doesn't appear in that same profile's fact list
c) legal_name and orgnr are both non-empty
d) no fact's source touches a restricted domain (linkedin.com, glassdoor.com, indeed.com, facebook.com/meta.com, google.com)
"""

import glob
import json
import re
from pathlib import Path
import sys

from signalpost.models import CompanyFact, CompanyProfile
from signalpost.explain import generate_deterministic_summary

RESTRICTED_DOMAINS = [
    "linkedin.com",
    "glassdoor.com",
    "indeed.com",
    "facebook.com",
    "meta.com",
    "google.com",
]


def audit_profiles(profiles_dir: str = "profiles") -> dict:
    profile_paths = sorted(glob.glob(f"{profiles_dir}/*.json"))
    total = len(profile_paths)
    if total == 0:
        print(f"ERROR: No profile JSON files found in '{profiles_dir}'!")
        sys.exit(1)

    print(f"[*] Starting quality audit on {total} profile files in '{profiles_dir}'...")

    passed = 0
    auto_fixed = 0
    failed_a = []
    failed_c = []
    failed_d = []
    fixed_b = []

    for path in profile_paths:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        profile_failed_stop = False
        orgnr = str(data.get("orgnr", "")).strip()
        facts = data.get("facts", [])
        summary = data.get("executive_summary")

        # Check (c): legal_name and orgnr are both non-empty
        legal_name = None
        for fact in facts:
            if fact.get("field_name") == "legal_name" and fact.get("value"):
                legal_name = str(fact["value"]).strip()
                break
        if not legal_name and data.get("legal_name"):
            legal_name = str(data["legal_name"]).strip()

        if not orgnr or not legal_name:
            failed_c.append((path, orgnr, legal_name))
            profile_failed_stop = True

        # Build fact numbers / tokens for Check (b)
        facts_text_parts = [orgnr]
        for fact in facts:
            # Check (a): every fact has non-null source_url, as_of or retrieved_at, and content_hash
            src_url = fact.get("source_url")
            as_of = fact.get("as_of")
            retrieved_at = fact.get("retrieved_at")
            content_hash = fact.get("content_hash")

            if not src_url or not (as_of or retrieved_at) or not content_hash:
                failed_a.append((path, fact.get("field_name"), src_url, as_of, retrieved_at, content_hash))
                profile_failed_stop = True

            # Check (d): no fact's source touches a restricted domain
            if src_url:
                for rd in RESTRICTED_DOMAINS:
                    if rd in src_url.lower():
                        failed_d.append((path, fact.get("field_name"), src_url, rd))
                        profile_failed_stop = True

            val = fact.get("value")
            if val is not None:
                facts_text_parts.append(str(val))
                if isinstance(val, (int, float)):
                    facts_text_parts.append(f"{val:,}")
                    facts_text_parts.append(str(int(val)))
            if as_of:
                facts_text_parts.append(str(as_of))
                facts_text_parts.append(str(as_of)[:4])

        # Check (b): numeric tripwire in executive_summary
        needed_fix = False
        if summary and not profile_failed_stop:
            facts_blob = " ".join(facts_text_parts)
            digits_in_facts = set(re.findall(r"\d+", facts_blob))
            numbers_in_summary = re.findall(r"\b\d[\d,.]*\b", summary)

            unmatched_numbers = []
            for num in numbers_in_summary:
                clean_num = re.sub(r"[,.]", "", num)
                if clean_num and clean_num not in digits_in_facts:
                    unmatched_numbers.append(num)

            if unmatched_numbers:
                # Check (b) failure: auto-fix by regenerating that profile's summary
                # with the deterministic template fallback
                needed_fix = True
                p_obj = CompanyProfile(**data)
                new_summary = generate_deterministic_summary(p_obj)
                data["executive_summary"] = new_summary
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                fixed_b.append((path, unmatched_numbers, new_summary))
                auto_fixed += 1

        if not profile_failed_stop:
            if not needed_fix:
                passed += 1

    print("\n" + "=" * 60)
    print("QUALITY AUDIT SUMMARY REPORT")
    print("=" * 60)
    print(f"Total Profiles Audited: {total}")
    print(f"Passed Cleanly        : {passed}")
    print(f"Auto-Fixed (Check b)  : {auto_fixed}")
    print(f"Failed (Check a)      : {len(failed_a)}")
    print(f"Failed (Check c)      : {len(failed_c)}")
    print(f"Failed (Check d)      : {len(failed_d)}")
    print("=" * 60)

    if fixed_b:
        print("\nProfiles Auto-Fixed under Check (b):")
        for p, unrec, new_s in fixed_b:
            print(f" - {p}: Fabricated numbers {unrec} -> regenerated deterministic summary.")

    if failed_a or failed_c or failed_d:
        print("\n❌ STOP CONDITION TRIGGERED: Irrecoverable quality audit failure:")
        if failed_a:
            print(f" - Check (a) Failures ({len(failed_a)}): {failed_a[:5]}")
        if failed_c:
            print(f" - Check (c) Failures ({len(failed_c)}): {failed_c[:5]}")
        if failed_d:
            print(f" - Check (d) Failures ({len(failed_d)}): {failed_d[:5]}")
        return {
            "total": total,
            "passed": passed,
            "auto_fixed": auto_fixed,
            "failed_a": len(failed_a),
            "failed_c": len(failed_c),
            "failed_d": len(failed_d),
            "stop_triggered": True,
        }

    print("\n✅ ALL AUDIT CHECKS PASSED: 100% compliant with source, fact, and fabrication policies.")
    return {
        "total": total,
        "passed": passed,
        "auto_fixed": auto_fixed,
        "failed_a": 0,
        "failed_c": 0,
        "failed_d": 0,
        "stop_triggered": False,
    }


if __name__ == "__main__":
    res = audit_profiles()
    if res["stop_triggered"]:
        sys.exit(1)
    sys.exit(0)
