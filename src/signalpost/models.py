"""Data models for Signalpost facts and company profiles."""

from datetime import date, datetime, timezone
from typing import Any
from pydantic import BaseModel, ConfigDict, Field, model_validator


class CompanyFact(BaseModel):
    """A single atomic company fact retrieved from a verified registry or source."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    field_name: str = Field(
        ...,
        description="Canonical name of the fact (e.g. 'legal_name', 'nace_code', 'revenue').",
    )
    value: Any = Field(
        ...,
        description="Value of the fact (can be string, number, dict, list, boolean, etc.).",
    )
    unit: str | None = Field(
        default=None,
        description="Unit or currency if applicable (e.g. 'NOK', 'count', 'percent').",
    )
    source_name: str = Field(
        ...,
        description="Name of the authoritative data source (e.g. 'Brønnøysundregistrene – Enhetsregisteret').",
    )
    source_url: str = Field(
        ...,
        description="Exact URL endpoint or resource where this fact was retrieved.",
    )
    as_of: date | datetime | None = Field(
        default=None,
        description="Effective date of the fact according to the data source (e.g. fiscal year end).",
    )
    retrieved_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timestamp when this fact was retrieved by Signalpost.",
    )
    confidence: str = Field(
        default="official",
        description="Confidence label ('official', 'verified_secondary', 'unverified_secondary').",
    )
    confidence_level: float | str | None = Field(
        default=1.0,
        description="Numeric confidence score or alias for backwards compatibility.",
    )

    @model_validator(mode="before")
    @classmethod
    def validate_confidence_fields(cls, values: Any) -> Any:
        if isinstance(values, dict):
            # If confidence_level is provided as float, validate range 0.0 <= val <= 1.0
            conf_level = values.get("confidence_level")
            if isinstance(conf_level, (int, float)):
                if not (0.0 <= float(conf_level) <= 1.0):
                    raise ValueError("confidence_level must be between 0.0 and 1.0")
                if "confidence" not in values:
                    values["confidence"] = "official" if conf_level >= 0.99 else "unverified_secondary"
            elif isinstance(conf_level, str) and "confidence" not in values:
                values["confidence"] = conf_level
        return values


class CompanyProfile(BaseModel):
    """Aggregated profile for an organisation containing all observed facts."""

    model_config = ConfigDict(extra="forbid")

    orgnr: str = Field(
        ...,
        description="9-digit Norwegian organisation number.",
    )
    facts: list[CompanyFact] = Field(
        default_factory=list,
        description="List of atomic facts associated with this organisation.",
    )
    last_checked: datetime | None = Field(
        default=None,
        description="Timestamp of the latest check/lookup attempt for this organisation.",
    )
    last_changed: datetime | None = Field(
        default=None,
        description="Timestamp of the latest change or registration update from source.",
    )
