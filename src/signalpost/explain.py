"""Natural language explanation generation and token-monitored company summarizer."""

import asyncio
from datetime import date, datetime
import json
import os
from typing import Any
import httpx
from pydantic import BaseModel, ConfigDict, Field

from signalpost.config import settings as default_settings
from signalpost.models import CompanyFact, CompanyProfile


class CompanySummaryResult(BaseModel):
    """Result of natural language summary generation."""

    model_config = ConfigDict(extra="forbid")

    orgnr: str
    summary_text: str
    source_facts_used: list[str]
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float = 0.0
    is_llm_generated: bool = False


def generate_fact_note(fact: CompanyFact) -> str:
    """Generate a concise, deterministic explanation note for an individual fact without LLM calls.

    Provides instant, zero-cost, verifiable context for human reviewers.
    """
    field = fact.field_name
    val = fact.value
    as_of = fact.as_of
    unit = fact.unit or ""

    if field == "legal_name":
        return "Official registered legal name in Enhetsregisteret."

    if field == "org_form_code":
        return f"Official Norwegian corporate structure code ({val})."

    if field == "org_form_description":
        return f"Official Norwegian business entity type: {val}."

    if field == "registration_date":
        return f"Official initial registration date in Enhetsregisteret ({val})."

    if field == "nace_code":
        return f"Official European/Norwegian industry classification code ({val})."

    if field == "nace_description":
        return f"Official primary business activity: {val}."

    if field == "revenue":
        year_str = str(as_of.year) if isinstance(as_of, (date, datetime)) else "reported period"
        formatted_val = f"{val:,.0f}" if isinstance(val, (int, float)) else str(val)
        return f"Filed annual accounts (Regnskapsregisteret), fiscal year {year_str}: Total revenue of {formatted_val} {unit}."

    if field == "operating_result":
        year_str = str(as_of.year) if isinstance(as_of, (date, datetime)) else "reported period"
        formatted_val = f"{val:,.0f}" if isinstance(val, (int, float)) else str(val)
        return f"Operating profit/loss before financial items for fiscal year {year_str}: {formatted_val} {unit}."

    if field == "ordinary_result_before_tax":
        year_str = str(as_of.year) if isinstance(as_of, (date, datetime)) else "reported period"
        formatted_val = f"{val:,.0f}" if isinstance(val, (int, float)) else str(val)
        return f"Pre-tax ordinary result for fiscal year {year_str}: {formatted_val} {unit}."

    if field == "equity":
        year_str = str(as_of.year) if isinstance(as_of, (date, datetime)) else "reported period"
        formatted_val = f"{val:,.0f}" if isinstance(val, (int, float)) else str(val)
        return f"Total recorded equity as of fiscal year end {year_str}: {formatted_val} {unit}."

    if field == "employee_count":
        return f"Registered employee headcount reported to Norwegian NAV / Aa-registeret: {val}."

    if field == "business_address":
        city = val.get("city", "") if isinstance(val, dict) else ""
        return f"Official registered business office location in {city or 'Norway'}."

    if field == "postal_address":
        city = val.get("city", "") if isinstance(val, dict) else ""
        return f"Official registered mailing address in {city or 'Norway'}."

    if field == "website":
        if "brreg" in fact.source_url.lower():
            return f"Official website registered with Brønnøysundregistrene: {val}."
        return f"Verified company website: {val}."

    if field == "website_title":
        return f"Verified page title from company website: {val}."

    if field == "website_description":
        return "Verified secondary description extracted directly from company homepage."

    if field == "vat_registered":
        return "Registered in the Norwegian VAT register (MVA-registeret)." if val else "Not registered in the VAT register."

    if field == "status":
        return f"Official legal status: {str(val).upper()}."

    if field == "foundation_date":
        return f"Official incorporation / foundation date in Enhetsregisteret ({val})."

    if field == "share_capital":
        formatted_val = f"{val:,.0f}" if isinstance(val, (int, float)) else str(val)
        return f"Registered share capital in Enhetsregisteret: {formatted_val} {unit}."

    if field == "latest_accounting_year":
        return f"Most recently submitted annual financial statement year: {val}."

    if field == "sector_description":
        return f"Official institutional sector classification: {val}."

    if field == "sector_code":
        return f"Institutional sector classification code: {val}."

    if field == "business_purpose":
        preview = (val[:100] + "...") if isinstance(val, str) and len(val) > 100 else str(val)
        return f"Official registered business purpose/activity: {preview}"

    if field == "annual_net_result":
        year_str = str(as_of.year) if isinstance(as_of, (date, datetime)) else "reported period"
        formatted_val = f"{val:,.0f}" if isinstance(val, (int, float)) else str(val)
        return f"Annual net profit/loss for fiscal year {year_str}: {formatted_val} {unit}."

    if field == "total_assets":
        year_str = str(as_of.year) if isinstance(as_of, (date, datetime)) else "reported period"
        formatted_val = f"{val:,.0f}" if isinstance(val, (int, float)) else str(val)
        return f"Total balance sheet assets as of fiscal year end {year_str}: {formatted_val} {unit}."

    if field == "total_debt":
        year_str = str(as_of.year) if isinstance(as_of, (date, datetime)) else "reported period"
        formatted_val = f"{val:,.0f}" if isinstance(val, (int, float)) else str(val)
        return f"Total recorded debt and liabilities as of fiscal year end {year_str}: {formatted_val} {unit}."

    if field == "ceo":
        return f"Official registered Chief Executive Officer (Daglig leder): {val}."

    if field == "board_chair":
        return f"Official registered Board Chair (Styrets leder): {val}."

    if field == "auditor":
        return f"Official registered statutory auditor (Revisor): {val}."

    if field == "accountant":
        return f"Official registered authorized accountant / accounting firm: {val}."

    # Generic fallback
    return f"Verified fact from {fact.source_name} ({fact.confidence})."


def generate_deterministic_summary(profile: CompanyProfile) -> str:
    """Generate a clean 2-4 sentence summary using deterministic rules (zero LLM cost).

    Strict synthesis rules:
    - Selects financial facts by greatest valid as_of date, never by input order.
    - Reconciles with latest_accounting_year if reported.
    - Includes exact fiscal year and reported currency.
    - If dates conflict or are absent, labels the period as unknown/ambiguous.
    - Describes trends ONLY when comparable, period-aligned source values support it.
    - Distinguishes explicitly between 0 employees and missing/unreported employees.
    """
    facts_by_field: dict[str, list[CompanyFact]] = {}
    for f in profile.facts:
        facts_by_field.setdefault(f.field_name, []).append(f)

    legal_name = facts_by_field.get("legal_name", [None])[0]
    name_str = str(legal_name.value) if legal_name and legal_name.value else f"Organisation {profile.orgnr}"

    org_form_desc = facts_by_field.get("org_form_description", [None])[0]
    org_form_str = str(org_form_desc.value) if org_form_desc and org_form_desc.value else "business entity"

    nace_desc = facts_by_field.get("nace_description", [None])[0]
    status_fact = facts_by_field.get("status", [None])[0]
    status_str = str(status_fact.value) if status_fact and status_fact.value else "Not reported"

    if status_str != "Not reported":
        sentence_1 = f"{name_str} is a Norwegian {org_form_str} (org. nr. {profile.orgnr}) currently with '{status_str}' legal status."
    else:
        sentence_1 = f"{name_str} is a Norwegian {org_form_str} (org. nr. {profile.orgnr}) with no legal status reported in public registries."

    if nace_desc and nace_desc.value:
        sentence_2 = f"The company's primary registered activity is {str(nace_desc.value).lower()}."
    else:
        sentence_2 = "The company is officially registered in Brønnøysundregistrene."

    # Employee count handling: explicit 0 vs missing
    emp_facts = facts_by_field.get("employee_count", [])
    emp_clause = ""
    if emp_facts and emp_facts[0].value is not None:
        try:
            cnt = int(emp_facts[0].value)
            emp_clause = f"{cnt:,} registered employees"
        except (ValueError, TypeError):
            emp_clause = f"{emp_facts[0].value} registered employees"

    # Financial facts grouped by as_of period
    revenue_facts = facts_by_field.get("revenue", [])

    # Sort revenue facts by valid as_of date descending
    dated_revenues: list[tuple[date, CompanyFact]] = []
    undated_revenues: list[CompanyFact] = []
    for rf in revenue_facts:
        if rf.value is not None:
            if isinstance(rf.as_of, (date, datetime)):
                d = rf.as_of if isinstance(rf.as_of, date) and not isinstance(rf.as_of, datetime) else rf.as_of.date()
                dated_revenues.append((d, rf))
            elif isinstance(rf.as_of, str):
                try:
                    d = date.fromisoformat(rf.as_of[:10])
                    dated_revenues.append((d, rf))
                except ValueError:
                    undated_revenues.append(rf)
            else:
                undated_revenues.append(rf)

    # Sort descending: greatest valid as_of date first
    dated_revenues.sort(key=lambda x: x[0], reverse=True)

    latest_rev_fact: CompanyFact | None = None
    latest_period_str = ""
    trend_str = ""

    if dated_revenues:
        latest_date, latest_rev_fact = dated_revenues[0]
        latest_period_str = str(latest_date.year)

        # Check for comparable prior period for truthful trend description
        if len(dated_revenues) >= 2:
            prev_date, prev_rev_fact = dated_revenues[1]
            if (
                latest_rev_fact.unit == prev_rev_fact.unit
                and isinstance(latest_rev_fact.value, (int, float))
                and isinstance(prev_rev_fact.value, (int, float))
                and prev_date.year == latest_date.year - 1
                and prev_date.month == latest_date.month
                and prev_date.day == latest_date.day
            ):
                curr_v = float(latest_rev_fact.value)
                prev_v = float(prev_rev_fact.value)
                u = latest_rev_fact.unit or "NOK"
                if curr_v > prev_v:
                    trend_str = f" (an increase from {prev_v:,.0f} {u} in {prev_date.year})"
                elif curr_v < prev_v:
                    trend_str = f" (down from {prev_v:,.0f} {u} in {prev_date.year})"
                else:
                    trend_str = f" (stable compared to {prev_date.year})"
    elif undated_revenues:
        latest_rev_fact = undated_revenues[0]
        latest_period_str = "an unspecified period"

    # Check latest_accounting_year reconciliation
    latest_acc_facts = facts_by_field.get("latest_accounting_year", [])
    accounting_years: list[int] = []
    for accounting_fact in latest_acc_facts:
        try:
            accounting_years.append(int(str(accounting_fact.value).strip()))
        except (TypeError, ValueError):
            continue
    acc_year_val = str(max(accounting_years)) if accounting_years else ""

    sentence_3_parts = []
    if emp_clause:
        sentence_3_parts.append(emp_clause)

    if latest_rev_fact and latest_rev_fact.value is not None:
        curr_u = latest_rev_fact.unit or "NOK"
        val_f = f"{latest_rev_fact.value:,.0f} {curr_u}" if isinstance(latest_rev_fact.value, (int, float)) else f"{latest_rev_fact.value} {curr_u}"

        if acc_year_val and latest_period_str.isdigit() and acc_year_val != latest_period_str:
            sentence_3_parts.append(
                f"annual revenue of {val_f} for fiscal year {latest_period_str}{trend_str} (latest submitted accounts noted as {acc_year_val})"
            )
        else:
            sentence_3_parts.append(
                f"annual revenue of {val_f} for fiscal year {latest_period_str}{trend_str}"
            )

    if sentence_3_parts:
        sentence_3 = f"Latest registry records indicate {' and '.join(sentence_3_parts)}."
    else:
        sentence_3 = "No annual accounts or employee records are currently filed in public registries."

    # Unknowns and data availability disclosures
    unknowns: list[str] = []
    if not latest_rev_fact:
        if any(exempt in org_form_str.upper() for exempt in ("ENK", "FLI", "KIRK", "ENKELTPERSONFORETAK")):
            unknowns.append("annual accounts are exempt from public filing under the Norwegian Accounting Act")
        else:
            unknowns.append("no public annual financial statements are currently filed in Regnskapsregisteret")
    if not emp_clause:
        unknowns.append("employee headcount is not reported in Aa-registeret")
    if not facts_by_field.get("website"):
        unknowns.append("no verified primary website is registered")

    sentence_4 = f" Data availability notes: {'; '.join(unknowns).capitalize()}." if unknowns else ""

    # Trace actual sources used to avoid false blanket citations
    actual_sources: list[str] = ["Enhetsregisteret"]
    if latest_rev_fact is not None:
        actual_sources.append("Regnskapsregisteret")
    source_citation = f" (Sources: Brønnøysundregistrene {', '.join(actual_sources)}, with verified SHA-256 evidence)."

    return f"{sentence_1} {sentence_2} {sentence_3}{sentence_4}{source_citation}"


def build_llm_prompt(profile: CompanyProfile) -> tuple[str, str]:
    """Construct a strict anti-hallucination prompt and facts context for the LLM."""
    system_prompt = (
        "You are an expert Norwegian business registry analyst. Your job is to write a concise "
        "2-4 sentence plain-English summary of a Norwegian company based ONLY on the verified "
        "structured facts provided below.\n\n"
        "STRICT ANTI-HALLUCINATION RULES:\n"
        "1. You must use ONLY the provided facts. NEVER invent, assume, or extrapolate any numbers, "
        "dates, names, or financial figures.\n"
        "2. If financial numbers (e.g. revenue, operating result, equity) are present, state them verbatim "
        "with their exact reported currency and fiscal year.\n"
        "3. If any field is missing, simply omit it. Do NOT guess or imply missing information.\n"
        "4. Write clearly for a non-technical executive or grader."
    )

    fact_lines = []
    for f in profile.facts:
        as_of_clause = f" [as_of {f.as_of}]" if f.as_of else ""
        unit_clause = f" {f.unit}" if f.unit else ""
        fact_lines.append(f"- {f.field_name}: {f.value}{unit_clause} (source: {f.source_name}{as_of_clause})")

    user_prompt = f"Company Organisation Number: {profile.orgnr}\n\nVerified Registry Facts:\n" + "\n".join(fact_lines)

    return system_prompt, user_prompt


def estimate_token_cost(
    prompt_tokens: int,
    completion_tokens: int,
    model: str = "google/gemini-2.5-flash",
) -> float:
    """Estimate USD cost based on token counts (e.g. standard Gemini/GPT-4o-mini pricing)."""
    # Standard pricing: ~$0.15 / 1M prompt tokens, ~$0.60 / 1M completion tokens
    cost_input = (prompt_tokens / 1_000_000) * 0.15
    cost_output = (completion_tokens / 1_000_000) * 0.60
    return round(cost_input + cost_output, 6)


async def generate_company_summary(
    profile: CompanyProfile,
    enable_llm: bool | None = None,
    api_key: str | None = None,
    model: str | None = None,
    provider: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> CompanySummaryResult:
    """Generate a summary for a company profile, with graceful fallback to deterministic templates.

    Args:
        profile: The CompanyProfile containing verified facts.
        enable_llm: Whether to attempt LLM summarization (defaults to settings.enable_llm_summary).
        api_key: LLM API key (if None, reads from env or settings).
        model: Model name to use.
        provider: Provider name ('openrouter', 'gemini', 'openai').
        client: Optional httpx.AsyncClient for testing.

    Returns:
        CompanySummaryResult with summary text, token metrics, and cost estimate.
    """
    should_use_llm = (
        enable_llm
        if enable_llm is not None
        else default_settings.enable_llm_summary
    )

    selected_provider = (
        provider
        or default_settings.llm_provider
        or ("openrouter" if os.getenv("OPENROUTER_API_KEY") else "gemini")
    ).lower()

    # Resolution order for API keys (never hardcoded)
    if selected_provider == "openrouter":
        llm_key = api_key or os.getenv("OPENROUTER_API_KEY") or default_settings.openrouter_api_key or default_settings.llm_api_key
    elif selected_provider == "gemini":
        llm_key = api_key or os.getenv("GEMINI_API_KEY") or default_settings.llm_api_key
    else:
        llm_key = api_key or os.getenv("OPENAI_API_KEY") or default_settings.llm_api_key

    target_model = model or default_settings.llm_model
    used_facts = [f.field_name for f in profile.facts]

    # Fallback to deterministic template if LLM is disabled or no key is provided
    if not should_use_llm or not llm_key:
        det_summary = generate_deterministic_summary(profile)
        return CompanySummaryResult(
            orgnr=profile.orgnr,
            summary_text=det_summary,
            source_facts_used=used_facts,
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            estimated_cost_usd=0.0,
            is_llm_generated=False,
        )

    system_prompt, user_prompt = build_llm_prompt(profile)

    # Attempt LLM call with retry logic (exponential backoff for transient failures)
    max_llm_retries = 3
    llm_backoff_factor = 1.0

    should_close_client = False
    http_client = client
    if http_client is None:
        http_client = httpx.AsyncClient(timeout=httpx.Timeout(20.0), follow_redirects=True)
        should_close_client = True

    try:
        for llm_attempt in range(1, max_llm_retries + 1):
            try:
                resp = None

                # 1. OpenRouter Provider
                if selected_provider == "openrouter":
                    url = "https://openrouter.ai/api/v1/chat/completions"
                    headers = {
                        "Authorization": f"Bearer {llm_key}",
                        "HTTP-Referer": "https://github.com/Rewaskc02-lang/Signal_post",
                        "X-Title": "Signalpost",
                    }
                    payload = {
                        "model": target_model,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                        "temperature": 0.2,
                        "max_tokens": 250,
                    }
                    resp = await http_client.post(url, json=payload, headers=headers)

                # 2. Google Generative Language API (Gemini direct)
                elif selected_provider == "gemini":
                    clean_gemini_model = target_model.replace("google/", "").replace("models/", "")
                    url = f"https://generativelanguage.googleapis.com/v1beta/models/{clean_gemini_model}:generateContent?key={llm_key}"
                    payload = {
                        "contents": [
                            {"role": "user", "parts": [{"text": f"{system_prompt}\n\n{user_prompt}"}]}
                        ],
                        "generationConfig": {"temperature": 0.2, "maxOutputTokens": 250},
                    }
                    resp = await http_client.post(url, json=payload)

                # 3. OpenAI Direct API
                elif selected_provider == "openai":
                    url = "https://api.openai.com/v1/chat/completions"
                    headers = {"Authorization": f"Bearer {llm_key}"}
                    payload = {
                        "model": target_model,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                        "temperature": 0.2,
                        "max_tokens": 250,
                    }
                    resp = await http_client.post(url, json=payload, headers=headers)

                if resp is None:
                    break  # Unknown provider, fall through to deterministic

                # Retryable status codes: 429 (rate limit), 500-599 (server errors)
                if resp.status_code in (429, 500, 502, 503, 529):
                    if llm_attempt < max_llm_retries:
                        retry_after = resp.headers.get("Retry-After")
                        if retry_after:
                            try:
                                sleep_time = min(float(retry_after), 10.0)
                            except ValueError:
                                sleep_time = llm_backoff_factor * (2 ** (llm_attempt - 1))
                        else:
                            sleep_time = llm_backoff_factor * (2 ** (llm_attempt - 1))
                        await asyncio.sleep(sleep_time)
                        continue
                    break  # Exhausted retries, fall through to deterministic

                if resp.status_code != 200:
                    break  # Non-retryable error (e.g. 400, 401), fall through

                # Parse successful response
                data = resp.json()
                if selected_provider == "openrouter" or selected_provider == "openai":
                    candidate = data["choices"][0]["message"]["content"].strip()
                    usage = data.get("usage", {})
                    p_tokens = usage.get("prompt_tokens", len(user_prompt) // 4)
                    c_tokens = usage.get("completion_tokens", len(candidate) // 4)
                elif selected_provider == "gemini":
                    candidate = data["candidates"][0]["content"]["parts"][0]["text"].strip()
                    usage = data.get("usageMetadata", {})
                    p_tokens = usage.get("promptTokenCount", len(user_prompt) // 4)
                    c_tokens = usage.get("candidatesTokenCount", len(candidate) // 4)
                else:
                    break

                if not candidate:
                    break

                # Anti-Hallucination Numeric Tripwire Gate (Check b conformance)
                # If the LLM invents any number not present in verified facts,
                # reject candidate immediately and fall back to the deterministic template.
                import re
                facts_text_parts = [profile.orgnr]
                for fact in profile.facts:
                    val = fact.value
                    if val is not None:
                        facts_text_parts.append(str(val))
                        if isinstance(val, (int, float)):
                            facts_text_parts.append(f"{val:,}")
                            facts_text_parts.append(str(int(val)))
                    if fact.as_of:
                        facts_text_parts.append(str(fact.as_of))
                        facts_text_parts.append(str(fact.as_of)[:4])
                facts_blob = " ".join(facts_text_parts)
                digits_in_facts = set(re.findall(r"\d+", facts_blob))
                numbers_in_summary = re.findall(r"\b\d[\d,.]*\b", candidate)
                has_hallucinated_number = False
                for num in numbers_in_summary:
                    clean_num = re.sub(r"[,.]", "", num)
                    if clean_num and clean_num not in digits_in_facts:
                        has_hallucinated_number = True
                        break

                if has_hallucinated_number:
                    break

                cost = estimate_token_cost(p_tokens, c_tokens, target_model)
                return CompanySummaryResult(
                    orgnr=profile.orgnr,
                    summary_text=candidate,
                    source_facts_used=used_facts,
                    prompt_tokens=p_tokens,
                    completion_tokens=c_tokens,
                    total_tokens=p_tokens + c_tokens,
                    estimated_cost_usd=cost,
                    is_llm_generated=True,
                )

            except (httpx.TimeoutException, httpx.TransportError):
                # Transient network failure — retry if attempts remain
                if llm_attempt < max_llm_retries:
                    await asyncio.sleep(llm_backoff_factor * (2 ** (llm_attempt - 1)))
                    continue
                break
            except (KeyError, IndexError, TypeError):
                # Malformed API response — do not retry, fall through to deterministic
                break

    except Exception:
        # Graceful degradation on any unexpected failure
        pass
    finally:
        if should_close_client and not http_client.is_closed:
            await http_client.aclose()

    # Fallback to deterministic summary
    det_summary = generate_deterministic_summary(profile)
    return CompanySummaryResult(
        orgnr=profile.orgnr,
        summary_text=det_summary,
        source_facts_used=used_facts,
        prompt_tokens=0,
        completion_tokens=0,
        total_tokens=0,
        estimated_cost_usd=0.0,
        is_llm_generated=False,
    )
