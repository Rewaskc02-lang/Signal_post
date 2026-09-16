"""Unit tests for BrregClient with respx mocked HTTP requests."""

import json
from unittest.mock import patch
import httpx
import pytest
import respx

from signalpost.brreg_client import (
    BrregClient,
    BrregError,
    BrregServerError,
    InvalidOrgnrError,
    NotFoundError,
    RateLimitedError,
)
from signalpost.config import Settings


@pytest.fixture
def mock_settings() -> Settings:
    return Settings(
        brreg_enhet_base_url="https://mock.brreg.no/enheter",
        brreg_regnskap_base_url="https://mock.brreg.no/regnskap",
        brreg_request_timeout_seconds=5.0,
        brreg_max_concurrency=5,
        brreg_max_retries=3,
        brreg_retry_backoff_factor=0.01,  # Fast for tests
    )


@pytest.mark.asyncio
async def test_fetch_enhet_success(mock_settings: Settings) -> None:
    orgnr = "923609016"
    expected_response = {
        "organisasjonsnummer": orgnr,
        "navn": "EQUINOR ASA",
        "organisasjonsform": {"kode": "ASA", "beskrivelse": "Allmennaksjeselskap"},
    }

    with respx.mock(assert_all_called=True) as respx_mock:
        route = respx_mock.get(f"{mock_settings.brreg_enhet_base_url}/{orgnr}").respond(
            status_code=200,
            json=expected_response,
        )

        async with BrregClient(settings=mock_settings) as client:
            result = await client.fetch_enhet(orgnr)

        assert route.called
        assert result["navn"] == "EQUINOR ASA"
        assert result["organisasjonsnummer"] == orgnr


@pytest.mark.asyncio
async def test_fetch_regnskap_success(mock_settings: Settings) -> None:
    orgnr = "923609016"
    expected_response = [
        {"id": 1, "journalnr": "2023001", "regnskapsperiode": {"fraDato": "2023-01-01", "tilDato": "2023-12-31"}},
        {"id": 2, "journalnr": "2022001", "regnskapsperiode": {"fraDato": "2022-01-01", "tilDato": "2022-12-31"}},
    ]

    with respx.mock(assert_all_called=True) as respx_mock:
        route = respx_mock.get(f"{mock_settings.brreg_regnskap_base_url}/{orgnr}").respond(
            status_code=200,
            json=expected_response,
        )

        async with BrregClient(settings=mock_settings) as client:
            result = await client.fetch_regnskap(orgnr)

        assert route.called
        assert isinstance(result, list)
        assert len(result) == 2
        assert result[0]["journalnr"] == "2023001"


@pytest.mark.asyncio
async def test_invalid_orgnr_raises_before_network(mock_settings: Settings) -> None:
    # Invalid checksum
    invalid_orgnr = "923609017"

    with respx.mock(assert_all_called=False) as respx_mock:
        async with BrregClient(settings=mock_settings) as client:
            with pytest.raises(InvalidOrgnrError):
                await client.fetch_enhet(invalid_orgnr)

            with pytest.raises(InvalidOrgnrError):
                await client.fetch_regnskap(invalid_orgnr)

        # Confirm zero network requests were attempted
        assert len(respx_mock.calls) == 0


@pytest.mark.asyncio
async def test_fetch_enhet_404_raises_not_found_error(mock_settings: Settings) -> None:
    orgnr = "923609016"

    with respx.mock(assert_all_called=True) as respx_mock:
        respx_mock.get(f"{mock_settings.brreg_enhet_base_url}/{orgnr}").respond(
            status_code=404,
            json={"melding": "Ikke funnet"},
        )

        async with BrregClient(settings=mock_settings) as client:
            with pytest.raises(NotFoundError) as exc_info:
                await client.fetch_enhet(orgnr)

            assert exc_info.value.status_code == 404
            assert exc_info.value.orgnr == orgnr


@pytest.mark.asyncio
async def test_fetch_enhet_429_raises_rate_limited_error(mock_settings: Settings) -> None:
    orgnr = "923609016"

    with respx.mock(assert_all_called=True) as respx_mock:
        respx_mock.get(f"{mock_settings.brreg_enhet_base_url}/{orgnr}").respond(
            status_code=429,
            json={"melding": "Too many requests"},
        )

        async with BrregClient(settings=mock_settings) as client:
            with pytest.raises(RateLimitedError) as exc_info:
                await client.fetch_enhet(orgnr)

            assert exc_info.value.status_code == 429
            assert exc_info.value.orgnr == orgnr


@pytest.mark.asyncio
async def test_fetch_enhet_503_retries_and_raises_server_error(mock_settings: Settings) -> None:
    orgnr = "923609016"

    with respx.mock() as respx_mock:
        route = respx_mock.get(f"{mock_settings.brreg_enhet_base_url}/{orgnr}").respond(
            status_code=503,
            text="Service Unavailable",
        )

        async with BrregClient(settings=mock_settings) as client:
            with pytest.raises(BrregServerError) as exc_info:
                await client.fetch_enhet(orgnr)

            assert exc_info.value.status_code == 503
            assert route.call_count == mock_settings.brreg_max_retries


@pytest.mark.asyncio
async def test_fetch_enhet_retry_recovers(mock_settings: Settings) -> None:
    orgnr = "923609016"
    expected_response = {"organisasjonsnummer": orgnr, "navn": "EQUINOR ASA"}

    with respx.mock() as respx_mock:
        # First call fails with 503, second call succeeds with 200
        route = respx_mock.get(f"{mock_settings.brreg_enhet_base_url}/{orgnr}")
        route.side_effect = [
            httpx.Response(503, text="Service Unavailable"),
            httpx.Response(200, json=expected_response),
        ]

        async with BrregClient(settings=mock_settings) as client:
            result = await client.fetch_enhet(orgnr)

        assert route.call_count == 2
        assert result["navn"] == "EQUINOR ASA"


@pytest.mark.asyncio
async def test_structured_json_lines_logging(mock_settings: Settings, capsys: pytest.CaptureFixture[str]) -> None:
    orgnr = "923609016"

    with respx.mock() as respx_mock:
        respx_mock.get(f"{mock_settings.brreg_enhet_base_url}/{orgnr}").respond(
            status_code=200,
            json={"organisasjonsnummer": orgnr},
        )

        async with BrregClient(settings=mock_settings) as client:
            await client.fetch_enhet(orgnr)

    captured = capsys.readouterr()
    lines = [line for line in captured.out.strip().split("\n") if line]
    assert len(lines) >= 1

    last_log = json.loads(lines[-1])
    assert last_log["orgnr"] == orgnr
    assert last_log["endpoint"] == "enhetsregisteret"
    assert last_log["status_code"] == 200
    assert "latency_ms" in last_log
    assert last_log["attempt"] == 1
    assert last_log["error"] is None
