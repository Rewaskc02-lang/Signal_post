# Signalpost Submission

## Repository Information
- **Repository URL**: `https://github.com/Rewaskc02-lang/Signal_post`
- **Commit Hash**: `bf7266f81ba6dfffcdae9a5bebe837494e5e791f`
- **Language & Runtime**: Python 3.11+

---

## The ONE Command to Run It

### Batch Ingestion Mode (Evaluating List of Organisation Numbers)
```bash
python run.py --input orgnumbers.txt --output profiles/ --max-requests 5000 --max-seconds 2400
```
*Note: Accepts `--input` (or `--organisations`) pointing to a `.txt`, `.json`, or `.jsonl` file. Emits exactly one JSON terminal envelope per input company directly to `stdout` (JSONL stream) and saves `envelopes.jsonl` (and `{orgnr}.json` profiles) to the output path. Website enrichment is **on by default** — use `--no-enrich` to disable.*

### Single Lookup Mode (Instantaneous Daily Ad-Hoc Grading)
```bash
python run.py --orgnr 923609016 --output profiles/
```

---

## Output Contract & Terminal Envelope Specification
Emits exactly one terminal envelope JSON object per supplied organisation number conforming strictly to the Builderr Signalpost output contract (`OUTPUT_CONTRACT.md` and evaluation harness):
- **Streams to `stdout`**: One clean JSON object per line (JSONL), with all diagnostic logs sent to `stderr`.
- **Saves to `--output`**: Automatically writes `envelopes.jsonl` (and per-company `{orgnr}.json` files when output is a directory).
- **Zero Silent Drops**: Every input organisation emits a terminal envelope with explicit terminal states (`complete`, `not_found`, `submission_error`, `blocked_robots`, `budget_exhausted`, `source_error`).
- **Comprehensive Sections**:
  - `organisation_number`: 9-digit stable identity key.
  - `run`: `{"run_id": ..., "started_at": ..., "completed_at": ..., "terminal_status": ...}`.
  - `claims`: Fact claims with exact `availability`, `source_url`, `retrieved_at`, and `supporting_value` for every claim, plus confidence scores and evidence IDs.
  - `evidence`: Cryptographic sha256 content hashes, exact source URLs (e.g. `https://www.elopak.com/`), source classes, and retrieval timestamps.
  - `source_snapshots`: Raw HTTP response snapshots (URL, status_code, content_type, full response_body, and raw content sha256) enabling instant offline claim verification. Includes both Brreg API JSON bodies and live company website HTML pages.
  - `modules`: State tracking for `registry`, `financials`, and `website`.
  - `changes`: Historical diff records between consecutive runs.
  - `operations`: Request count, wall-clock runtime ms, and third-party cost.
  - `profile`: Structured company profile data.

---

## Model & API Details
- **Primary Data Registry**: Brønnøysundregistrene (Brreg) official REST APIs
  - `Enhetsregisteret`: `https://data.brreg.no/enhetsregisteret/api/enheter/{orgnr}` (core corporate data)
  - `Regnskapsregisteret`: `https://data.brreg.no/regnskapsregisteret/regnskap/{orgnr}` (filed annual accounts)
  - `Bulk Export`: `https://data.brreg.no/enhetsregisteret/api/enheter/lastned` (active entity sampling)
  - *Authentication*: Free, public, no API key required.
- **Natural Language Summary Model**:
  - Model: `gemini-2.5-flash` (or `gpt-4o-mini`)
  - Role: Synthesizes a 2-4 sentence plain-English executive summary based exclusively on verified structured facts.
  - Zero-Cost Fallback: Built-in deterministic template generator active when `--llm` is not passed or if an external call fails.

---

## Expected Run Cost Breakdown & Observed Run Metrics

| Scope | Ingestion Requests | Wall-Clock Time | Registry Cost (Brreg) | LLM Summary Cost | Total Run Cost |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **100 Companies (Daily Grading Run)** | ~210 requests | **~5.6s** | **$0.00** (Free API) | **$0.000** *(or $0.024 with LLM)* | **$0.00 USD** *(or $0.024 USD)* |
| **1,000 Companies (Observed Batch Run)** | **2,112 requests** | **56.23s** | **$0.00** (Free API) | **$0.000** *(or $0.241 with LLM)* | **$0.00 USD** *(or $0.241 USD)* |

- **Strict Challenge Budget Compliance**:
  - Request Limit: 2,112 requests for 1,000 companies (well within 5,000 cap).
  - Time Limit: 56.23s total execution time (well under 2,400s cap).
  - Total Declared Cost: **$0.00 USD** (deterministic) / **$0.24 USD** (with LLM), far below the $10.00 cap.

---

## Permitted Data Sources Rationale
1. **Brønnøysundregistrene (Enhetsregisteret & Regnskapsregisteret)**:
   - Official Norwegian Government business register made available under the Norwegian Licence for Open Government Data (NLOD / CC BY 4.0). Completely public and free.
2. **Company Primary Websites (Secondary Source)**:
   - Each company's registered `hjemmeside` URL (from Brreg) is fetched directly (1 HTTP GET per company). Identity is verified by matching the organisation number or registered legal name in the live page content. Verified pages produce three external claims (`website`, `website_title`, `website_description`) with `source_url` set to the actual live URL (e.g. `https://www.elopak.com/`), byte-level SHA-256 content hash, and full response body retained in `source_snapshots`.

---

## Known Limitations
1. **Newly Registered Entities**: Companies registered in the current fiscal year have not yet filed annual accounts in `Regnskapsregisteret` (the client handles 404 gracefully and populates all core entity facts).
2. **Specialized Financial Entities**: Banks and select state entities file financial accounts under specialized statutory accounting schemes where standard Regnskapsregisteret endpoints may return 404/500 (handled gracefully without halting profile generation).
3. **Secondary Website Coverage**: If a company's website is protected by Cloudflare JS challenges or does not publish its orgnr/imprint, the anti-hallucination gate intentionally extracts 0 secondary facts to eliminate hallucination risk.
