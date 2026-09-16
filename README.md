# Signalpost 🧭

Signalpost is an asynchronous Python library and agent foundation designed to retrieve, validate, and structure Norwegian company facts from official public registries (primarily **Brønnøysundregistrene (Brreg)**) and verified secondary sources.

---

## Architecture

Signalpost is structured into focused, testable layers:

1. **MOD-11 Checksum Validator (`orgnr.py`)**: Strict Modulus-11 validation with weights `[3, 2, 7, 6, 5, 4, 3, 2]` that fails fast on invalid organisation numbers before any network interaction.
2. **Data Models (`models.py`)**: Immutable, atomic `CompanyFact` units with complete provenance metadata (`source_url`, `source_name`, `as_of`, `retrieved_at`, and `confidence`) and aggregated `CompanyProfile` models.
3. **Async Registry Client (`brreg_client.py`)**: Asynchronous HTTP client for `Enhetsregisteret` and `Regnskapsregisteret` featuring exponential backoff retries on transient 5xx errors, connection pooling, concurrency limiting (`asyncio.Semaphore`), and structured JSON-lines output for auditability and daily quota tracking.
4. **Extractors (`extractors/`)**:
   - `brreg_enhet.py`: Extracts official core company facts (legal name, org form, registration date, NACE codes, addresses, employee count, contact info, status).
   - `brreg_regnskap.py`: Extracts financial facts (revenue, operating result, pre-tax profit, equity) with precise `as_of` dates set to the filing's fiscal year end date (`tilDato`). Never fabricates zeros or null values for omitted fields.
   - `website_enrichment.py`: Scrapes company websites while respecting `robots.txt` and applying a strict **Anti-Hallucination Verification Guardrail** (requiring proof of orgnr or exact legal name on the page before publishing any facts).
5. **Profile Orchestrator (`matcher.py`)**: `build_profile(orgnr)` orchestrates concurrent registry lookups, fact extraction, optional website enrichment, and deduplication into a unified `CompanyProfile`.

---

## Anti-Hallucination Guarantee

To prevent wrong-company publication or fabricated facts:
- **Registry Data (Official)**: Brreg lookups are keyed by org number directly. Every fact carries `confidence="official"` and the exact URL queried.
- **Secondary Web Sources**: Before extracting any facts from a candidate website, `website_enrichment` strictly validates that the 9-digit `orgnr` (standard, spaced, or MVA format) or an exact match of the registered legal name is present in the page text. If unverified, **zero facts** are published, and a structured `match_unverified` log is recorded.
- **Financial Precision**: Financial metrics are never interpolated, estimated, or rounded beyond reported values. If a line item is absent in a filing, it is omitted.

---

## Installation & Setup

```bash
# Create virtual environment
python3.11 -m venv .venv
source .venv/bin/activate

# Install dependencies and editable package
pip install -e ".[dev]"

# Configure environment
cp .env.example .env
```

---

## Running Tests

Run all unit and integration tests (100% offline with `respx` mocks):
```bash
pytest -v
```

---

## Manual Live Verification

Run `scripts/manual_fetch.py` against live Brreg APIs:
```bash
# Fetch and print profiles for default companies (Equinor, DNB, Yara, Orkla, Posten)
python scripts/manual_fetch.py

# Fetch specific organisation numbers with optional website enrichment
python scripts/manual_fetch.py 923609016 986228608 --enrich
```
