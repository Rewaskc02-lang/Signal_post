"""Signalpost: Norwegian company fact lookup agent."""

from signalpost.brreg_client import (
    BrregClient,
    BrregError,
    BrregServerError,
    InvalidOrgnrError,
    NotFoundError,
    RateLimitedError,
)
from signalpost.config import Settings, settings
from signalpost.differ import (
    FactChange,
    ProfileUpdateSummary,
    diff_and_update_profile,
)
from signalpost.explain import (
    CompanySummaryResult,
    generate_company_summary,
    generate_deterministic_summary,
    generate_fact_note,
)
from signalpost.extractors import (
    enrich_from_website,
    extract_enhet_facts,
    extract_regnskap_facts,
)
from signalpost.matcher import build_profile, deduplicate_facts
from signalpost.models import CompanyFact, CompanyProfile
from signalpost.orgnr import sanitize_orgnr, validate_orgnr
from signalpost.sampling import is_active_entity, sample_active_orgnrs
from signalpost.storage import FactHistoryEntry, Storage

__version__ = "0.3.0"

__all__ = [
    "BrregClient",
    "BrregError",
    "BrregServerError",
    "CompanyFact",
    "CompanyProfile",
    "CompanySummaryResult",
    "FactChange",
    "FactHistoryEntry",
    "InvalidOrgnrError",
    "NotFoundError",
    "ProfileUpdateSummary",
    "RateLimitedError",
    "Settings",
    "Storage",
    "build_profile",
    "deduplicate_facts",
    "diff_and_update_profile",
    "enrich_from_website",
    "extract_enhet_facts",
    "extract_regnskap_facts",
    "generate_company_summary",
    "generate_deterministic_summary",
    "generate_fact_note",
    "is_active_entity",
    "sample_active_orgnrs",
    "sanitize_orgnr",
    "settings",
    "validate_orgnr",
]
