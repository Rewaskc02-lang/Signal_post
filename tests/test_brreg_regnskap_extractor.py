"""Unit tests for Regnskapsregisteret fact extractor."""

from datetime import date, datetime, timezone
from signalpost.extractors.brreg_regnskap import extract_regnskap_facts

ENDPOINT = "https://data.brreg.no/regnskapsregisteret/regnskap/923609016"


def test_extract_regnskap_facts_multi_year() -> None:
    filings = [
        # Year 2024
        {
            "id": 101,
            "regnskapsperiode": {"fraDato": "2024-01-01", "tilDato": "2024-12-31"},
            "valuta": "NOK",
            "resultatregnskapResultat": {
                "ordinaertResultatFoerSkattekostnad": 5000000.0,
                "driftsresultat": {
                    "driftsresultat": 6000000.0,
                    "driftsinntekter": {"sumDriftsinntekter": 50000000.0},
                },
            },
            "egenkapitalGjeld": {
                "egenkapital": {"sumEgenkapital": 20000000.0},
            },
        },
        # Year 2023
        {
            "id": 102,
            "regnskapsperiode": {"fraDato": "2023-01-01", "tilDato": "2023-12-31"},
            "valuta": "NOK",
            "resultatregnskapResultat": {
                "ordinaertResultatFoerSkattekostnad": 4200000.0,
                "driftsresultat": {
                    "driftsresultat": 5100000.0,
                    "driftsinntekter": {"sumDriftsinntekter": 45000000.0},
                },
            },
            "egenkapitalGjeld": {
                "egenkapital": {"sumEgenkapital": 18000000.0},
            },
        },
    ]

    retrieved_time = datetime(2026, 9, 16, 12, 0, 0, tzinfo=timezone.utc)
    facts = extract_regnskap_facts(filings, endpoint_url=ENDPOINT, retrieved_at=retrieved_time)

    # 4 facts per year * 2 years = 8 facts
    assert len(facts) == 8

    facts_2024 = [f for f in facts if f.as_of == date(2024, 12, 31)]
    facts_2023 = [f for f in facts if f.as_of == date(2023, 12, 31)]

    assert len(facts_2024) == 4
    assert len(facts_2023) == 4

    f2024_map = {f.field_name: f for f in facts_2024}
    assert f2024_map["revenue"].value == 50000000.0
    assert f2024_map["revenue"].unit == "NOK"
    assert f2024_map["operating_result"].value == 6000000.0
    assert f2024_map["ordinary_result_before_tax"].value == 5000000.0
    assert f2024_map["equity"].value == 20000000.0

    # Ensure no today's date leakage in as_of
    for f in facts:
        assert f.as_of in (date(2024, 12, 31), date(2023, 12, 31))
        assert f.source_name == "Brønnøysundregistrene – Regnskapsregisteret"
        assert f.source_url == ENDPOINT
        assert f.confidence == "official"


def test_extract_regnskap_facts_no_fabrication_on_missing_fields() -> None:
    filings = [
        {
            "id": 201,
            "regnskapsperiode": {"fraDato": "2024-01-01", "tilDato": "2024-12-31"},
            "valuta": "USD",
            "resultatregnskapResultat": {
                # Missing ordinary result before tax, operating result, and revenue
            },
            "egenkapitalGjeld": {
                "egenkapital": {"sumEgenkapital": 99000.0},
            },
        }
    ]

    facts = extract_regnskap_facts(filings, endpoint_url=ENDPOINT)

    # MUST ONLY extract equity, never fabricating 0 or None for missing fields
    assert len(facts) == 1
    assert facts[0].field_name == "equity"
    assert facts[0].value == 99000.0
    assert facts[0].unit == "USD"
    assert facts[0].as_of == date(2024, 12, 31)


def test_extract_regnskap_empty_or_malformed() -> None:
    assert extract_regnskap_facts([], endpoint_url=ENDPOINT) == []
    assert extract_regnskap_facts([{"missing_periode": True}], endpoint_url=ENDPOINT) == []
