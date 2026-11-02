# Signalpost Submission

## Repository Information
- **Repository URL**: `https://github.com/Rewaskc02-lang/Signal_post`
- **Commit Hash**: `4d01524d86ec94cc3b518305ae0bb36c67e897ad`
- **Language & Runtime**: Python 3.11+
- **100-Company Smoke Test Report**: [`reports/100_company_smoke_test_report.md`](https://github.com/Rewaskc02-lang/Signal_post/blob/main/reports/100_company_smoke_test_report.md)
- **Interactive Visual Dashboard**: [`reports/ui/index.html`](https://github.com/Rewaskc02-lang/Signal_post/blob/main/reports/ui/index.html)

---

## Clean-Machine Setup & Run Instructions

### 1. Clean-Machine Installation Step (Single Command)
```bash
pip install -r requirements.txt
```
*(Dependencies are strictly pinned down to exact release versions in `requirements.txt` ensuring 100% reproducible environments on any fresh clone.)*

### 2. Batch Execution Command (Official Evaluation Run)
```bash
python run.py --input <builderr-supplied-company-file> --output /tmp/signalpost-run/ --max-requests 2000 --max-seconds 2400
```
*The evaluator supplies the company file through `--input`; local testing can use `orgnumbers.txt`. Accepts `.txt`, `.json`, or `.jsonl`, preserves input order in the stdout JSONL stream, and writes to a temporary output directory so existing artifacts are not overwritten.*

### 3. Human-Readable Profile Inspection Mode (UX Evaluation)
```bash
python run.py --view 923609016
```
*Renders a structured, human-readable terminal profile view breaking down verified facts across identity, governance, multi-year financials, online presence, careers, and legal activity, with explicit explanations of what remains unknown and cryptographic SHA-256 evidence links.*

### 4. Interactive HTML Dashboard Generation (Browser UX Evaluation)
```bash
python scripts/generate_ui_manifest.py --profiles profiles/ --output reports/ui/profiles_index.json
```
*The generated `reports/ui/index.html` dashboard supports company/org-number/city/industry search, status/category filters, and readable profile pages with source links and SHA-256 evidence.*

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
| **100 Companies (recorded smoke run)** | **316 requests** | **Recorded in run report** | **$0.00** (Free API) | **$0.000** deterministic | **$0.00 USD** |
| **10 Companies (measured representative run)** | **32 requests** | **5.79s** | **$0.00** (Free API) | **$0.000** deterministic | **$0.00 USD** |

- **Strict Challenge Budget Compliance**:
  - Request Limit: The recorded 100-company run used 316 requests; the 10-company measured run used 32. The 1,000-company estimate is not claimed as measured.
  - Time Limit: The recorded 100-company run and measured 10-company run completed within the configured cap.
  - Total Declared Cost: **$0.00 USD** with deterministic summaries.

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
