"""Unit tests for matcher.build_profile orchestration."""

from datetime import date, datetime, timezone
import pytest
import respx

from signalpost.brreg_client import InvalidOrgnrError, NotFoundError
from signalpost.config import Settings
from signalpost.matcher import build_profile, deduplicate_facts
from signalpost.models import CompanyFact


@pytest.fixture
def test_settings() -> Settings:
    return Settings(
        brreg_enhet_base_url="https://mock.brreg.no/enheter",
        brreg_regnskap_base_url="https://mock.brreg.no/regnskap",
        brreg_request_timeout_seconds=5.0,
        brreg_max_concurrency=5,
        brreg_max_retries=2,
        brreg_retry_backoff_factor=0.01,
    )


@pytest.mark.asyncio
async def test_build_profile_clean_company(test_settings: Settings) -> None:
    orgnr = "923609016"

    enhet_payload = {
        "organisasjonsnummer": orgnr,
        "navn": "EQUINOR ASA",
        "organisasjonsform": {"kode": "ASA", "beskrivelse": "Allmennaksjeselskap"},
        "registreringsdatoEnhetsregisteret": "1995-03-12",
        "antallAnsatte": 21000,
        "hjemmeside": "https://www.equinor.com",
        "registrertIMvaregisteret": True,
        "konkurs": False,
    }

    regnskap_payload = [
        {
            "id": 1,
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
        }
    ]

    website_html = """
    <html><head><meta name="description" content="Official Equinor website."></head>
    <body>Equinor ASA - Org 923609016</body></html>
    """

    with respx.mock(assert_all_called=False) as respx_mock:
        respx_mock.get(f"{test_settings.brreg_enhet_base_url}/{orgnr}").respond(
            status_code=200, json=enhet_payload
        )
        respx_mock.get(f"{test_settings.brreg_regnskap_base_url}/{orgnr}").respond(
            status_code=200, json=regnskap_payload
        )
        respx_mock.get("https://www.equinor.com/robots.txt").respond(
            status_code=200, text="User-agent: *\nAllow: /"
        )
        respx_mock.get("https://www.equinor.com").respond(
            status_code=200, text=website_html, headers={"content-type": "text/html"}
        )

        profile = await build_profile(
            orgnr,
            enable_website_enrichment=True,
            settings=test_settings,
        )

    assert profile.orgnr == orgnr
    assert profile.last_checked is not None

    facts_dict = {f.field_name: f for f in profile.facts}

    # Enhet facts
    assert facts_dict["legal_name"].value == "EQUINOR ASA"
    assert facts_dict["org_form_code"].value == "ASA"
    assert facts_dict["employee_count"].value == 21000

    # Regnskap facts with fiscal year as_of
    assert facts_dict["revenue"].value == 50000000.0
    assert facts_dict["revenue"].as_of == date(2024, 12, 31)
    assert facts_dict["revenue"].unit == "NOK"

    # Website enrichment fact
    assert facts_dict["website_description"].value == "Official Equinor website."
    assert facts_dict["website_description"].confidence == "unverified_secondary"

    # Guarantee: Every single fact has source_url and retrieved_at populated
    for fact in profile.facts:
        assert fact.source_url is not None and len(fact.source_url) > 0
        assert fact.retrieved_at is not None
        assert fact.confidence in ("official", "unverified_secondary")


@pytest.mark.asyncio
async def test_build_profile_missing_regnskap_filings(test_settings: Settings) -> None:
    orgnr = "984851006"

    enhet_payload = {
        "organisasjonsnummer": orgnr,
        "navn": "DNB BANK ASA",
        "organisasjonsform": {"kode": "ASA", "beskrivelse": "Allmennaksjeselskap"},
        "registrertIMvaregisteret": True,
        "konkurs": False,
    }

    with respx.mock(assert_all_called=False) as respx_mock:
        respx_mock.get(f"{test_settings.brreg_enhet_base_url}/{orgnr}").respond(
            status_code=200, json=enhet_payload
        )
        # Regnskap returns 404 (no filings)
        respx_mock.get(f"{test_settings.brreg_regnskap_base_url}/{orgnr}").respond(
            status_code=404, json={"melding": "Ingen regnskap funnet"}
        )

        profile = await build_profile(
            orgnr,
            enable_website_enrichment=False,
            settings=test_settings,
        )

    assert profile.orgnr == orgnr
    # Should still contain Enhet facts without error
    legal_name = next(f for f in profile.facts if f.field_name == "legal_name")
    assert legal_name.value == "DNB BANK ASA"
    # No financial facts
    assert not any(f.field_name in ("revenue", "equity") for f in profile.facts)


@pytest.mark.asyncio
async def test_build_profile_unverified_website_yields_zero_enrichment(test_settings: Settings) -> None:
    orgnr = "923609016"

    enhet_payload = {
        "organisasjonsnummer": orgnr,
        "navn": "EQUINOR ASA",
        "hjemmeside": "https://www.unrelated-site.com",
        "registrertIMvaregisteret": True,
    }

    with respx.mock(assert_all_called=False) as respx_mock:
        respx_mock.get(f"{test_settings.brreg_enhet_base_url}/{orgnr}").respond(
            status_code=200, json=enhet_payload
        )
        respx_mock.get(f"{test_settings.brreg_regnskap_base_url}/{orgnr}").respond(
            status_code=404
        )
        respx_mock.get("https://www.unrelated-site.com/robots.txt").respond(
            status_code=200, text="User-agent: *\nAllow: /"
        )
        respx_mock.get("https://www.unrelated-site.com").respond(
            status_code=200,
            text="<html><body>Welcome to an unrelated bakery shop!</body></html>",
            headers={"content-type": "text/html"},
        )
        respx_mock.get("https://www.unrelated-site.com/om-oss").respond(status_code=404)
        respx_mock.get("https://www.unrelated-site.com/om").respond(status_code=404)
        respx_mock.get("https://www.unrelated-site.com/about").respond(status_code=404)
        respx_mock.get("https://www.unrelated-site.com/about-us").respond(status_code=404)
        respx_mock.get("https://www.unrelated-site.com/kontakt").respond(status_code=404)
        respx_mock.get("https://www.unrelated-site.com/contact").respond(status_code=404)

        profile = await build_profile(
            orgnr,
            enable_website_enrichment=True,
            settings=test_settings,
        )

    # NO website facts should be added because identity verification failed
    assert not any(f.field_name == "website_description" for f in profile.facts)
    assert not any(f.confidence == "unverified_secondary" for f in profile.facts)


@pytest.mark.asyncio
async def test_build_profile_invalid_orgnr_fails_before_network(test_settings: Settings) -> None:
    invalid_orgnr = "923609017"  # Bad checksum

    with respx.mock(assert_all_called=False) as respx_mock:
        with pytest.raises(InvalidOrgnrError):
            await build_profile(invalid_orgnr, settings=test_settings)

        # Zero network calls allowed
        assert len(respx_mock.calls) == 0


def test_deduplicate_facts() -> None:
    now = datetime.now()
    fact1 = CompanyFact(
        field_name="revenue",
        value=100.0,
        source_name="Official Source",
        source_url="https://source1.no",
        as_of=date(2024, 12, 31),
        confidence="official",
        retrieved_at=now,
    )
    fact2 = CompanyFact(
        field_name="revenue",
        value=99.0,
        source_name="Secondary Source",
        source_url="https://source2.no",
        as_of=date(2024, 12, 31),
        confidence="unverified_secondary",
        retrieved_at=now,
    )
    fact3 = CompanyFact(
        field_name="revenue",
        value=90.0,
        source_name="Official Source",
        source_url="https://source1.no",
        as_of=date(2023, 12, 31),  # Different as_of date
        confidence="official",
        retrieved_at=now,
    )

    deduped = deduplicate_facts([fact2, fact1, fact3])
    assert len(deduped) == 2
    # The official fact takes priority over unverified_secondary for 2024
    fact_2024 = next(f for f in deduped if f.as_of == date(2024, 12, 31))
    assert fact_2024.value == 100.0
    assert fact_2024.confidence == "official"
