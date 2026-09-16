"""Unit tests for Enhetsregisteret fact extractor."""

from datetime import date, datetime, timezone
from signalpost.extractors.brreg_enhet import (
    determine_status,
    extract_enhet_facts,
    format_address,
)

ENDPOINT = "https://data.brreg.no/enhetsregisteret/api/enheter/923609016"


def test_extract_enhet_facts_complete() -> None:
    raw_data = {
        "organisasjonsnummer": "923609016",
        "navn": "EQUINOR ASA",
        "organisasjonsform": {
            "kode": "ASA",
            "beskrivelse": "Allmennaksjeselskap",
        },
        "registreringsdatoEnhetsregisteret": "1995-03-12",
        "naeringskode1": {
            "kode": "06.100",
            "beskrivelse": "Utvinning av råolje",
        },
        "forretningsadresse": {
            "land": "Norge",
            "landkode": "NO",
            "postnummer": "4035",
            "poststed": "STAVANGER",
            "adresse": ["Forusbeen 50"],
            "kommune": "STAVANGER",
            "kommunenummer": "1103",
        },
        "postadresse": {
            "land": "Norge",
            "landkode": "NO",
            "postnummer": "4035",
            "poststed": "STAVANGER",
            "adresse": ["Postboks 8500"],
            "kommune": "STAVANGER",
            "kommunenummer": "1103",
        },
        "antallAnsatte": 21272,
        "hjemmeside": "www.equinor.com",
        "epost": "contact@equinor.com",
        "telefon": "51 99 00 00",
        "registrertIMvaregisteret": True,
        "konkurs": False,
        "underAvvikling": False,
        "underTvangsavviklingEllerTvangsopplosning": False,
    }

    retrieved_time = datetime(2026, 9, 16, 12, 0, 0, tzinfo=timezone.utc)
    facts = extract_enhet_facts(raw_data, endpoint_url=ENDPOINT, retrieved_at=retrieved_time)

    facts_by_field = {f.field_name: f for f in facts}

    # Verify all 14 facts
    assert facts_by_field["legal_name"].value == "EQUINOR ASA"
    assert facts_by_field["org_form_code"].value == "ASA"
    assert facts_by_field["org_form_description"].value == "Allmennaksjeselskap"
    assert facts_by_field["registration_date"].value == "1995-03-12"
    assert facts_by_field["registration_date"].as_of == date(1995, 3, 12)
    assert facts_by_field["nace_code"].value == "06.100"
    assert facts_by_field["nace_description"].value == "Utvinning av råolje"
    assert facts_by_field["employee_count"].value == 21272
    assert facts_by_field["employee_count"].unit == "count"
    assert facts_by_field["website"].value == "www.equinor.com"
    assert facts_by_field["email"].value == "contact@equinor.com"
    assert facts_by_field["phone"].value == "51 99 00 00"
    assert facts_by_field["vat_registered"].value is True
    assert facts_by_field["status"].value == "active"

    # Business address verification
    b_addr = facts_by_field["business_address"].value
    assert b_addr["street"] == "Forusbeen 50"
    assert b_addr["postal_code"] == "4035"
    assert b_addr["city"] == "STAVANGER"
    assert b_addr["country_code"] == "NO"

    # All facts must carry strict provenance
    for f in facts:
        assert f.source_url == ENDPOINT
        assert f.source_name == "Brønnøysundregistrene – Enhetsregisteret"
        assert f.confidence == "official"
        assert f.retrieved_at == retrieved_time


def test_extract_enhet_facts_sparse() -> None:
    raw_data = {
        "organisasjonsnummer": "999999999",
        "navn": "MINIMAL ENK",
        "registrertIMvaregisteret": False,
    }

    facts = extract_enhet_facts(raw_data, endpoint_url=ENDPOINT)
    facts_by_field = {f.field_name: f for f in facts}

    assert len(facts) == 3
    assert facts_by_field["legal_name"].value == "MINIMAL ENK"
    assert facts_by_field["vat_registered"].value is False
    assert facts_by_field["status"].value == "active"
    assert "website" not in facts_by_field
    assert "employee_count" not in facts_by_field


def test_determine_status() -> None:
    assert determine_status({"slettedato": "2023-01-01"}) == "deleted"
    assert determine_status({"konkurs": True}) == "bankrupt"
    assert determine_status({"underAvvikling": True}) == "under_liquidation"
    assert determine_status({"underTvangsavviklingEllerTvangsopplosning": True}) == "under_liquidation"
    assert determine_status({}) == "active"


def test_format_address() -> None:
    assert format_address(None) is None
    assert format_address({}) is None
    formatted = format_address({
        "adresse": ["Gate 1", "Bygg B"],
        "postnummer": "0123",
        "poststed": "OSLO",
    })
    assert formatted is not None
    assert formatted["street"] == "Gate 1, Bygg B"
    assert formatted["postal_code"] == "0123"
    assert formatted["city"] == "OSLO"
