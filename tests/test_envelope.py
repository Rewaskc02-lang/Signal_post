"""Unit tests for Signalpost terminal envelope emission and contract validation."""

from datetime import date, datetime, timezone
import json
from pathlib import Path
import tempfile
import pytest
import respx

from signalpost.cli import run_pipeline, read_organisation_inputs
from signalpost.envelope import (
    CONTRACT_STATES,
    ContractValidationError,
    build_terminal_envelope,
    validate_envelope,
)
from signalpost.models import CompanyFact, CompanyProfile


def validate_test_envelope(envelope: dict) -> None:
    """Validate that envelope satisfies official Builderr contract and validator checks."""
    is_valid, errors = validate_envelope(envelope)
    assert is_valid, f"Envelope failed contract validation: {errors}"


def test_build_terminal_envelope_available() -> None:
    orgnr = "923609016"
    now = datetime.now(timezone.utc)
    profile = CompanyProfile(
        orgnr=orgnr,
        facts=[
            CompanyFact(
                field_name="legal_name",
                value="EQUINOR ASA",
                source_name="Brønnøysundregistrene – Enhetsregisteret",
                source_url=f"https://data.brreg.no/enhetsregisteret/api/enheter/{orgnr}",
                confidence="official",
                confidence_level=1.0,
            ),
            CompanyFact(
                field_name="revenue",
                value=1000000,
                unit="NOK",
                source_name="Brønnøysundregistrene – Regnskapsregisteret",
                source_url=f"https://data.brreg.no/regnskapsregisteret/regnskap/{orgnr}",
                confidence="official",
                confidence_level=1.0,
            ),
        ],
    )

    envelope = build_terminal_envelope(
        orgnr=orgnr,
        run_id="test-run-1",
        started_at=now,
        completed_at=now,
        profile=profile,
        request_count=2,
        runtime_ms=150.0,
        cost_usd=0.0,
    )

    validate_test_envelope(envelope)
    assert envelope["state"] == "available"
    assert envelope["run"]["terminal_status"] == "available"
    assert len(envelope["claims"]) >= 2
    assert len(envelope["evidence"]) >= 2
    assert envelope["modules"]["registry"]["state"] == "available"
    assert envelope["modules"]["financials"]["state"] == "available"


def test_build_terminal_envelope_not_available() -> None:
    orgnr = "999999999"
    now = datetime.now(timezone.utc)
    envelope = build_terminal_envelope(
        orgnr=orgnr,
        run_id="test-run-2",
        started_at=now,
        completed_at=now,
        error="Organisasjonsnummer not found in Enhetsregisteret (404)",
        terminal_state="not_available",
    )

    validate_test_envelope(envelope)
    assert envelope["state"] == "not_available"
    assert envelope["run"]["terminal_status"] == "not_available"
    assert len(envelope["claims"]) == 0
    assert len(envelope["errors"]) == 1
    assert envelope["modules"]["registry"]["state"] == "not_available"


def test_build_terminal_envelope_invalid_checksum_failed() -> None:
    orgnr = "12345678"
    now = datetime.now(timezone.utc)
    envelope = build_terminal_envelope(
        orgnr=orgnr,
        run_id="test-run-3",
        started_at=now,
        completed_at=now,
        error="Invalid Norwegian organisation number format or checksum",
        terminal_state="failed",
    )

    validate_test_envelope(envelope)
    assert envelope["state"] == "failed"
    assert envelope["run"]["terminal_status"] == "failed"
    assert envelope["modules"]["registry"]["state"] == "failed"


def test_validator_rejects_uncontract_states() -> None:
    """Test that validate_envelope rejects legacy or unrecognized states."""
    bad_envelope = {
        "organisation_number": "923609016",
        "run_id": "r1",
        "state": "complete",  # Legacy state - must be rejected by validator
        "run": {"run_id": "r1", "started_at": "2026-10-13T00:00:00Z", "completed_at": "2026-10-13T00:00:01Z", "terminal_status": "complete"},
        "claims": [],
        "evidence": [],
        "source_snapshots": [],
        "modules": {"registry": {"state": "complete", "retry_count": 0, "final_timestamp": "2026-10-13T00:00:00Z"}},
        "operations": {"requests": 1, "runtime_ms": 10.0, "third_party_cost_usd": 0.0},
        "changes": [],
        "errors": [],
    }
    is_valid, errors = validate_envelope(bad_envelope)
    assert not is_valid
    assert any("Invalid top-level state 'complete'" in e for e in errors)


def test_validator_rejects_fabricated_zero() -> None:
    """Test that validate_envelope rejects fabricated 0 values for missing financial facts."""
    bad_envelope = {
        "organisation_number": "923609016",
        "run_id": "r1",
        "state": "available",
        "run": {"run_id": "r1", "started_at": "2026-10-13T00:00:00Z", "completed_at": "2026-10-13T00:00:01Z", "terminal_status": "available"},
        "claims": [
            {
                "field": "revenue",
                "value": 0,  # Fabricated 0 for missing field
                "availability": "not_available",
                "source_url": "https://data.brreg.no/regnskap",
                "retrieved_at": "2026-10-13T00:00:00Z",
                "confidence": 1.0,
                "evidence_ids": [],
            }
        ],
        "evidence": [],
        "source_snapshots": [],
        "modules": {"registry": {"state": "available", "retry_count": 0, "final_timestamp": "2026-10-13T00:00:00Z"}},
        "operations": {"requests": 1, "runtime_ms": 10.0, "third_party_cost_usd": 0.0},
        "changes": [],
        "errors": [],
    }
    is_valid, errors = validate_envelope(bad_envelope)
    assert not is_valid
    assert any("fabricated zero prohibited" in e for e in errors)


@pytest.mark.asyncio
async def test_run_pipeline_emits_terminal_envelopes_stdout_and_file(capsys: pytest.CaptureFixture[str]) -> None:
    orgnr = "923609016"
    invalid_org = "12345678"

    with tempfile.TemporaryDirectory() as tmpdir:
        out_file = Path(tmpdir) / "envelopes.jsonl"
        db_file = Path(tmpdir) / "test.db"

        with respx.mock() as respx_mock:
            respx_mock.get(f"https://data.brreg.no/enhetsregisteret/api/enheter/{orgnr}").respond(
                status_code=200, json={"organisasjonsnummer": orgnr, "navn": "EQUINOR ASA"}
            )
            respx_mock.get(f"https://data.brreg.no/regnskapsregisteret/regnskap/{orgnr}").respond(
                status_code=404
            )
            respx_mock.get(f"https://data.brreg.no/enhetsregisteret/api/enheter/{orgnr}/roller").respond(
                status_code=404
            )

            report = await run_pipeline(
                orgnrs=[orgnr, invalid_org],
                output_dir=out_file,
                run_id="eval-run-001",
                db_path=str(db_file),
                quiet=True,
            )

        assert report.total_requested == 2
        assert report.emitted_envelopes == 2

        # 1. Verify stdout contains exactly 2 valid JSON terminal envelopes
        captured = capsys.readouterr()
        stdout_lines = [line.strip() for line in captured.out.strip().split("\n") if line.strip()]
        assert len(stdout_lines) == 2

        envs = [json.loads(line) for line in stdout_lines]
        for env in envs:
            validate_test_envelope(env)

        envs_by_org = {env["organisation_number"]: env for env in envs}
        assert orgnr in envs_by_org
        assert envs_by_org[orgnr]["state"] == "available"

        assert invalid_org in envs_by_org
        assert envs_by_org[invalid_org]["state"] == "failed"

        # 2. Verify output file contains identical valid JSON envelopes
        assert out_file.exists()
        file_lines = [line.strip() for line in out_file.read_text(encoding="utf-8").splitlines() if line.strip()]
        assert len(file_lines) == 2


def test_read_organisation_inputs() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        # 1. Plain text
        txt_path = Path(tmpdir) / "orgs.txt"
        txt_path.write_text("# comment\n923609016\n984851006\n", encoding="utf-8")
        assert read_organisation_inputs(txt_path) == ["923609016", "984851006"]

        # 2. JSON array
        json_path = Path(tmpdir) / "orgs.json"
        json_path.write_text(json.dumps([{"organisation_number": "923609016"}, {"orgnr": "984851006"}]), encoding="utf-8")
        assert read_organisation_inputs(json_path) == ["923609016", "984851006"]

        # 3. JSONL
        jsonl_path = Path(tmpdir) / "orgs.jsonl"
        jsonl_path.write_text('{"organisation_number": "923609016"}\n{"organisation_number": "984851006"}\n', encoding="utf-8")
        assert read_organisation_inputs(jsonl_path) == ["923609016", "984851006"]


def test_build_terminal_envelope_blocked_website_and_claims_as_of() -> None:
    """Test claim as_of date population and module state for blocked website."""
    orgnr = "923609016"
    now = datetime.now(timezone.utc)
    profile = CompanyProfile(
        orgnr=orgnr,
        facts=[
            CompanyFact(
                field_name="revenue",
                value=50000000.0,
                unit="NOK",
                as_of=date(2024, 12, 31),
                source_name="Brønnøysundregistrene – Regnskapsregisteret",
                source_url=f"https://data.brreg.no/regnskapsregisteret/regnskap/{orgnr}",
                confidence="official",
            ),
        ],
        source_statuses={"website": "blocked"},
    )

    envelope = build_terminal_envelope(
        orgnr=orgnr,
        run_id="test-run-blocked-web",
        started_at=now,
        completed_at=now,
        profile=profile,
    )

    validate_test_envelope(envelope)
    assert envelope["modules"]["website"]["state"] == "blocked"
    claim = envelope["claims"][0]
    assert claim["field"] == "revenue"
    assert claim["as_of"] == "2024-12-31"
    assert claim["unit"] == "NOK"
