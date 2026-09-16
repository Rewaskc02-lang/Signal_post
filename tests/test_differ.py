"""Unit tests for diff engine and zero-spurious-change guarantees."""

from datetime import date, datetime, timezone
import os
import tempfile
import pytest

from signalpost.differ import diff_and_update_profile
from signalpost.models import CompanyFact, CompanyProfile
from signalpost.storage import Storage


@pytest.fixture
def temp_storage() -> Storage:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    storage = Storage(db_path=path)
    yield storage
    storage.close()
    if os.path.exists(path):
        os.remove(path)


def test_differ_three_run_lifecycle_and_zero_spurious_changes(temp_storage: Storage) -> None:
    orgnr = "923609016"
    t1 = datetime(2026, 9, 16, 10, 0, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 9, 16, 11, 0, 0, tzinfo=timezone.utc)
    t3 = datetime(2026, 9, 16, 12, 0, 0, tzinfo=timezone.utc)

    # Base facts
    fact_name = CompanyFact(
        field_name="legal_name",
        value="EQUINOR ASA",
        source_name="Enhet",
        source_url="https://brreg.no",
        as_of=None,
        confidence="official",
    )
    fact_rev_2024 = CompanyFact(
        field_name="revenue",
        value=50000000.0,
        unit="NOK",
        source_name="Regnskap",
        source_url="https://brreg.no",
        as_of=date(2024, 12, 31),
        confidence="official",
    )
    fact_status = CompanyFact(
        field_name="status",
        value="active",
        source_name="Enhet",
        source_url="https://brreg.no",
        as_of=None,
        confidence="official",
    )

    # -------------------------------------------------------------------------
    # RUN 1: Initial Ingestion (All facts must be NEW)
    # -------------------------------------------------------------------------
    profile_run1 = CompanyProfile(
        orgnr=orgnr,
        facts=[fact_name, fact_rev_2024, fact_status],
    )
    prev_stored = temp_storage.get_profile(orgnr)  # None
    updated_p1, summary_1 = diff_and_update_profile(
        current_profile=profile_run1,
        previous_profile=prev_stored,
        storage=temp_storage,
        now=t1,
    )

    assert summary_1.new_count == 3
    assert summary_1.changed_count == 0
    assert summary_1.confirmed_count == 0
    assert summary_1.last_checked == t1
    assert summary_1.last_changed == t1
    assert all(c.status == "new" for c in summary_1.changes)

    # -------------------------------------------------------------------------
    # RUN 2: Immediate re-run with NO data change (All facts must be CONFIRMED)
    # ZERO spurious change guarantee: last_changed must NOT be touched.
    # -------------------------------------------------------------------------
    prev_stored = temp_storage.get_profile(orgnr)
    assert prev_stored is not None

    profile_run2 = CompanyProfile(
        orgnr=orgnr,
        facts=[fact_name, fact_rev_2024, fact_status],
    )
    updated_p2, summary_2 = diff_and_update_profile(
        current_profile=profile_run2,
        previous_profile=prev_stored,
        storage=temp_storage,
        now=t2,
    )

    assert summary_2.new_count == 0
    assert summary_2.changed_count == 0
    assert summary_2.confirmed_count == 3
    assert summary_2.last_checked == t2
    # STRICT GUARANTEE: last_changed must remain t1
    assert summary_2.last_changed == t1
    assert updated_p2.last_changed == t1
    assert all(c.status == "confirmed" for c in summary_2.changes)

    # -------------------------------------------------------------------------
    # RUN 3: 1 modified financial figure + 1 brand-new field (employee_count)
    # -------------------------------------------------------------------------
    fact_rev_modified = CompanyFact(
        field_name="revenue",
        value=55000000.0,  # Changed from 50000000.0
        unit="NOK",
        source_name="Regnskap",
        source_url="https://brreg.no",
        as_of=date(2024, 12, 31),
        confidence="official",
    )
    fact_employees = CompanyFact(
        field_name="employee_count",  # Brand new field
        value=21000,
        unit="count",
        source_name="Enhet",
        source_url="https://brreg.no",
        as_of=None,
        confidence="official",
    )

    prev_stored = temp_storage.get_profile(orgnr)
    profile_run3 = CompanyProfile(
        orgnr=orgnr,
        facts=[fact_name, fact_rev_modified, fact_status, fact_employees],
    )
    updated_p3, summary_3 = diff_and_update_profile(
        current_profile=profile_run3,
        previous_profile=prev_stored,
        storage=temp_storage,
        now=t3,
    )

    assert summary_3.new_count == 1  # employee_count
    assert summary_3.changed_count == 1  # revenue
    assert summary_3.confirmed_count == 2  # legal_name, status
    assert summary_3.last_checked == t3
    assert summary_3.last_changed == t3

    # Check the specific changed fact
    changed_rev = next(c for c in summary_3.changes if c.field_name == "revenue")
    assert changed_rev.status == "changed"
    assert changed_rev.old_value == 50000000.0
    assert changed_rev.new_value == 55000000.0

    # Verify history accumulated in database: 3 + 3 + 4 = 10 entries
    history = temp_storage.get_fact_history(orgnr, limit=50)
    assert len(history) == 10
