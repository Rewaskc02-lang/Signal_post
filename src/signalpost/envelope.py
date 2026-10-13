"""Terminal Envelope builder conforming to Builderr evaluation contract and validation rules."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
import re
from typing import Any

from signalpost.differ import ProfileUpdateSummary
from signalpost.models import CompanyFact, CompanyProfile

# Official Builderr Evaluation Contract States:
# Exactly 6 valid states: available, not_available, blocked, not_applicable, ambiguous, failed.
CONTRACT_STATES: set[str] = {
    "available",
    "not_available",
    "blocked",
    "not_applicable",
    "ambiguous",
    "failed",
}

# Backward compatibility alias
TERMINAL_STATES = CONTRACT_STATES


class ContractValidationError(ValueError):
    """Raised when an emitted envelope violates Builderr's output contract."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__(f"Contract validation failed with {len(errors)} error(s): {'; '.join(errors)}")


def utc_iso(dt: datetime | None = None) -> str:
    """Format datetime as UTC ISO-8601 string ending with Z."""
    if dt is None:
        dt = datetime.now(timezone.utc)
    elif dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")


def normalize_contract_state(state: str | None) -> str:
    """Normalize any internal or legacy status into the 6 official contract states.

    - available: Information found and verified.
    - not_available: Checked, nothing found (e.g. 404 in registry).
    - blocked: Access prohibited by robots.txt, 401/403, or restricted domain policy.
    - not_applicable: Category does not apply to this entity type.
    - ambiguous: Candidate found but identity uncertain / unverified.
    - failed: System error, format/checksum error, budget exhaustion, or crash.
    """
    if not state:
        return "failed"
    s = str(state).lower().strip()
    if s in {"available", "complete", "completed", "success", "ok"}:
        return "available"
    if s in {"not_available", "not_found", "missing", "404", "empty"}:
        return "not_available"
    if s in {"blocked", "blocked_robots", "blocked_policy", "401", "403", "forbidden"}:
        return "blocked"
    if s in {"not_applicable", "n/a", "na", "inapplicable"}:
        return "not_applicable"
    if s in {"ambiguous", "uncertain", "conflict", "match_unverified"}:
        return "ambiguous"
    if s in {"failed", "source_error", "submission_error", "budget_exhausted", "error", "timeout", "crash"}:
        return "failed"
    return "failed"


def validate_envelope(envelope: dict[str, Any]) -> tuple[bool, list[str]]:
    """Validate that a terminal envelope strictly complies with Builderr's contract.

    Returns:
        (is_valid, error_list)

    Raises:
        ContractValidationError: If is_valid is False and raise_on_error is needed.
    """
    errors: list[str] = []

    if not isinstance(envelope, dict):
        return False, ["Envelope must be a JSON dictionary"]

    # 1. Organisation number
    orgnr = envelope.get("organisation_number")
    if not isinstance(orgnr, str) or not orgnr.strip():
        errors.append("Missing or invalid 'organisation_number' field")

    # 2. Top-level state
    state = envelope.get("state")
    if state not in CONTRACT_STATES:
        errors.append(f"Invalid top-level state '{state}'. Must be one of: {sorted(CONTRACT_STATES)}")

    # 3. Run metadata
    run = envelope.get("run")
    if not isinstance(run, dict):
        errors.append("Missing 'run' metadata dictionary")
    else:
        for req_key in ("run_id", "started_at", "completed_at", "terminal_status"):
            if req_key not in run:
                errors.append(f"Missing '{req_key}' in 'run' object")
        if run.get("terminal_status") not in CONTRACT_STATES:
            errors.append(f"Invalid run.terminal_status '{run.get('terminal_status')}'. Must be in: {sorted(CONTRACT_STATES)}")

    # 4. Modules tracking
    modules = envelope.get("modules")
    if not isinstance(modules, dict):
        errors.append("Missing 'modules' dictionary")
    else:
        for mod_name, mod_info in modules.items():
            if not isinstance(mod_info, dict):
                errors.append(f"Module '{mod_name}' must be an object")
                continue
            m_state = mod_info.get("state")
            if m_state not in CONTRACT_STATES:
                errors.append(f"Module '{mod_name}' has invalid state '{m_state}'. Must be in: {sorted(CONTRACT_STATES)}")

    # 5. Claims
    claims = envelope.get("claims")
    if not isinstance(claims, list):
        errors.append("'claims' must be a list")
    else:
        for idx, claim in enumerate(claims):
            if not isinstance(claim, dict):
                errors.append(f"Claim at index {idx} is not an object")
                continue
            if "field" not in claim:
                errors.append(f"Claim at index {idx} missing 'field'")
            c_avail = claim.get("availability")
            if c_avail not in CONTRACT_STATES:
                errors.append(f"Claim '{claim.get('field')}' has invalid availability '{c_avail}'")
            # Prohibit fabricated zeroes for missing financial fields
            if c_avail in {"not_available", "not_applicable", "blocked", "ambiguous", "failed"}:
                val = claim.get("value")
                if val == 0 and claim.get("field") in {"revenue", "operating_result", "ordinary_result_before_tax", "equity"}:
                    errors.append(f"Claim '{claim.get('field')}' has zero value for non-available state '{c_avail}' (fabricated zero prohibited)")

    # 6. Evidence
    evidence = envelope.get("evidence")
    if not isinstance(evidence, list):
        errors.append("'evidence' must be a list")
    else:
        for idx, ev in enumerate(evidence):
            if not isinstance(ev, dict):
                errors.append(f"Evidence at index {idx} is not an object")
                continue
            for k in ("id", "source_url", "source_class", "retrieved_at", "content_sha256", "claim_span"):
                if k not in ev:
                    errors.append(f"Evidence item {idx} missing required field '{k}'")

    # 7. Operations
    ops = envelope.get("operations")
    if not isinstance(ops, dict):
        errors.append("Missing 'operations' dictionary")
    else:
        for op_key in ("requests", "runtime_ms", "third_party_cost_usd"):
            if op_key not in ops:
                errors.append(f"Missing operations field '{op_key}'")

    # 8. Source snapshots, changes, errors
    for list_field in ("source_snapshots", "changes", "errors"):
        if not isinstance(envelope.get(list_field), list):
            errors.append(f"Field '{list_field}' must be a list")

    return (len(errors) == 0, errors)


def build_terminal_envelope(
    orgnr: str,
    run_id: str,
    started_at: datetime,
    completed_at: datetime,
    profile: CompanyProfile | None = None,
    diff_summary: ProfileUpdateSummary | None = None,
    request_count: int = 0,
    runtime_ms: float = 0.0,
    cost_usd: float = 0.0,
    error: str | None = None,
    terminal_state: str | None = None,
    snapshots: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Emit exactly one unified terminal envelope conforming to Builderr's evaluation contract.

    Enforces:
    - Exactly one of the 6 official states: available, not_available, blocked, not_applicable, ambiguous, failed.
    - Explicit status representation for every category (registry, financials, people, jobs, public_activity, website).
    - Provenance linking each claim to evidence IDs and raw SHA-256 snapshots.
    - Zero fabricated zeroes for missing information.
    """
    started_iso = utc_iso(started_at)
    completed_iso = utc_iso(completed_at)

    claims: list[dict[str, Any]] = []
    evidence_list: list[dict[str, Any]] = []
    changes: list[dict[str, Any]] = []
    errors: list[str] = [str(error)] if error else []

    # Initialize all 6 official modules with explicit status tracking
    modules: dict[str, dict[str, Any]] = {
        "registry": {
            "state": "failed" if error else "available",
            "retry_count": 0,
            "final_timestamp": completed_iso,
        },
        "financials": {
            "state": "not_applicable",
            "retry_count": 0,
            "final_timestamp": completed_iso,
        },
        "people": {
            "state": "not_applicable",
            "retry_count": 0,
            "final_timestamp": completed_iso,
        },
        "jobs": {
            "state": "not_applicable",
            "retry_count": 0,
            "final_timestamp": completed_iso,
        },
        "public_activity": {
            "state": "not_available",
            "retry_count": 0,
            "final_timestamp": completed_iso,
        },
        "website": {
            "state": "not_applicable",
            "retry_count": 0,
            "final_timestamp": completed_iso,
        },
    }

    if profile is not None and not error:
        # Determine presence of facts per category
        has_financials = any(
            f.field_name in {"revenue", "operating_result", "ordinary_result_before_tax", "equity", "net_income", "total_assets", "total_debt"}
            for f in profile.facts
        )
        has_roles = any(
            f.field_name in {"ceo", "board_chair", "auditor", "accountant"}
            for f in profile.facts
        )
        has_jobs = any(
            f.field_name in {"careers_url", "hiring_status", "job_openings_count", "job_titles"}
            for f in profile.facts
        )
        has_public_activity = any(
            f.field_name in {"latest_news_title", "latest_news_date", "registry_last_updated", "legal_status", "founded_date"}
            for f in profile.facts
        )
        has_website = any(f.field_name == "website" or "website" in f.source_name.lower() for f in profile.facts)

        web_src_status = (profile.source_statuses or {}).get("website", "")
        regnskap_src_status = (profile.source_statuses or {}).get("regnskapsregisteret", "")
        roller_src_status = (profile.source_statuses or {}).get("roller", "")
        jobs_src_status = (profile.source_statuses or {}).get("jobs", "")
        activity_src_status = (profile.source_statuses or {}).get("public_activity", "")

        org_form = next((str(f.value).upper() for f in profile.facts if f.field_name == "org_form"), "")
        is_accounts_exempt = any(exempt in org_form for exempt in ("ENK", "FLI", "KIRK", "SA", "BA", "BRL"))

        # 1. Registry module
        modules["registry"]["state"] = "available"

        # 2. Financials module
        if has_financials:
            modules["financials"]["state"] = "available"
        elif is_accounts_exempt:
            modules["financials"]["state"] = "not_applicable"
        elif regnskap_src_status in ("missing", "not_found", "not_available"):
            modules["financials"]["state"] = "not_available"
        else:
            modules["financials"]["state"] = "not_available"

        # 3. People / Governance module
        if has_roles:
            modules["people"]["state"] = "available"
        elif roller_src_status in ("missing", "not_found", "not_available"):
            modules["people"]["state"] = "not_available"
        else:
            modules["people"]["state"] = "not_available"

        # 4. Website module
        if has_website:
            modules["website"]["state"] = "available"
        elif web_src_status in ("blocked", "blocked_robots", "blocked_policy"):
            modules["website"]["state"] = "blocked"
        elif web_src_status in ("ambiguous", "match_unverified"):
            modules["website"]["state"] = "ambiguous"
        elif web_src_status in ("missing", "not_applicable"):
            modules["website"]["state"] = "not_applicable"
        else:
            modules["website"]["state"] = "not_applicable"

        # 5. Jobs module
        if has_jobs:
            modules["jobs"]["state"] = "available"
        elif jobs_src_status == "available":
            modules["jobs"]["state"] = "available"
        elif web_src_status in ("blocked", "blocked_robots"):
            modules["jobs"]["state"] = "blocked"
        elif web_src_status in ("ambiguous", "match_unverified"):
            modules["jobs"]["state"] = "ambiguous"
        elif not has_website:
            modules["jobs"]["state"] = "not_applicable"
        else:
            modules["jobs"]["state"] = "not_available"

        # 6. Public Activity module
        if has_public_activity:
            modules["public_activity"]["state"] = "available"
        elif activity_src_status in ("available", "not_available"):
            modules["public_activity"]["state"] = activity_src_status
        else:
            modules["public_activity"]["state"] = "not_available"

        # Build evidence list and claim items with full provenance
        evidence_by_url: dict[str, str] = {}
        for fact in profile.facts:
            source_url = fact.source_url or f"https://data.brreg.no/enhetsregisteret/api/enheter/{orgnr}"
            if source_url not in evidence_by_url:
                ev_id = f"ev-{len(evidence_by_url) + 1}"
                evidence_by_url[source_url] = ev_id

                s_lower = fact.source_name.lower()
                u_lower = source_url.lower()
                if "brreg" in u_lower or "enhetsregisteret" in s_lower or "regnskapsregisteret" in s_lower or "roller" in s_lower:
                    s_class = "official_registry"
                elif "careers" in s_lower or "job" in s_lower:
                    s_class = "company_owned_careers"
                elif "news" in s_lower or "press" in s_lower:
                    s_class = "company_owned_news"
                elif "website" in s_lower or "enrichment" in s_lower:
                    s_class = "company_owned"
                else:
                    s_class = "public_source"

                content_hash = fact.content_hash or hashlib.sha256(str(fact.value).encode()).hexdigest()
                unit_suffix = f" {fact.unit}" if fact.unit else ""
                evidence_list.append({
                    "id": ev_id,
                    "source_url": source_url,
                    "source_class": s_class,
                    "retrieved_at": utc_iso(fact.retrieved_at),
                    "content_sha256": content_hash,
                    "claim_span": f"{fact.field_name}: {fact.value}{unit_suffix}",
                })
            else:
                ev_id = evidence_by_url[source_url]

            conf = 1.0
            if isinstance(fact.confidence_level, (int, float)):
                conf = float(fact.confidence_level)
            elif fact.confidence == "official":
                conf = 1.0
            elif fact.confidence == "verified_secondary":
                conf = 0.95
            elif fact.confidence == "unverified_secondary":
                conf = 0.70

            claim_entry: dict[str, Any] = {
                "field": fact.field_name,
                "value": fact.value,
                "source_url": source_url,
                "retrieved_at": utc_iso(fact.retrieved_at),
                "supporting_value": fact.value,
                "availability": "available",
                "confidence": conf,
                "evidence_ids": [ev_id],
            }
            if fact.unit:
                claim_entry["unit"] = fact.unit
            if fact.as_of:
                claim_entry["as_of"] = fact.as_of.isoformat() if hasattr(fact.as_of, "isoformat") else str(fact.as_of)
            claims.append(claim_entry)

        # Represent explicitly missing categories as explicit non-available claims
        if not has_financials:
            fin_state = modules["financials"]["state"]
            claims.append({
                "field": "financials",
                "value": None,
                "source_url": f"https://data.brreg.no/regnskapsregisteret/regnskap/{orgnr}",
                "retrieved_at": completed_iso,
                "supporting_value": None,
                "availability": fin_state,
                "confidence": 1.0,
                "evidence_ids": [],
            })

        if not has_website:
            web_state = modules["website"]["state"]
            claims.append({
                "field": "website",
                "value": None,
                "source_url": "",
                "retrieved_at": completed_iso,
                "supporting_value": None,
                "availability": web_state,
                "confidence": 1.0,
                "evidence_ids": [],
            })

        if not has_jobs:
            jobs_state = modules["jobs"]["state"]
            claims.append({
                "field": "jobs",
                "value": None,
                "source_url": "",
                "retrieved_at": completed_iso,
                "supporting_value": None,
                "availability": jobs_state,
                "confidence": 1.0,
                "evidence_ids": [],
            })

        if diff_summary:
            for item in diff_summary.changes:
                changes.append({
                    "field": item.field_name,
                    "old_value": item.old_value,
                    "new_value": item.new_value,
                    "change_type": item.status,
                    "timestamp": utc_iso(item.changed_at),
                })

        resolved_state = "available"
    else:
        err_str = str(error or "").lower()
        if "404" in err_str or "not found" in err_str:
            resolved_state = "not_available"
        elif "robot" in err_str or "policy" in err_str or "403" in err_str or "forbidden" in err_str:
            resolved_state = "blocked"
        elif "ambiguous" in err_str or "uncertain" in err_str:
            resolved_state = "ambiguous"
        elif "checksum" in err_str or "invalid" in err_str or "format" in err_str:
            resolved_state = "failed"
        elif "budget" in err_str or "exhausted" in err_str:
            resolved_state = "failed"
        elif error:
            resolved_state = "failed"
        else:
            resolved_state = "failed"

        modules["registry"]["state"] = resolved_state

    # Explicit override if caller passed terminal_state
    if terminal_state:
        resolved_state = normalize_contract_state(terminal_state)

    final_state = normalize_contract_state(resolved_state)

    envelope = {
        "organisation_number": orgnr,
        "run_id": run_id,
        "state": final_state,
        "started_at": started_iso,
        "completed_at": completed_iso,
        "run": {
            "run_id": run_id,
            "started_at": started_iso,
            "completed_at": completed_iso,
            "terminal_status": final_state,
        },
        "claims": claims,
        "evidence": evidence_list,
        "source_snapshots": [
            {
                "url": s.get("url", ""),
                "status_code": s.get("status_code", 200),
                "content_sha256": s.get("content_hash") or s.get("content_sha256", ""),
                "content_type": s.get("content_type", "application/json"),
                "retrieved_at": s.get("retrieved_at", completed_iso),
                "response_body": s.get("response_body", ""),
            }
            for s in (snapshots or [])
        ],
        "modules": modules,
        "changes": changes,
        "errors": errors,
        "operations": {
            "requests": request_count,
            "runtime_ms": round(runtime_ms, 2),
            "third_party_cost_usd": round(cost_usd, 6),
        },
        "profile": profile.model_dump(mode="json") if profile else None,
    }

    # Internal assertion to guarantee contract compliance before return
    is_valid, validation_errors = validate_envelope(envelope)
    if not is_valid:
        raise ContractValidationError(validation_errors)

    return envelope
