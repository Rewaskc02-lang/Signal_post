"""Unit tests for SQLite storage layer and fact history audit log."""

from datetime import date, datetime, timezone
import os
import tempfile
import pytest

from signalpost.models import CompanyFact, CompanyProfile
from signalpost.storage import FactHistoryEntry, Storage


@pytest.fixture
def temp_storage() -> Storage:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    storage = Storage(db_path=path)
    yield storage
    storage.close()
    if os.path.exists(path):
        os.remove(path)


def test_storage_save_and_get_profile(temp_storage: Storage) -> None:
    orgnr = "923609016"
    fact1 = CompanyFact(
        field_name="legal_name",
        value="EQUINOR ASA",
        source_name="Enhetsregisteret",
        source_url="https://data.brreg.no/enhet",
        as_of=None,
        confidence="official",
    )
    fact2 = CompanyFact(
        field_name="revenue",
        value=50000000.0,
        unit="NOK",
        source_name="Regnskapsregisteret",
        source_url="https://data.brreg.no/regnskap",
        as_of=date(2024, 12, 31),
        confidence="official",
    )

    now = datetime(2026, 9, 16, 12, 0, 0, tzinfo=timezone.utc)
    profile = CompanyProfile(
        orgnr=orgnr,
        facts=[fact1, fact2],
        last_checked=now,
        last_changed=now,
    )

    # Initial state -> None
    assert temp_storage.get_profile(orgnr) is None

    # Save
    temp_storage.save_profile(profile)

    # Retrieve
    loaded = temp_storage.get_profile(orgnr)
    assert loaded is not None
    assert loaded.orgnr == orgnr
    assert len(loaded.facts) == 2
    assert loaded.facts[0].value == "EQUINOR ASA"
    assert loaded.facts[1].value == 50000000.0
    assert loaded.last_checked == now
    assert loaded.last_changed == now


def test_storage_fact_history_recording(temp_storage: Storage) -> None:
    orgnr = "923609016"
    now = datetime(2026, 9, 16, 12, 0, 0, tzinfo=timezone.utc)

    entries = [
        FactHistoryEntry(
            orgnr=orgnr,
            field_name="revenue",
            as_of="2024-12-31",
            value_json="50000000.0",
            unit="NOK",
            source_name="Regnskapsregisteret",
            source_url="https://brreg.no",
            confidence="official",
            status="new",
            old_value_json=None,
            content_hash="abc123hash",
            extraction_method="official_api",
            recorded_at=now,
        ),
        FactHistoryEntry(
            orgnr=orgnr,
            field_name="revenue",
            as_of="2024-12-31",
            value_json="52000000.0",
            unit="NOK",
            source_name="Regnskapsregisteret",
            source_url="https://brreg.no",
            confidence="official",
            status="changed",
            old_value_json="50000000.0",
            content_hash="def456hash",
            extraction_method="official_api",
            recorded_at=now,
        ),
    ]

    temp_storage.record_fact_history(entries)

    history = temp_storage.get_fact_history(orgnr)
    assert len(history) == 2
    assert history[0].status == "changed"
    assert history[0].old_value_json == "50000000.0"
    assert history[0].content_hash == "def456hash"
    assert history[0].extraction_method == "official_api"
    assert history[1].status == "new"
    assert history[1].content_hash == "abc123hash"


def test_storage_source_snapshots(temp_storage: Storage) -> None:
    url = "https://data.brreg.no/enhetsregisteret/api/enheter/923609016"
    content_hash = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    body = '{"organisasjonsnummer":"923609016","navn":"EQUINOR ASA"}'
    now_iso = "2026-10-02T20:00:00Z"

    temp_storage.save_snapshot(
        url=url,
        content_hash=content_hash,
        status_code=200,
        content_type="application/json",
        response_body=body,
        retrieved_at=now_iso,
    )

    by_hash = temp_storage.get_snapshot(content_hash)
    assert by_hash is not None
    assert by_hash["url"] == url
    assert by_hash["content_hash"] == content_hash
    assert by_hash["response_body"] == body
    assert by_hash["status_code"] == 200

    by_url = temp_storage.get_snapshot_by_url(url)
    assert by_url is not None
    assert by_url["content_hash"] == content_hash
