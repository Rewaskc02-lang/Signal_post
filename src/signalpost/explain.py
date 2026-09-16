"""Natural language explanation generation and token-monitored company summarizer."""

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
        return f"Official registered legal name in Enhetsregisteret."

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
        return f"Official website registered with Brønnøysundregistrene: {val}."

    if field == "website_description":
        return f"Verified secondary description extracted directly from company homepage."

    if field == "vat_registered":
        status_text = "Registered in the Norwegian VAT register (MVA-registeret)." if val else "Not registered in the VAT register."
        return status_text

    if field == "status":
        return f"Official legal status: {str(val).upper()}."

    # Generic fallback
    return f"Verified fact from {fact.source_name} ({fact.confidence})."


def generate_deterministic_summary(profile: CompanyProfile) -> str:
    """Generate a clean 2-3 sentence summary using deterministic rules (zero LLM cost)."""
    facts_map = {f.field_name: f for f in profile.facts}

    legal_name = facts_map.get("legal_name").value if "legal_name" in facts_map else f"Organisation {profile.orgnr}"
    org_form = facts_map.get("org_form_description").value if "org_form_description" in facts_map else "business entity"
    nace_desc = facts_map.get("nace_description").value if "nace_description" in facts_map else None
    status = facts_map.get("status").value if "status" in facts_map else "active"

    sentence_1 = f"{legal_name} is a Norwegian {org_form} (org. nr. {profile.orgnr}) currently with '{status}' legal status."

    if nace_desc:
        sentence_2 = f"The company's primary registered activity is {nace_desc.lower()}."
    else:
        sentence_2 = "The company is officially registered in Brønnøysundregistrene."

    # Financial sentence
    rev_fact = facts_map.get("revenue")
    emp_fact = facts_map.get("employee_count")

    financial_parts = []
    if emp_fact and emp_fact.value:
        financial_parts.append(f"{emp_fact.value:,} registered employees")
    if rev_fact and rev_fact.value is not None:
        year_str = str(rev_fact.as_of.year) if isinstance(rev_fact.as_of, (date, datetime)) else ""
        val_str = f"{rev_fact.value:,.0f} {rev_fact.unit or 'NOK'}"
        year_clause = f" for fiscal year {year_str}" if year_str else ""
        financial_parts.append(f"revenue of {val_str}{year_clause}")

    if financial_parts:
        sentence_3 = f"Latest registry records indicate {' and '.join(financial_parts)}."
    else:
        sentence_3 = "No annual accounts or employee records are currently filed in public registries."

    return f"{sentence_1} {sentence_2} {sentence_3}"


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
    model: str = "gemini-2.5-flash",
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
    client: httpx.AsyncClient | None = None,
) -> CompanySummaryResult:
    """Generate a summary for a company profile, with graceful fallback to deterministic templates.

    Args:
        profile: The CompanyProfile containing verified facts.
        enable_llm: Whether to attempt LLM summarization (defaults to settings.enable_llm_summary).
        api_key: LLM API key (if None, reads from env or settings).
        model: Model name to use.
        client: Optional httpx.AsyncClient for testing.

    Returns:
        CompanySummaryResult with summary text, token metrics, and cost estimate.
    """
    should_use_llm = (
        enable_llm
        if enable_llm is not None
        else default_settings.enable_llm_summary
    )
    llm_key = api_key or default_settings.llm_api_key or os.getenv("GEMINI_API_KEY") or os.getenv("OPENAI_API_KEY")
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

    # Attempt LLM call via provider (Gemini / OpenAI API compatible)
    try:
        should_close_client = False
        http_client = client
        if http_client is None:
            http_client = httpx.AsyncClient(timeout=httpx.Timeout(15.0))
            should_close_client = True

        try:
            # Standard Gemini REST API or OpenAI-compatible endpoint
            # We support both: if model starts with 'gemini', use Google Generative Language API
            if "gemini" in target_model.lower():
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{target_model}:generateContent?key={llm_key}"
                payload = {
                    "contents": [
                        {"role": "user", "parts": [{"text": f"{system_prompt}\n\n{user_prompt}"}]}
                    ],
                    "generationConfig": {"temperature": 0.2, "maxOutputTokens": 250},
                }
                resp = await http_client.post(url, json=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    candidate = data["candidates"][0]["content"]["parts"][0]["text"].strip()
                    usage = data.get("usageMetadata", {})
                    p_tokens = usage.get("promptTokenCount", len(user_prompt) // 4)
                    c_tokens = usage.get("candidatesTokenCount", len(candidate) // 4)
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
            else:
                # OpenAI-compatible API
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
                if resp.status_code == 200:
                    data = resp.json()
                    candidate = data["choices"][0]["message"]["content"].strip()
                    usage = data.get("usage", {})
                    p_tokens = usage.get("prompt_tokens", len(user_prompt) // 4)
                    c_tokens = usage.get("completion_tokens", len(candidate) // 4)
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

        finally:
            if should_close_client and not http_client.is_closed:
                await http_client.aclose()

    except Exception:
        # Graceful degradation on network/API failure
        pass

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
