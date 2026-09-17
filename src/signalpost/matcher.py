"""Profile builder orchestrating validation, registry fetches, and enrichment."""

import asyncio
from datetime import datetime, timezone
from typing import Any

from signalpost.brreg_client import (
    BrregClient,
    BrregError,
    InvalidOrgnrError,
    NotFoundError,
)
from signalpost.config import Settings, settings as default_settings
from signalpost.extractors.brreg_enhet import extract_enhet_facts
from signalpost.extractors.brreg_regnskap import extract_regnskap_facts
from signalpost.extractors.website_enrichment import (
    enrich_from_website,
    enrich_from_website_with_status,
)
from signalpost.models import CompanyFact, CompanyProfile
from signalpost.orgnr import sanitize_orgnr, validate_orgnr


def deduplicate_facts(facts: list[CompanyFact]) -> list[CompanyFact]:
    """Deduplicate facts having the same (field_name, as_of).

    Confidence priority: 'official' > 'verified_secondary' > 'unverified_secondary'.
    For ties, keeps the most recently retrieved fact.
    """
    priority = {
        "official": 3,
        "verified_secondary": 2,
        "unverified_secondary": 1,
    }

    fact_map: dict[tuple[str, Any], CompanyFact] = {}
    for fact in facts:
        key = (fact.field_name, fact.as_of)
        existing = fact_map.get(key)
        if existing is None:
            fact_map[key] = fact
        else:
            existing_score = priority.get(existing.confidence, 0)
            new_score = priority.get(fact.confidence, 0)
            if new_score > existing_score:
                fact_map[key] = fact
            elif new_score == existing_score:
                if fact.retrieved_at >= existing.retrieved_at:
                    fact_map[key] = fact

    return list(fact_map.values())


async def build_profile(
    orgnr: str,
    enable_website_enrichment: bool = False,
    client: BrregClient | None = None,
    settings: Settings | None = None,
) -> CompanyProfile:
    """Build a complete, provenance-backed CompanyProfile for an organisation.

    Execution Flow:
    1. Validates orgnr using MOD-11 checksum -> Fails fast before any network request.
    2. Concurrently fetches Enhetsregisteret and Regnskapsregisteret payloads.
    3. Handles 404 from Regnskapsregisteret gracefully (defaults to empty accounts list).
    4. Extracts atomic CompanyFact objects from registry responses with exact source URLs.
    5. Optionally enriches with verified secondary facts from the company website.
    6. Merges and deduplicates facts into a unified CompanyProfile.

    Args:
        orgnr: 9-digit Norwegian organisation number.
        enable_website_enrichment: Whether to fetch and verify company website.
        client: Optional BrregClient instance.
        settings: Optional custom Settings.

    Returns:
        A validated CompanyProfile containing atomic CompanyFact objects.

    Raises:
        InvalidOrgnrError: If orgnr fails checksum validation.
        NotFoundError: If orgnr is not registered in Enhetsregisteret.
        BrregError: On registry communication failures.
    """
    cleaned_orgnr = sanitize_orgnr(orgnr)

    # 1. Fail-fast validation: absolutely NO network calls if orgnr is invalid
    if not validate_orgnr(cleaned_orgnr):
        raise InvalidOrgnrError(f"Invalid organisation number: {orgnr}")

    cfg = settings or default_settings
    should_close_client = False
    brreg_client = client
    if brreg_client is None:
        brreg_client = BrregClient(settings=cfg)
        should_close_client = True

    try:
        enhet_url = f"{cfg.brreg_enhet_base_url}/{cleaned_orgnr}"
        regnskap_url = f"{cfg.brreg_regnskap_base_url}/{cleaned_orgnr}"

        # 2. Concurrently fetch Enhetsregisteret and Regnskapsregisteret
        enhet_task = brreg_client.fetch_enhet(cleaned_orgnr)
        regnskap_task = brreg_client.fetch_regnskap(cleaned_orgnr)

        # Regnskap 404 is normal for entities without public accounts (e.g. ENK, associations)
        enhet_data, regnskap_data = await asyncio.gather(
            enhet_task,
            _fetch_regnskap_safe(regnskap_task),
        )

        fetch_time = datetime.now(timezone.utc)

        # 3. Extract core registry facts
        all_facts: list[CompanyFact] = []
        enhet_facts = extract_enhet_facts(enhet_data, endpoint_url=enhet_url, retrieved_at=fetch_time)
        all_facts.extend(enhet_facts)

        if regnskap_data:
            regnskap_facts = extract_regnskap_facts(
                regnskap_data,
                endpoint_url=regnskap_url,
                retrieved_at=fetch_time,
            )
            all_facts.extend(regnskap_facts)

        # 4. Optional website enrichment with strict anti-hallucination verification
        source_statuses: dict[str, str] = {
            "enhetsregisteret": "available",
            "regnskapsregisteret": "available" if regnskap_data else "missing",
        }

        if enable_website_enrichment:
            website_val = next((f.value for f in enhet_facts if f.field_name == "website"), None)
            legal_name_val = next((f.value for f in enhet_facts if f.field_name == "legal_name"), "")

            if website_val and isinstance(website_val, str):
                enrichment_facts, web_status = await enrich_from_website_with_status(
                    website_url=website_val,
                    orgnr=cleaned_orgnr,
                    legal_name=legal_name_val,
                    client=brreg_client._client,
                )
                source_statuses["website"] = web_status
                all_facts.extend(enrichment_facts)
            else:
                source_statuses["website"] = "missing"

        # 5. Deduplicate and assemble profile
        deduped_facts = deduplicate_facts(all_facts)

        return CompanyProfile(
            orgnr=cleaned_orgnr,
            facts=deduped_facts,
            last_checked=fetch_time,
            source_statuses=source_statuses,
        )

    finally:
        if should_close_client:
            await brreg_client.close()


async def _fetch_regnskap_safe(task: Any) -> list[dict[str, Any]]:
    """Safely await regnskap fetch, returning empty list if not found (404) or unavailable."""
    try:
        return await task
    except (NotFoundError, BrregError):
        return []
