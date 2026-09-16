"""Extractor for annual financial accounts from Brønnøysundregistrene (Regnskapsregisteret)."""

from datetime import date, datetime, timezone
from typing import Any

from signalpost.models import CompanyFact

SOURCE_NAME_REGNSKAP = "Brønnøysundregistrene – Regnskapsregisteret"


def parse_date_safe(val: str | None) -> date | None:
    """Safely parse an ISO date string (YYYY-MM-DD)."""
    if not val or not isinstance(val, str):
        return None
    try:
        return date.fromisoformat(val[:10])
    except ValueError:
        return None


def extract_regnskap_facts(
    filings: list[dict[str, Any]],
    endpoint_url: str,
    retrieved_at: datetime | None = None,
) -> list[CompanyFact]:
    """Extract official financial CompanyFact objects from Regnskapsregisteret filings.

    Strict Extraction Guarantees:
    - Exactly ONE fact per (field_name, fiscal_year) pair.
    - as_of is set to the exact fiscal year end date (tilDato), NEVER retrieval date.
    - If a field is omitted in a filing, it is skipped without fabricating zeros or nulls.
    - Exact numeric values and currency units reported by Brreg are preserved verbatim.

    Args:
        filings: List of yearly filing dicts returned by Brreg.
        endpoint_url: The exact API URL that was queried.
        retrieved_at: Timestamp when data was retrieved (defaults to UTC now).

    Returns:
        List of CompanyFact instances with full provenance and confidence='official'.
    """
    facts: list[CompanyFact] = []
    fetch_time = retrieved_at or datetime.now(timezone.utc)

    for filing in filings:
        if not isinstance(filing, dict):
            continue

        # Extract fiscal year end date (tilDato)
        periode = filing.get("regnskapsperiode", {})
        til_dato_str = periode.get("tilDato") if isinstance(periode, dict) else None
        as_of_date = parse_date_safe(til_dato_str)

        if not as_of_date:
            # Cannot accurately date the financial fact without fiscal period end
            continue

        currency = filing.get("valuta", "NOK")

        # Helpers for safe nested retrieval
        resultat = filing.get("resultatregnskapResultat", {})
        if not isinstance(resultat, dict):
            resultat = {}

        driftsresultat_section = resultat.get("driftsresultat", {})
        if not isinstance(driftsresultat_section, dict):
            driftsresultat_section = {}

        driftsinntekter_section = driftsresultat_section.get("driftsinntekter", {})
        if not isinstance(driftsinntekter_section, dict):
            driftsinntekter_section = {}

        egenkapital_gjeld = filing.get("egenkapitalGjeld", {})
        if not isinstance(egenkapital_gjeld, dict):
            egenkapital_gjeld = {}

        egenkapital_section = egenkapital_gjeld.get("egenkapital", {})
        if not isinstance(egenkapital_section, dict):
            egenkapital_section = {}

        def add_financial_fact(field_name: str, value: Any) -> None:
            if value is not None and isinstance(value, (int, float)):
                facts.append(
                    CompanyFact(
                        field_name=field_name,
                        value=value,
                        unit=currency,
                        source_name=SOURCE_NAME_REGNSKAP,
                        source_url=endpoint_url,
                        as_of=as_of_date,
                        retrieved_at=fetch_time,
                        confidence="official",
                    )
                )

        # 1. Revenue (sumDriftsinntekter or salgsinntekter)
        revenue = driftsinntekter_section.get("sumDriftsinntekter")
        if revenue is None:
            revenue = driftsinntekter_section.get("salgsinntekter")
        add_financial_fact("revenue", revenue)

        # 2. Operating Result (driftsresultat)
        operating_result = driftsresultat_section.get("driftsresultat")
        add_financial_fact("operating_result", operating_result)

        # 3. Ordinary Result Before Tax (ordinaertResultatFoerSkattekostnad)
        pre_tax = resultat.get("ordinaertResultatFoerSkattekostnad")
        add_financial_fact("ordinary_result_before_tax", pre_tax)

        # 4. Total Equity (sumEgenkapital)
        equity = egenkapital_section.get("sumEgenkapital")
        add_financial_fact("equity", equity)

    return facts
