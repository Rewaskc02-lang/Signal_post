"""Official fact extractor for Brønnøysundregistrene (Brreg) Roller API."""

from datetime import datetime, timezone
import hashlib
import json
from typing import Any

from signalpost.models import CompanyFact

SOURCE_NAME_ROLLER = "Brønnøysundregistrene – Enhetsregisteret (Roller)"


def extract_roller_facts(
    raw_data: dict[str, Any] | None,
    endpoint_url: str,
    retrieved_at: datetime | None = None,
    content_hash: str | None = None,
) -> list[CompanyFact]:
    """Extract official governance, leadership, and audit CompanyFact objects from Brreg Roller API.

    Extracts:
    - ceo / daglig leder (Managing Director / Chief Executive Officer)
    - board_chair / styrets leder (Chairperson of the Board of Directors)
    - auditor / revisor (Registered statutory auditor)
    - accountant / regnskapsforer (Authorized accounting firm / accountant)

    Args:
        raw_data: The raw JSON dictionary returned by Brreg Roller API.
        endpoint_url: The exact URL queried (https://data.brreg.no/enhetsregisteret/api/enheter/{orgnr}/roller).
        retrieved_at: Timestamp when data was retrieved (defaults to UTC now).
        content_hash: Optional precomputed SHA-256 hash of the response content.

    Returns:
        List of CompanyFact instances with full provenance and confidence='official'.
    """
    if not raw_data or not isinstance(raw_data, dict):
        return []

    facts: list[CompanyFact] = []
    fetch_time = retrieved_at or datetime.now(timezone.utc)

    if content_hash:
        hash_digest = content_hash
    else:
        payload_bytes = json.dumps(raw_data, sort_keys=True, default=str).encode("utf-8")
        hash_digest = hashlib.sha256(payload_bytes).hexdigest()

    def add_fact(field_name: str, value: Any) -> None:
        if value:
            facts.append(
                CompanyFact(
                    field_name=field_name,
                    value=value,
                    unit=None,
                    source_name=SOURCE_NAME_ROLLER,
                    source_url=endpoint_url,
                    as_of=None,
                    retrieved_at=fetch_time,
                    confidence="official",
                    content_hash=hash_digest,
                    extraction_method="official_api",
                )
            )

    extracted_keys: set[str] = set()

    for rg in raw_data.get("rollegrupper", []):
        if not isinstance(rg, dict):
            continue
        for item in rg.get("roller", []):
            if not isinstance(item, dict):
                continue
            r_desc = item.get("type", {}).get("beskrivelse", "")

            # Person name resolution (fornavn + mellomnavn + etternavn)
            p = item.get("person", {}).get("navn", {})
            name_parts = [p.get("fornavn", ""), p.get("mellomnavn", ""), p.get("etternavn", "")]
            name = " ".join(part.strip() for part in name_parts if part and part.strip())

            # Enhet name resolution (for corporate entities like audit firms)
            if not name:
                e_val = item.get("enhet", {}).get("navn", "")
                if isinstance(e_val, list):
                    name = " ".join(str(x).strip() for x in e_val if x)
                else:
                    name = str(e_val).strip()

            if not name:
                continue

            r_lower = r_desc.lower()
            if "daglig leder" in r_lower and "ceo" not in extracted_keys:
                add_fact("ceo", name)
                extracted_keys.add("ceo")
            elif "nestleder" in r_lower and "deputy_chair" not in extracted_keys:
                add_fact("deputy_chair", name)
                extracted_keys.add("deputy_chair")
            elif "leder" in r_lower and "styre" in r_lower and "board_chair" not in extracted_keys:
                add_fact("board_chair", name)
                extracted_keys.add("board_chair")
            elif "revisor" in r_lower and "auditor" not in extracted_keys:
                add_fact("auditor", name)
                extracted_keys.add("auditor")
            elif "regnskapsfører" in r_lower and "accountant" not in extracted_keys:
                add_fact("accountant", name)
                extracted_keys.add("accountant")
            elif ("innehaver" in r_lower or "eier" in r_lower) and "proprietor" not in extracted_keys:
                add_fact("proprietor", name)
                extracted_keys.add("proprietor")
            elif "deltaker" in r_lower and "partner" not in extracted_keys:
                add_fact("partner", name)
                extracted_keys.add("partner")
            elif "kontaktperson" in r_lower and "contact_person" not in extracted_keys:
                add_fact("contact_person", name)
                extracted_keys.add("contact_person")
            elif "styremedlem" in r_lower:
                add_fact("board_member", name)
            elif "varamedlem" in r_lower:
                add_fact("deputy_board_member", name)

    return facts
