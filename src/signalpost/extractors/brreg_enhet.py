import hashlib
import json
from datetime import date, datetime, timezone
from typing import Any

from signalpost.models import CompanyFact

SOURCE_NAME_ENHET = "Brønnøysundregistrene – Enhetsregisteret"


def parse_date_safe(val: str | None) -> date | None:
    """Safely parse an ISO date string (YYYY-MM-DD)."""
    if not val or not isinstance(val, str):
        return None
    try:
        return date.fromisoformat(val[:10])
    except ValueError:
        return None


def format_address(addr: dict[str, Any] | None) -> dict[str, Any] | None:
    """Format an address dict into a clean, normalized structure."""
    if not isinstance(addr, dict) or not addr:
        return None

    street_lines = addr.get("adresse", [])
    if isinstance(street_lines, str):
        street_lines = [street_lines]

    return {
        "street": ", ".join(s.strip() for s in street_lines if s and s.strip()),
        "postal_code": addr.get("postnummer"),
        "city": addr.get("poststed"),
        "country": addr.get("land", "Norge"),
        "country_code": addr.get("landkode", "NO"),
        "municipality": addr.get("kommune"),
        "municipality_code": addr.get("kommunenummer"),
    }


def determine_status(raw: dict[str, Any]) -> str:
    """Determine the current legal status of the entity."""
    status_fields = (
        "slettedato",
        "konkurs",
        "underAvvikling",
        "underTvangsavviklingEllerTvangsopplosning",
    )
    if not any(field in raw for field in status_fields):
        return "not_reported"
    if raw.get("slettedato") is not None:
        return "deleted"
    if raw.get("konkurs", False) is True:
        return "bankrupt"
    if raw.get("underAvvikling", False) is True or raw.get(
        "underTvangsavviklingEllerTvangsopplosning", False
    ) is True:
        return "under_liquidation"
    return "active"


def extract_enhet_facts(
    raw_data: dict[str, Any],
    endpoint_url: str,
    retrieved_at: datetime | None = None,
    content_hash: str | None = None,
) -> list[CompanyFact]:
    """Extract official CompanyFact objects from an Enhetsregisteret response payload.

    Args:
        raw_data: The raw JSON dictionary returned by Brreg Enhetsregisteret.
        endpoint_url: The exact URL that was queried.
        retrieved_at: Timestamp when data was retrieved (defaults to UTC now).
        content_hash: Optional precomputed SHA-256 hash of the response content.

    Returns:
        List of CompanyFact instances with full provenance and confidence='official'.
    """
    facts: list[CompanyFact] = []
    fetch_time = retrieved_at or datetime.now(timezone.utc)

    # Compute SHA-256 content hash of the raw response payload if not provided
    if content_hash:
        hash_digest = content_hash
    else:
        payload_bytes = json.dumps(raw_data, sort_keys=True, default=str).encode("utf-8")
        hash_digest = hashlib.sha256(payload_bytes).hexdigest()

    # Base helper to append fact with standard provenance
    def add_fact(
        field_name: str,
        value: Any,
        unit: str | None = None,
        as_of: date | None = None,
    ) -> None:
        # Reject None, empty string, empty dict, empty list — but allow 0 and False
        if value is None:
            return
        if isinstance(value, (str, dict, list)) and not value:
            return
        facts.append(
            CompanyFact(
                field_name=field_name,
                value=value,
                unit=unit,
                source_name=SOURCE_NAME_ENHET,
                source_url=endpoint_url,
                as_of=as_of,
                retrieved_at=fetch_time,
                confidence="official",
                content_hash=hash_digest,
                extraction_method="official_api",
            )
        )

    # 1. Legal Name
    if navn := raw_data.get("navn"):
        add_fact("legal_name", str(navn).strip())

    # 2. Organisation Form
    if orgform := raw_data.get("organisasjonsform"):
        if isinstance(orgform, dict):
            if code := orgform.get("kode"):
                add_fact("org_form_code", str(code).strip())
            if desc := orgform.get("beskrivelse"):
                add_fact("org_form_description", str(desc).strip())

    # 3. Registration Date
    reg_date_str = raw_data.get("registreringsdatoEnhetsregisteret")
    reg_date = parse_date_safe(reg_date_str)
    if reg_date:
        add_fact("registration_date", reg_date.isoformat(), as_of=reg_date)

    # 4. Industry / NACE Code
    if nace := raw_data.get("naeringskode1"):
        if isinstance(nace, dict):
            if code := nace.get("kode"):
                add_fact("nace_code", str(code).strip())
            if desc := nace.get("beskrivelse"):
                add_fact("nace_description", str(desc).strip())

    if nace2 := raw_data.get("naeringskode2"):
        if isinstance(nace2, dict):
            if code2 := nace2.get("kode"):
                add_fact("secondary_nace_code", str(code2).strip())
            if desc2 := nace2.get("beskrivelse"):
                add_fact("secondary_nace_description", str(desc2).strip())

    if nace3 := raw_data.get("naeringskode3"):
        if isinstance(nace3, dict):
            if code3 := nace3.get("kode"):
                add_fact("tertiary_nace_code", str(code3).strip())
            if desc3 := nace3.get("beskrivelse"):
                add_fact("tertiary_nace_description", str(desc3).strip())

    # 5. Business Address & Atomic Address Fields
    if b_addr := format_address(raw_data.get("forretningsadresse")):
        add_fact("business_address", b_addr)
        if city := b_addr.get("city"):
            add_fact("city", str(city).strip())
        if p_code := b_addr.get("postal_code"):
            add_fact("postal_code", str(p_code).strip())
        if mun := b_addr.get("municipality"):
            add_fact("municipality", str(mun).strip())
        if mun_code := b_addr.get("municipality_code"):
            add_fact("municipality_code", str(mun_code).strip())
        if street := b_addr.get("street"):
            add_fact("street_address", str(street).strip())

    # 6. Postal Address
    if p_addr := format_address(raw_data.get("postadresse")):
        add_fact("postal_address", p_addr)
        # If city or postal_code wasn't available from business address, fallback to postal address
        if not b_addr:
            if p_city := p_addr.get("city"):
                add_fact("city", str(p_city).strip())
            if p_pcode := p_addr.get("postal_code"):
                add_fact("postal_code", str(p_pcode).strip())

    # 7. Employee Count
    if (ansatte := raw_data.get("antallAnsatte")) is not None:
        try:
            count = int(ansatte)
            add_fact("employee_count", count, unit="count")
        except (ValueError, TypeError):
            pass

    # 8. Website (Hjemmeside)
    if website := raw_data.get("hjemmeside"):
        add_fact("website", str(website).strip())

    # 9. Email (check both epost and epostadresse)
    epost = raw_data.get("epost") or raw_data.get("epostadresse")
    if epost:
        add_fact("email", str(epost).strip())

    # 10. Phone
    phone = raw_data.get("telefon") or raw_data.get("mobil")
    if phone:
        add_fact("phone", str(phone).strip())

    # 11. VAT Registered Flag
    if "registrertIMvaregisteret" in raw_data:
        add_fact("vat_registered", bool(raw_data["registrertIMvaregisteret"]))

    # 12. Current Status
    status = determine_status(raw_data)
    add_fact("status", status)

    # 13. Foundation / Incorporation Date (stiftelsesdato)
    if stift_str := raw_data.get("stiftelsesdato"):
        if stift_date := parse_date_safe(stift_str):
            add_fact("foundation_date", stift_date.isoformat(), as_of=stift_date)

    # 14. Share Capital (kapital)
    if kapital := raw_data.get("kapital"):
        if isinstance(kapital, dict):
            if belop := kapital.get("belop"):
                try:
                    cap_val = float(belop)
                    valuta = kapital.get("valuta", "NOK")
                    add_fact("share_capital", cap_val, unit=valuta)
                except (ValueError, TypeError):
                    pass

    # 15. Latest Accounting Year (sisteInnsendteAarsregnskap)
    if siste_regnskap := raw_data.get("sisteInnsendteAarsregnskap"):
        add_fact("latest_accounting_year", str(siste_regnskap).strip())

    # 16. Institutional Sector (institusjonellSektorkode)
    if sektor := raw_data.get("institusjonellSektorkode"):
        if isinstance(sektor, dict):
            if s_desc := sektor.get("beskrivelse"):
                add_fact("sector_description", str(s_desc).strip())
            if s_kode := sektor.get("kode"):
                add_fact("sector_code", str(s_kode).strip())

    # 17. Business Purpose / Activity (vedtektsfestetFormaal / aktivitet)
    purpose_lines = raw_data.get("vedtektsfestetFormaal") or raw_data.get("aktivitet")
    if purpose_lines:
        if isinstance(purpose_lines, list):
            joined_purpose = " ".join(str(p).strip() for p in purpose_lines if p)
        else:
            joined_purpose = str(purpose_lines).strip()
        if joined_purpose:
            add_fact("business_purpose", joined_purpose)

    # 18. Labeled Relationship: Parent Unit (Overordnet enhet)
    # Stored as an explicit labeled relationship fact, NOT merged into company facts
    if parent_orgnr := raw_data.get("overordnetEnhet"):
        add_fact("parent_orgnr", str(parent_orgnr).strip())

    # 19. Additional Registry Flags & Registrations
    if "registrertIForetaksregisteret" in raw_data and raw_data["registrertIForetaksregisteret"] is not None:
        add_fact("foretaksregister_registered", bool(raw_data["registrertIForetaksregisteret"]))

    if ft_date_str := raw_data.get("registreringsdatoForetaksregisteret"):
        if ft_date := parse_date_safe(ft_date_str):
            add_fact("foretaksregister_registration_date", ft_date.isoformat(), as_of=ft_date)

    if mva_date_str := raw_data.get("registreringsdatoMerverdiavgiftsregisteret"):
        if mva_date := parse_date_safe(mva_date_str):
            add_fact("mva_registration_date", mva_date.isoformat(), as_of=mva_date)

    if "registrertIFrivillighetsregisteret" in raw_data and raw_data["registrertIFrivillighetsregisteret"] is not None:
        add_fact("frivillighetsregister_registered", bool(raw_data["registrertIFrivillighetsregisteret"]))

    if "registrertIStiftelsesregisteret" in raw_data and raw_data["registrertIStiftelsesregisteret"] is not None:
        add_fact("stiftelsesregister_registered", bool(raw_data["registrertIStiftelsesregisteret"]))

    if slettedato_str := raw_data.get("slettedato"):
        if slett_date := parse_date_safe(slettedato_str):
            add_fact("dissolution_date", slett_date.isoformat(), as_of=slett_date)

    if lang := raw_data.get("maalform"):
        add_fact("language", str(lang).strip())

    return facts
