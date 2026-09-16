"""Diff engine for change detection between consecutive pipeline runs."""

from datetime import date, datetime, timezone
import json
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field

from signalpost.models import CompanyFact, CompanyProfile
from signalpost.storage import FactHistoryEntry, Storage


class FactChange(BaseModel):
    """Detailed record of a single fact comparison result."""

    model_config = ConfigDict(extra="forbid")

    field_name: str
    as_of: date | datetime | None = None
    status: Literal["new", "changed", "confirmed"]
    old_value: Any = None
    new_value: Any
    unit: str | None = None
    changed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ProfileUpdateSummary(BaseModel):
    """Summary of changes detected during profile reconciliation."""

    model_config = ConfigDict(extra="forbid")

    orgnr: str
    new_count: int = 0
    changed_count: int = 0
    confirmed_count: int = 0
    changes: list[FactChange] = Field(default_factory=list)
    last_checked: datetime
    last_changed: datetime | None = None

    @property
    def has_changes(self) -> bool:
        """True if any facts were new or modified."""
        return self.new_count > 0 or self.changed_count > 0


def values_are_equal(val1: Any, val2: Any) -> bool:
    """Compare two fact values safely, handling nested structures and types."""
    if val1 == val2:
        return True
    try:
        j1 = json.dumps(val1, sort_keys=True, default=str)
        j2 = json.dumps(val2, sort_keys=True, default=str)
        return j1 == j2
    except Exception:
        return False


def diff_and_update_profile(
    current_profile: CompanyProfile,
    previous_profile: CompanyProfile | None,
    storage: Storage | None = None,
    now: datetime | None = None,
) -> tuple[CompanyProfile, ProfileUpdateSummary]:
    """Reconcile a fresh CompanyProfile against the previously stored state.

    Statefulness & Zero-Spurious-Change Guarantees:
    - If previously unseen: status='new'.
    - If value modified: status='changed', logs old_value & new_value, bumps last_changed.
    - If unchanged: status='confirmed', bumps last_checked, PRESERVES last_changed.
    - Writes all changes to fact_history and updates the profiles table.

    Args:
        current_profile: Freshly fetched and extracted CompanyProfile.
        previous_profile: Previously stored CompanyProfile for the same orgnr (if any).
        storage: Optional Storage instance for persistence.
        now: Optional fixed timestamp for deterministic testing.

    Returns:
        Tuple of (updated_profile, profile_update_summary).
    """
    check_time = now or datetime.now(timezone.utc)

    # Index previous facts by (field_name, as_of_key)
    prev_map: dict[tuple[str, str | None], CompanyFact] = {}
    if previous_profile:
        for f in previous_profile.facts:
            as_of_key = f.as_of.isoformat() if f.as_of else None
            prev_map[(f.field_name, as_of_key)] = f

    changes: list[FactChange] = []
    history_entries: list[FactHistoryEntry] = []

    new_count = 0
    changed_count = 0
    confirmed_count = 0

    for current_fact in current_profile.facts:
        as_of_key = current_fact.as_of.isoformat() if current_fact.as_of else None
        fact_key = (current_fact.field_name, as_of_key)

        prev_fact = prev_map.get(fact_key)

        if prev_fact is None:
            # 1. Unseen before -> NEW
            status: Literal["new", "changed", "confirmed"] = "new"
            new_count += 1
            change = FactChange(
                field_name=current_fact.field_name,
                as_of=current_fact.as_of,
                status="new",
                old_value=None,
                new_value=current_fact.value,
                unit=current_fact.unit,
                changed_at=check_time,
            )
            changes.append(change)
            history_entries.append(
                FactHistoryEntry(
                    orgnr=current_profile.orgnr,
                    field_name=current_fact.field_name,
                    as_of=as_of_key,
                    value_json=json.dumps(current_fact.value, default=str),
                    unit=current_fact.unit,
                    source_name=current_fact.source_name,
                    source_url=current_fact.source_url,
                    confidence=current_fact.confidence,
                    status="new",
                    old_value_json=None,
                    recorded_at=check_time,
                )
            )

        elif not values_are_equal(current_fact.value, prev_fact.value):
            # 2. Value changed -> CHANGED
            status = "changed"
            changed_count += 1
            change = FactChange(
                field_name=current_fact.field_name,
                as_of=current_fact.as_of,
                status="changed",
                old_value=prev_fact.value,
                new_value=current_fact.value,
                unit=current_fact.unit,
                changed_at=check_time,
            )
            changes.append(change)
            history_entries.append(
                FactHistoryEntry(
                    orgnr=current_profile.orgnr,
                    field_name=current_fact.field_name,
                    as_of=as_of_key,
                    value_json=json.dumps(current_fact.value, default=str),
                    unit=current_fact.unit,
                    source_name=current_fact.source_name,
                    source_url=current_fact.source_url,
                    confidence=current_fact.confidence,
                    status="changed",
                    old_value_json=json.dumps(prev_fact.value, default=str),
                    recorded_at=check_time,
                )
            )

        else:
            # 3. Value unchanged -> CONFIRMED
            status = "confirmed"
            confirmed_count += 1
            change = FactChange(
                field_name=current_fact.field_name,
                as_of=current_fact.as_of,
                status="confirmed",
                old_value=prev_fact.value,
                new_value=current_fact.value,
                unit=current_fact.unit,
                changed_at=check_time,
            )
            changes.append(change)
            history_entries.append(
                FactHistoryEntry(
                    orgnr=current_profile.orgnr,
                    field_name=current_fact.field_name,
                    as_of=as_of_key,
                    value_json=json.dumps(current_fact.value, default=str),
                    unit=current_fact.unit,
                    source_name=current_fact.source_name,
                    source_url=current_fact.source_url,
                    confidence=current_fact.confidence,
                    status="confirmed",
                    old_value_json=json.dumps(prev_fact.value, default=str),
                    recorded_at=check_time,
                )
            )

    # Resolve last_changed timestamp
    if previous_profile is None:
        last_changed = check_time
    elif new_count > 0 or changed_count > 0:
        last_changed = check_time
    else:
        # Strictly preserve previous last_changed when no facts changed
        last_changed = previous_profile.last_changed or check_time

    updated_profile = CompanyProfile(
        orgnr=current_profile.orgnr,
        facts=current_profile.facts,
        last_checked=check_time,
        last_changed=last_changed,
    )

    summary = ProfileUpdateSummary(
        orgnr=current_profile.orgnr,
        new_count=new_count,
        changed_count=changed_count,
        confirmed_count=confirmed_count,
        changes=changes,
        last_checked=check_time,
        last_changed=last_changed,
    )

    # Persist if storage instance provided
    if storage is not None:
        storage.save_profile(updated_profile)
        storage.record_fact_history(history_entries)

    return updated_profile, summary
