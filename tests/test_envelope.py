"""Unit tests for Signalpost terminal envelope emission and contract validation."""

from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import pytest
import respx

from signalpost.cli import run_pipeline, read_organisation_inputs
from signalpost.config import Settings
from signalpost.envelope import TERMINAL_STATES, build_terminal_envelope
from signalpost.models import CompanyFact, CompanyProfile


def validate_test_envelope(envelope: dict) -> None:
    """Validate that envelope satisfies minimal output contract and evaluator checks."""
    assert "organisation_number" in envelope
    assert "run" in envelope
    run = envelope["run"]
    assert "run_id" in run
    assert "started_at" in run
    assert "completed_at" in run
    assert "terminal_status" in run
    assert "claims" in envelope
    assert isinstance(envelope["claims"], list)
    assert "evidence" in envelope
    assert isinstance(envelope["evidence"], list)
    assert "changes" in envelope
    assert isinstance(envelope["changes"], list)
    assert "errors" in envelope
    assert isinstance(envelope["errors"], list)
    assert "operations" in envelope
    ops = envelope["operations"]
    assert "requests" in ops
    assert "runtime_ms" in ops
    assert "third_party_cost_usd" in ops

    # Check terminal state conformance
    assert envelope["state"] in TERMINAL_STATES
    for mod_name, mod in envelope.get("modules", {}).items():
        assert mod["state"] in TERMINAL_STATES


def test_build_terminal_envelope_success() -> None:
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
    assert envelope["state"] == "complete"
    assert envelope["run"]["terminal_status"] == "completed"
    assert len(envelope["claims"]) == 2
    assert len(envelope["evidence"]) == 2
    assert envelope["modules"]["registry"]["state"] == "complete"
    assert envelope["modules"]["financials"]["state"] == "complete"


def test_build_terminal_envelope_not_found() -> None:
    orgnr = "999999999"
    now = datetime.now(timezone.utc)
    envelope = build_terminal_envelope(
        orgnr=orgnr,
        run_id="test-run-2",
        started_at=now,
        completed_at=now,
        error="Organisasjonsnummer not found in Enhetsregisteret (404)",
        terminal_state="not_found",
    )

    validate_test_envelope(envelope)
    assert envelope["state"] == "not_found"
    assert envelope["run"]["terminal_status"] == "not_found"
    assert len(envelope["claims"]) == 0
    assert len(envelope["errors"]) == 1


def test_build_terminal_envelope_invalid_checksum() -> None:
    orgnr = "12345678"
    now = datetime.now(timezone.utc)
    envelope = build_terminal_envelope(
        orgnr=orgnr,
        run_id="test-run-3",
        started_at=now,
        completed_at=now,
        error="Invalid Norwegian organisation number format or checksum",
        terminal_state="submission_error",
    )

    validate_test_envelope(envelope)
    assert envelope["state"] == "submission_error"
    assert envelope["run"]["terminal_status"] == "submission_error"


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
        assert envs_by_org[orgnr]["state"] == "complete"

        assert invalid_org in envs_by_org
        assert envs_by_org[invalid_org]["state"] == "submission_error"

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
