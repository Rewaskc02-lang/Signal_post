"""Unit tests for explanation generation, fact notes, and company summaries."""

from datetime import date
import pytest
import respx

from signalpost.explain import (
    estimate_token_cost,
    generate_company_summary,
    generate_deterministic_summary,
    generate_fact_note,
)
from signalpost.models import CompanyFact, CompanyProfile


def test_generate_fact_note_templates() -> None:
    fact_name = CompanyFact(
        field_name="legal_name",
        value="EQUINOR ASA",
        source_name="Enhet",
        source_url="https://brreg.no",
        as_of=None,
        confidence="official",
    )
    assert "Enhetsregisteret" in generate_fact_note(fact_name)

    fact_rev = CompanyFact(
        field_name="revenue",
        value=50000000.0,
        unit="USD",
        source_name="Regnskap",
        source_url="https://brreg.no",
        as_of=date(2024, 12, 31),
        confidence="official",
    )
    note_rev = generate_fact_note(fact_rev)
    assert "fiscal year 2024" in note_rev
    assert "50,000,000" in note_rev
    assert "USD" in note_rev

    fact_emp = CompanyFact(
        field_name="employee_count",
        value=21000,
        unit="count",
        source_name="Enhet",
        source_url="https://brreg.no",
        as_of=None,
        confidence="official",
    )
    assert "21000" in generate_fact_note(fact_emp)


def test_generate_deterministic_summary() -> None:
    profile = CompanyProfile(
        orgnr="923609016",
        facts=[
            CompanyFact(
                field_name="legal_name",
                value="EQUINOR ASA",
                source_name="Enhet",
                source_url="https://brreg.no",
                confidence="official",
            ),
            CompanyFact(
                field_name="org_form_description",
                value="Allmennaksjeselskap",
                source_name="Enhet",
                source_url="https://brreg.no",
                confidence="official",
            ),
            CompanyFact(
                field_name="nace_description",
                value="Utvinning av råolje",
                source_name="Enhet",
                source_url="https://brreg.no",
                confidence="official",
            ),
            CompanyFact(
                field_name="status",
                value="active",
                source_name="Enhet",
                source_url="https://brreg.no",
                confidence="official",
            ),
            CompanyFact(
                field_name="employee_count",
                value=21000,
                unit="count",
                source_name="Enhet",
                source_url="https://brreg.no",
                confidence="official",
            ),
            CompanyFact(
                field_name="revenue",
                value=67000000000.0,
                unit="USD",
                source_name="Regnskap",
                source_url="https://brreg.no",
                as_of=date(2024, 12, 31),
                confidence="official",
            ),
        ],
    )

    summary = generate_deterministic_summary(profile)
    assert "EQUINOR ASA" in summary
    assert "923609016" in summary
    assert "active" in summary
    assert "21,000 registered employees" in summary
    assert "67,000,000,000 USD" in summary


@pytest.mark.asyncio
async def test_generate_company_summary_mocked_llm() -> None:
    profile = CompanyProfile(
        orgnr="923609016",
        facts=[
            CompanyFact(
                field_name="legal_name",
                value="EQUINOR ASA",
                source_name="Enhet",
                source_url="https://brreg.no",
                confidence="official",
            ),
        ],
    )

    mock_llm_response = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "text": "Equinor ASA is a Norwegian energy company officially registered in Enhetsregisteret."
                        }
                    ]
                }
            }
        ],
        "usageMetadata": {
            "promptTokenCount": 120,
            "candidatesTokenCount": 25,
        },
    }

    mock_api_url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key=test-key"
    with respx.mock() as respx_mock:
        respx_mock.post(mock_api_url).respond(
            status_code=200,
            json=mock_llm_response,
        )

        result = await generate_company_summary(
            profile=profile,
            enable_llm=True,
            api_key="test-key",
            model="gemini-2.5-flash",
        )

    assert result.is_llm_generated is True
    assert "Equinor ASA" in result.summary_text
    assert result.prompt_tokens == 120
    assert result.completion_tokens == 25
    assert result.estimated_cost_usd > 0.0


@pytest.mark.asyncio
async def test_generate_company_summary_fallback_on_failure() -> None:
    profile = CompanyProfile(
        orgnr="923609016",
        facts=[
            CompanyFact(
                field_name="legal_name",
                value="EQUINOR ASA",
                source_name="Enhet",
                source_url="https://brreg.no",
                confidence="official",
            ),
        ],
    )

    mock_api_url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key=test-key"
    with respx.mock() as respx_mock:
        # LLM returns 500
        respx_mock.post(mock_api_url).respond(status_code=500, text="Internal Server Error")

        # Must NOT crash, falls back gracefully to deterministic summary
        result = await generate_company_summary(
            profile=profile,
            enable_llm=True,
            api_key="test-key",
            model="gemini-2.5-flash",
        )

    assert result.is_llm_generated is False
    assert "EQUINOR ASA" in result.summary_text


def test_estimate_token_cost() -> None:
    cost = estimate_token_cost(prompt_tokens=1000, completion_tokens=500)
    # (1000/1M * 0.15) + (500/1M * 0.60) = 0.00015 + 0.00030 = 0.00045
    assert cost == 0.00045
