# Signalpost 🧭

Signalpost is an asynchronous Python agent designed to retrieve, validate, reconcile, and explain Norwegian company facts from official public registries (**Brønnøysundregistrene (Brreg)**) and verified secondary web sources.

---

## The ONE Command to Run It

### Batch Evaluation Mode
```bash
python run.py --input orgnumbers.txt --output profiles/ --max-requests 2000 --max-seconds 2400
```

### Single Lookup Mode (Instant Ad-Hoc Grading)
```bash
python run.py --orgnr 923609016 --output profiles/
```

---

## Pipeline Architecture

```
                                  [ Organisasjonsnummer ]
                                             │
                                             ▼
                               [ MOD-11 Checksum Validator ] ── (Fails Fast if Invalid)
                                             │
                       ┌─────────────────────┴─────────────────────┐
                       ▼                                           ▼
             [ Enhetsregisteret ]                         [ Regnskapsregisteret ]
             (Core Corporate Facts)                       (Annual Accounts & Filings)
                       │                                           │
                       └─────────────────────┬─────────────────────┘
                                             │
                                             ▼
                                [ Anti-Hallucination Gate ]
                                (Optional Website Verification)
                                             │
                                             ▼
                                  [ Fact Deduplication ]
                                             │
                                             ▼
                                [ Diff Engine & Storage ]
                               (SQLite History & Audit Log)
                                             │
                                             ▼
                                  [ Explanation Engine ]
                              (Fact Notes + Executive Summary)
                                             │
                                             ▼
                                 [ Structured JSON Output ]
                               (Saved to profiles/{orgnr}.json)
```

### Key Stages
1. **MOD-11 Validator (`orgnr.py`)**: Validates the 9-digit Norwegian organisation number with official weights `[3, 2, 7, 6, 5, 4, 3, 2]`. Fails immediately if the checksum is invalid with **zero network calls**.
2. **Registry Client (`brreg_client.py`)**: Asynchronous HTTP client for `Enhetsregisteret` and `Regnskapsregisteret` with `asyncio.Semaphore` concurrency limiting (default 15), connection pooling, exponential backoff retries, and structured JSON-lines logging.
3. **Extractors (`extractors/`)**:
   - `brreg_enhet.py`: Extracts 14+ official facts (`legal_name`, `org_form_code/desc`, `nace_code/desc`, `addresses`, `employee_count`, `website`, `status`).
   - `brreg_regnskap.py`: Extracts `revenue`, `operating_result`, `ordinary_result_before_tax`, and `equity` with exact fiscal year end dates (`as_of = tilDato`). Never fabricates zeros or null values for omitted fields.
   - `website_enrichment.py`: Scrapes company homepage and imprint pages under `robots.txt` compliance with strict identity verification.
4. **Stateful Diff Engine (`differ.py` & `storage.py`)**: Tracks historical facts in SQLite (`fact_history`). Classifies facts into `new`, `changed`, and `confirmed`. Preserves `last_changed` when no facts have changed (zero spurious changes).
5. **Explanation & Summarizer (`explain.py`)**: Generates deterministic verifiable notes for every fact and provides token-monitored plain-English executive summaries with strict anti-hallucination prompts.
6. **Hard Budget Circuit Breakers (`cli.py`)**: Enforces hard stops at `--max-requests` (default 2,000) and `--max-seconds` (default 2,400s). Flushes all completed work safely without crashing.

---

## Model & API Details

- **Brønnøysundregistrene (Brreg) APIs**:
  - `GET https://data.brreg.no/enhetsregisteret/api/enheter/{orgnr}`
  - `GET https://data.brreg.no/regnskapsregisteret/regnskap/{orgnr}`
  - `GET https://data.brreg.no/enhetsregisteret/api/enheter/lastned` (Bulk export)
  - Free public APIs provided by the Norwegian government. No API key required.
- **Natural Language Summary Model**:
  - Provider: Gemini / OpenAI compatible (`gemini-2.5-flash` or `gpt-4o-mini`).
  - Strict anti-hallucination prompt forces the model to synthesize exclusively from provided structured facts.
  - Zero-cost deterministic fallback template when `--llm` is not passed or if an external call fails.

---

## Expected Run Cost Breakdown

| Scope | Ingestion Requests | Registry Cost (Brreg) | LLM Summary Cost | Total Expected Cost |
| :--- | :--- | :--- | :--- | :--- |
| **100 Companies (Daily Grading Run)** | ~200 requests | **$0.00** | **$0.015** USD *(or $0.00 deterministic)* | **$0.015 USD** |
| **1,000 Companies (Submission Run)** | ~2,000 requests | **$0.00** | **$0.150** USD *(or $0.00 deterministic)* | **$0.150 USD** |

- **Constraint Safety Margins**:
  - Request limit: 2,000 requests max (exactly within the 2,000 daily budget).
  - Time limit: 2,400s (40 minutes, under the 45-minute limit).
  - Financial cost: <$0.20 declared run cost, far below the $10.00 cap.

---

## Permitted Data Sources Rationale

1. **Brønnøysundregistrene**: Official open registry for all Norwegian legal entities, licensed under NLOD (Norwegian Licence for Open Government Data) and CC BY 4.0.
2. **Company Websites**: Official homepages referenced in Enhetsregisteret, scraped under `robots.txt` compliance with identity proof validation.

---

## Known Limitations

1. **New Entity Filings**: Entities established during the active fiscal year have no filed accounts in Regnskapsregisteret; the pipeline handles 404 cleanly and returns all core corporate facts.
2. **Specialized Banking/Insurance Institutions**: Select financial institutions report accounts under statutory bank schemas; any unhandled accounts endpoint response is trapped gracefully without failing the core company profile.
3. **Bot-Protected Websites**: Web enrichment safely falls back to 0 secondary facts if a company website is inaccessible or protected by JavaScript challenge screens.

---

## Installation & Testing

```bash
# Set up virtual environment
python3.11 -m venv .venv
source .venv/bin/activate

# Install package and test dependencies
pip install -e ".[dev]"

# Run full offline test suite (75 tests, zero network calls)
pytest -v
```
