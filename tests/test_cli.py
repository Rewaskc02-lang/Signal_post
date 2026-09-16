"""Unit tests for unified CLI (run.py / cli.py) and circuit breaker budget enforcement."""

from pathlib import Path
import tempfile
import pytest
import respx

from signalpost.cli import BudgetExhaustedError, BudgetTracker, run_pipeline
from signalpost.config import Settings


@pytest.fixture
def mock_settings() -> Settings:
    return Settings(
        brreg_enhet_base_url="https://mock.brreg.no/enheter",
        brreg_regnskap_base_url="https://mock.brreg.no/regnskap",
        brreg_request_timeout_seconds=2.0,
        brreg_max_concurrency=5,
        brreg_max_retries=1,
    )


@pytest.mark.asyncio
async def test_run_pipeline_single_company(mock_settings: Settings) -> None:
    orgnr = "923609016"
    with tempfile.TemporaryDirectory() as tmpdir:
        out_dir = Path(tmpdir) / "profiles"
        db_file = Path(tmpdir) / "test.db"

        enhet_data = {
            "organisasjonsnummer": orgnr,
            "navn": "EQUINOR ASA",
            "registrertIMvaregisteret": True,
            "konkurs": False,
        }

        with respx.mock() as respx_mock:
            respx_mock.get(f"{default_settings_base(mock_settings.brreg_enhet_base_url)}/{orgnr}").respond(
                status_code=200, json=enhet_data
            )
            respx_mock.get(f"{default_settings_base(mock_settings.brreg_regnskap_base_url)}/{orgnr}").respond(
                status_code=404
            )

            report = await run_pipeline(
                orgnrs=[orgnr],
                output_dir=out_dir,
                max_requests=100,
                max_seconds=60.0,
                db_path=str(db_file),
                quiet=True,
            )

        assert report.completed == 1
        assert report.failed == 0
        assert (out_dir / f"{orgnr}.json").exists()


@pytest.mark.asyncio
async def test_circuit_breaker_max_requests_halts_cleanly(mock_settings: Settings) -> None:
    # 5 companies, but budget is only 2 requests (1 company takes 2 requests: enhet + regnskap)
    orgnrs = ["923609016", "984851006", "986228608", "910747711", "984661185"]

    with tempfile.TemporaryDirectory() as tmpdir:
        out_dir = Path(tmpdir) / "profiles"
        db_file = Path(tmpdir) / "test.db"

        with respx.mock() as respx_mock:
            # First company succeeds
            respx_mock.get(f"{default_settings_base(mock_settings.brreg_enhet_base_url)}/923609016").respond(
                status_code=200, json={"organisasjonsnummer": "923609016", "navn": "Company 1"}
            )
            respx_mock.get(f"{default_settings_base(mock_settings.brreg_regnskap_base_url)}/923609016").respond(
                status_code=404
            )

            report = await run_pipeline(
                orgnrs=orgnrs,
                output_dir=out_dir,
                max_requests=2,  # Hard cap
                max_seconds=60.0,
                db_path=str(db_file),
                concurrency=1,
                quiet=True,
            )

        assert report.outbound_requests_used <= 2
        assert report.halt_reason is not None
        assert "request limit" in report.halt_reason.lower()


@pytest.mark.asyncio
async def test_circuit_breaker_max_seconds_halts_cleanly(mock_settings: Settings) -> None:
    orgnrs = ["923609016", "984851006"]
    with tempfile.TemporaryDirectory() as tmpdir:
        out_dir = Path(tmpdir) / "profiles"
        db_file = Path(tmpdir) / "test.db"

        # Already expired budget
        report = await run_pipeline(
            orgnrs=orgnrs,
            output_dir=out_dir,
            max_requests=100,
            max_seconds=0.0001,  # Instantly exhausted
            db_path=str(db_file),
            quiet=True,
        )

        assert report.halt_reason is not None
        assert "time budget" in report.halt_reason.lower()


@pytest.mark.asyncio
async def test_run_pipeline_invalid_orgnr_fails_safely() -> None:
    invalid_orgnr = "12345678"  # Too short
    with tempfile.TemporaryDirectory() as tmpdir:
        out_dir = Path(tmpdir) / "profiles"
        db_file = Path(tmpdir) / "test.db"

        report = await run_pipeline(
            orgnrs=[invalid_orgnr],
            output_dir=out_dir,
            max_requests=10,
            max_seconds=10.0,
            db_path=str(db_file),
            quiet=True,
        )

        assert report.completed == 0
        assert report.failed == 1
        assert report.outbound_requests_used == 0


def default_settings_base(url: str) -> str:
    # Use global default settings URL since run_pipeline uses default_settings inside
    from signalpost.config import settings
    if "enheter" in url:
        return settings.brreg_enhet_base_url
    return settings.brreg_regnskap_base_url
