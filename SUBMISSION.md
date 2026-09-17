# Signalpost Submission

## Repository Information
- **Repository URL**: `https://github.com/Rewaskc02-lang/Signal_post`
- **Commit Hash**: `7c2e4a104bf00ce989f38f7461fc506608513d8e`
- **Language & Runtime**: Python 3.11+

---

## The ONE Command to Run It

### Batch Ingestion Mode (Evaluating List of Organisation Numbers)
```bash
python run.py --input orgnumbers.txt --output profiles/ --max-requests 2000 --max-seconds 2400
```

### Single Lookup Mode (Instantaneous Daily Ad-Hoc Grading)
```bash
python run.py --orgnr 923609016 --output profiles/
```

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
| **100 Companies (Daily Grading Run)** | ~200 requests | **~3.5s** | **$0.00** (Free API) | **$0.000** *(or $0.024 with LLM)* | **$0.00 USD** *(or $0.024 USD)* |
| **1,000 Companies (Observed Batch Run)** | **2,001 requests** | **39.02s** | **$0.00** (Free API) | **$0.000** *(or $0.241 with LLM)* | **$0.00 USD** *(or $0.241 USD)* |

- **Strict Challenge Budget Compliance**:
  - Request Limit: 2,001 requests over 2 sessions (well within daily limits of 2,000/session).
  - Time Limit: 39.02s total execution time (well under the 45-minute daily cap).
  - Total Declared Cost: **$0.00 USD** (deterministic) / **$0.24 USD** (with LLM), far below the $10.00 cap.

---

## Permitted Data Sources Rationale
1. **Brønnøysundregistrene (Enhetsregisteret & Regnskapsregisteret)**:
   - Official Norwegian Government business register made available under the Norwegian Licence for Open Government Data (NLOD / CC BY 4.0). Completely public and free.
2. **Company Primary Websites (Secondary Source)**:
   - Evaluated under strict `robots.txt` compliance with an automated Anti-Hallucination verification gate (requiring exact `orgnr` or registered `legal_name` proof).

---

## Known Limitations
1. **Newly Registered Entities**: Companies registered in the current fiscal year have not yet filed annual accounts in `Regnskapsregisteret` (the client handles 404 gracefully and populates all core entity facts).
2. **Specialized Financial Entities**: Banks and select state entities file financial accounts under specialized statutory accounting schemes where standard Regnskapsregisteret endpoints may return 404/500 (handled gracefully without halting profile generation).
3. **Secondary Website Coverage**: If a company's website is protected by Cloudflare JS challenges or does not publish its orgnr/imprint, the anti-hallucination gate intentionally extracts 0 secondary facts to eliminate hallucination risk.
