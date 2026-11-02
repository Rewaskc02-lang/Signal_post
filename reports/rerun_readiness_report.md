# Signalpost rerun-readiness report

**Checked:** 2026-11-02  
**Code revision requested for evaluation:** `4d01524d86ec94cc3b518305ae0bb36c67e897ad`  
**Latest local documentation revision:** `6c7d964381df06153cf515c86783a901c5dbee62`

## Delivery status

- Local `main` is five commits ahead of `origin/main`.
- `origin/main` currently resolves to `84f9bf36ab7d76c52d5dd93754c8b59da73c011e`.
- The requested code revision is an ancestor of local `HEAD`, but it is not present on `origin/main`.
- The local repository cannot confirm which commit Builderr will use for a weekend rerun. Builderr must confirm receipt of the exact hash through its required submission channel.
- The documentation identifies `4d01524d...`, not the later documentation-only `6c7d964...` revision. Do not assume the latest local `HEAD` is scheduled.

## C300 availability

The actual Builderr C300 input file and evaluator are not present in this workspace. The 1,000-number `orgnumbers.txt` file is a local corpus, not evidence that it is the C300 set. No C300 recall or score is claimed here.

## Measured live run

Command, using a temporary output directory and the first ten local input numbers:

```bash
python run.py --input <temporary-input.txt> \
  --output <temporary-output/> \
  --max-requests 100 --max-seconds 180 --quiet \
  --json-report <temporary-run.json>
```

| Metric | Result |
|---|---:|
| Inputs | 10 |
| Terminal envelopes | 10 |
| Input order preserved | Yes |
| Available / not available / failed | 10 / 0 / 0 |
| Outbound requests | 32 |
| Runtime | 5.79 seconds |
| Estimated third-party cost | $0.00 |
| Available claims without evidence | 0 |
| Claims with empty source URL | 0 |
| Summary claims with semicolon-joined URL | 0 |
| Financial missing values represented as zero | 0 |

This sample demonstrates execution behavior only. It is not a C300 recall measurement.

## Existing 100-company artifact

The checked-in smoke artifact contains 100 envelopes, 100/100 contract validation in its generating script, 4,088 claims, 300 evidence records, and 300 snapshots. Its run report records 316 outbound requests. The per-envelope `operations.requests` values are cumulative/overlapping under concurrent execution and must not be summed as a total; the run-level report is the authoritative total.

The existing artifact is useful for contract and provenance inspection, but it does not provide independently checked recall. It also predates the current 10-company measurement and should not be presented as a C300 result.

## Fact-family coverage available locally

From the existing 100-envelope artifact, the following are observed claim families, not externally verified recall:

| Family | Companies with at least one available claim | Available claims |
|---|---:|---:|
| Identity/status | 100 | 497 |
| People/roles | 100 | 250 |
| Financials | 100 | 1,870 |
| Website/jobs/activity | 100 | 33 |

These counts measure Signalpost output only. They do not measure misses against Builderr’s checked union.

## Browser checks

Using the local served UI in a real browser:

- Dashboard loaded 1,002 local profile entries without page errors.
- Search by company name returned the expected company and organisation number.
- Status and category controls rendered and populated.
- Profile navigation opened a readable profile page rather than raw JSON.
- Profile pages displayed fact values, source URLs, retrieval timestamps, periods, and SHA-256 hashes.
- A 320px viewport showed no horizontal body overflow.
- The C300-specific 300-profile output was not available, so empty-state behavior for that exact batch remains unverified.
- Comparison controls are present in the current UI, but an end-to-end two-profile interaction was not accepted by the shared browser automation and remains a follow-up verification item.

## Remaining risks

1. Builderr delivery/scheduling of `4d01524...` is unconfirmed.
2. Actual C300 input and independently checked reference findings are unavailable locally.
3. The 1,850-request estimate for 1,000 companies is not a measured result.
4. Website, jobs, and public-activity recall remains the largest expected scoring risk.
5. Per-envelope request counts should not be used for aggregate resource reporting under concurrent execution.

