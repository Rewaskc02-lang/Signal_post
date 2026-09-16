"""Unit tests for CompanyFact and CompanyProfile data models."""

from datetime import date, datetime, timezone
import pytest
from pydantic import ValidationError

from signalpost.models import CompanyFact, CompanyProfile


def test_company_fact_creation_minimal() -> None:
    fact = CompanyFact(
        field_name="organisasjonsform",
        value="ASA",
        source_name="brreg_enhetsregisteret",
        source_url="https://data.brreg.no/enhetsregisteret/api/enheter/923609016",
    )
    assert fact.field_name == "organisasjonsform"
    assert fact.value == "ASA"
    assert fact.unit is None
    assert fact.source_name == "brreg_enhetsregisteret"
    assert fact.source_url == "https://data.brreg.no/enhetsregisteret/api/enheter/923609016"
    assert fact.as_of is None
    assert fact.confidence_level == 1.0
    assert isinstance(fact.retrieved_at, datetime)


def test_company_fact_creation_full() -> None:
    as_of_date = date(2023, 12, 31)
    retrieved_time = datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
    fact = CompanyFact(
        field_name="salgsinntekter",
        value=1150000000.0,
        unit="NOK",
        source_name="brreg_regnskapsregisteret",
        source_url="https://data.brreg.no/regnskapsregisteret/regnskap/923609016",
        as_of=as_of_date,
        retrieved_at=retrieved_time,
        confidence_level=0.95,
    )
    assert fact.field_name == "salgsinntekter"
    assert fact.value == 1150000000.0
    assert fact.unit == "NOK"
    assert fact.as_of == as_of_date
    assert fact.retrieved_at == retrieved_time
    assert fact.confidence_level == 0.95


def test_company_fact_validation_confidence_range() -> None:
    # Confidence level cannot be > 1.0
    with pytest.raises(ValidationError):
        CompanyFact(
            field_name="name",
            value="Test",
            source_name="test",
            source_url="https://test.local",
            confidence_level=1.5,
        )

    # Confidence level cannot be < 0.0
    with pytest.raises(ValidationError):
        CompanyFact(
            field_name="name",
            value="Test",
            source_name="test",
            source_url="https://test.local",
            confidence_level=-0.1,
        )


def test_company_fact_immutability() -> None:
    fact = CompanyFact(
        field_name="name",
        value="Equinor",
        source_name="brreg",
        source_url="https://brreg.no",
    )
    with pytest.raises(ValidationError):
        fact.value = "New Name"  # type: ignore[misc]


def test_company_profile_creation_and_facts() -> None:
    fact1 = CompanyFact(
        field_name="navn",
        value="EQUINOR ASA",
        source_name="brreg_enhetsregisteret",
        source_url="https://data.brreg.no/enhetsregisteret/api/enheter/923609016",
    )
    fact2 = CompanyFact(
        field_name="antallAnsatte",
        value=21000,
        unit="count",
        source_name="brreg_enhetsregisteret",
        source_url="https://data.brreg.no/enhetsregisteret/api/enheter/923609016",
    )

    profile = CompanyProfile(
        orgnr="923609016",
        facts=[fact1, fact2],
        last_checked=datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc),
    )

    assert profile.orgnr == "923609016"
    assert len(profile.facts) == 2
    assert profile.facts[0].value == "EQUINOR ASA"
    assert profile.facts[1].value == 21000

    # Ensure JSON serializability
    dumped = profile.model_dump(mode="json")
    assert dumped["orgnr"] == "923609016"
    assert len(dumped["facts"]) == 2
