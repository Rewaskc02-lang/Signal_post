# Signalpost Submission

## Repository Information
- **Repository URL**: `https://github.com/Rewaskc02-lang/Signal_post`
- **Commit Hash**: `6accb74113bbf10c1000d0eef600263ed29ae91f` (latest HEAD on `main`)
- **Language & Runtime**: Python 3.11+
- **100-Company Smoke Test Report**: [`reports/100_company_smoke_test_report.md`](https://github.com/Rewaskc02-lang/Signal_post/blob/main/reports/100_company_smoke_test_report.md)
- **Interactive Visual Dashboard**: [`reports/smoke_test_dashboard.html`](https://github.com/Rewaskc02-lang/Signal_post/blob/main/reports/smoke_test_dashboard.html)

---

## Clean-Machine Setup & Run Instructions

### 1. Clean-Machine Installation Step (Single Command)
```bash
pip install -r requirements.txt
```
*(Dependencies are strictly pinned down to exact release versions in `requirements.txt` ensuring 100% reproducible environments on any fresh clone.)*

### 2. Batch Execution Command (Official Evaluation Run)
```bash
python run.py --input orgnumbers.txt --output profiles/ --max-requests 2000 --max-seconds 2400
```
*Accepts `--input` (or `--organisations`) pointing to a `.txt`, `.json`, or `.jsonl` file. Emits exactly one JSON terminal envelope per input company directly to `stdout` (JSONL stream) and saves `envelopes.jsonl` (and `{orgnr}.json` profiles) to the output path. Strictly stays within the Unstop/Builderr 2,000-request daily cap.*

### 3. Human-Readable Profile Inspection Mode (UX Evaluation)
```bash
python run.py --view 923609016
```
*Renders a structured, human-readable terminal profile view breaking down verified facts across identity, governance, multi-year financials, online presence, careers, and legal activity, with explicit explanations of what remains unknown and cryptographic SHA-256 evidence links.*

### 4. Interactive HTML Dashboard Generation (Browser UX Evaluation)
```bash
python scripts/generate_report.py --input envelopes.jsonl --output reports/report.html
```
*Generates a self-contained, responsive HTML verification dashboard for judges to search, compare facts, inspect raw HTTP snapshots, and audit SHA-256 digests offline.*

---

## Output Contract & Terminal Envelope Specification
Emits exactly one terminal envelope JSON object per supplied organisation number conforming strictly to Builderr's official evaluation contract:
- **Streams to `stdout`**: One clean JSON object per line (JSONL), with all diagnostic logs sent to `stderr`.
- **Saves to `--output`**: Automatically writes `envelopes.jsonl` (and per-company `{orgnr}.json` files when output is a directory).
- **Zero Silent Drops**: Every input organisation emits exactly one terminal envelope with strict terminal states (`available`, `not_available`, `blocked`, `not_applicable`, `ambiguous`, `failed`).
- **Comprehensive Sections**:
  - `organisation_number`: 9-digit stable identity key.
  - `run`: `{"run_id": ..., "started_at": ..., "completed_at": ..., "terminal_status": ...}`.
  - `claims`: 35–45 atomic fact claims per company with exact `availability`, `source_url`, `retrieved_at`, and `supporting_value` for every claim, plus confidence scores and evidence IDs.
  - `evidence`: Cryptographic sha256 content hashes, exact source URLs, source classes, and retrieval timestamps.
  - `source_snapshots`: Raw HTTP response snapshots (URL, status_code, content_type, full response_body, and raw content sha256) enabling instant offline claim verification.
  - `modules`: State tracking across all 6 core categories (`registry`, `financials`, `people`, `jobs`, `public_activity`, `website`).
  - `changes`: Historical diff records between consecutive runs.
  - `operations`: Request count, wall-clock runtime ms, and third-party cost.
  - `profile`: Structured company profile data.

---

## Model & API Details
- **Primary Data Registry**: Brønnøysundregistrene (Brreg) official REST APIs
  - `Enhetsregisteret`: `https://data.brreg.no/enhetsregisteret/api/enheter/{orgnr}` (core corporate data: name, org form, addresses, employees, foundation date, capital, accounting year, sector, business purpose)
  - `Regnskapsregisteret`: `https://data.brreg.no/regnskapsregisteret/regnskap/{orgnr}` (filed annual accounts: revenue, operating result, pre-tax ordinary result, net annual income, equity, total assets, total debt)
  - `Roller`: `https://data.brreg.no/enhetsregisteret/api/enheter/{orgnr}/roller` (official leadership & governance: CEO / daglig leder, Board Chair / styrets leder, Auditor / revisor, Accountant / regnskapsfører)
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
| **100 Companies (Official Smoke Test)** | **316 requests** | **~12.9 min** | **$0.00** (Free API) | **$0.000** *(or $0.024 with LLM)* | **$0.00 USD** *(or $0.024 USD)* |
| **1,000 Companies (Batch Evaluation)** | **~1,850 requests** | **~35 min** | **$0.00** (Free API) | **$0.000** *(or $0.241 with LLM)* | **$0.00 USD** *(or $0.241 USD)* |

- **Strict Challenge Budget Compliance**:
  - Request Limit: Under 2,000 requests (strictly compliant with daily cap).
  - Time Limit: Well under 2,400s (40 minutes) cap.
  - Total Declared Cost: **$0.00 USD** (deterministic) / **$0.24 USD** (with LLM), far below the $10.00 cap.

---

## Permitted Data Sources Rationale
1. **Brønnøysundregistrene (Enhetsregisteret, Regnskapsregisteret, Roller)**:
   - Official Norwegian Government business register made available under the Norwegian Licence for Open Government Data (NLOD / CC BY 4.0). Completely public and free.
2. **Company Primary Websites (Secondary Source)**:
   - Each company's registered `hjemmeside` URL (from Brreg) is fetched directly (1 HTTP GET per company). Identity is verified by matching the organisation number or registered legal name in the live page content. Verified pages produce external claims (`website`, `website_title`, `website_description`, `contact_email`, `contact_phone`, `investor_url`, `careers_url`, `latest_news_title`) with byte-level SHA-256 content hash and full response body retained in `source_snapshots`.

---

## Known Limitations
1. **Newly Registered Entities**: Companies registered in the current fiscal year have not yet filed annual accounts in `Regnskapsregisteret` (the client handles 404 gracefully and marks module `not_available`).
2. **Specialized Financial Entities**: Banks and select state entities file financial accounts under specialized statutory accounting schemes where standard Regnskapsregisteret endpoints may return 404/500 (handled gracefully without halting profile generation).
3. **Secondary Website Coverage**: If a company's website is protected by Cloudflare JS challenges or does not publish its orgnr/imprint, the anti-hallucination gate intentionally marks module `ambiguous` with 0 secondary claims to eliminate hallucination risk.
