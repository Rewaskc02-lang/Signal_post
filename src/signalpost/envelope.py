"""Terminal Envelope builder conforming to the Signalpost evaluation contract and OUTPUT_CONTRACT.md."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from signalpost.differ import ProfileUpdateSummary
from signalpost.models import CompanyFact, CompanyProfile

TERMINAL_STATES = {
    "complete",
    "not_applicable",
    "not_found",
    "blocked_policy",
    "blocked_robots",
    "source_error",
    "budget_exhausted",
    "submission_error",
}


def utc_iso(dt: datetime | None = None) -> str:
    """Format datetime as UTC ISO-8601 string ending with Z."""
    if dt is None:
        dt = datetime.now(timezone.utc)
    elif dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")


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
) -> dict[str, Any]:
    """Emit exactly one unified terminal envelope for a supplied organisation number.

    Conforms to both the Minimal Output Contract (OUTPUT_CONTRACT.md)
    and the Signalpost batch evaluation harness validator.
    """
    started_iso = utc_iso(started_at)
    completed_iso = utc_iso(completed_at)

    claims: list[dict[str, Any]] = []
    evidence_list: list[dict[str, Any]] = []
    changes: list[dict[str, Any]] = []
    errors: list[str] = [str(error)] if error else []

    modules: dict[str, dict[str, Any]] = {
        "registry": {
            "state": "submission_error" if error else "complete",
            "retry_count": 0,
            "final_timestamp": completed_iso,
        },
        "financials": {
            "state": "not_applicable",
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
        has_financials = any(
            f.field_name in {"revenue", "operating_result", "ordinary_result_before_tax", "equity"}
            for f in profile.facts
        )
        has_website = any(f.field_name == "website" or "website" in f.source_name.lower() for f in profile.facts)

        modules["registry"]["state"] = "complete"
        modules["financials"]["state"] = "complete" if has_financials else "not_applicable"
        modules["website"]["state"] = "complete" if has_website else "not_applicable"

        evidence_by_url: dict[str, str] = {}
        for fact in profile.facts:
            source_url = fact.source_url or f"https://data.brreg.no/enhetsregisteret/api/enheter/{orgnr}"
            if source_url not in evidence_by_url:
                ev_id = f"ev-{len(evidence_by_url) + 1}"
                evidence_by_url[source_url] = ev_id

                s_lower = fact.source_name.lower()
                u_lower = source_url.lower()
                if "brreg" in u_lower or "enhetsregisteret" in s_lower or "regnskapsregisteret" in s_lower:
                    s_class = "official_registry"
                elif "website" in s_lower or "enrichment" in s_lower:
                    s_class = "company_owned"
                else:
                    s_class = "public_source"

                content_hash = fact.content_hash or hashlib.sha256(str(fact.value).encode()).hexdigest()
                evidence_list.append({
                    "id": ev_id,
                    "source_url": source_url,
                    "source_class": s_class,
                    "retrieved_at": utc_iso(fact.retrieved_at),
                    "content_sha256": content_hash,
                    "claim_span": f"{fact.field_name}: {fact.value}",
                })
            else:
                ev_id = evidence_by_url[source_url]

            conf = 1.0
            if isinstance(fact.confidence_level, (int, float)):
                conf = float(fact.confidence_level)
            elif fact.confidence == "verified_secondary":
                conf = 0.9
            elif fact.confidence == "unverified_secondary":
                conf = 0.7

            claims.append({
                "field": fact.field_name,
                "value": fact.value,
                "availability": "available",
                "confidence": conf,
                "evidence_ids": [ev_id],
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

        status = terminal_state or "complete"
    else:
        if not terminal_state:
            err_str = str(error or "").lower()
            if "404" in err_str or "not found" in err_str:
                status = "not_found"
            elif "checksum" in err_str or "invalid" in err_str or "format" in err_str:
                status = "submission_error"
            elif "budget" in err_str or "exhausted" in err_str:
                status = "budget_exhausted"
            elif error:
                status = "source_error"
            else:
                status = "submission_error"
        else:
            status = terminal_state

        modules["registry"]["state"] = status

    entity_state = status if status in TERMINAL_STATES else "complete"
    terminal_status = "completed" if entity_state == "complete" else entity_state

    envelope = {
        "organisation_number": orgnr,
        "run_id": run_id,
        "state": entity_state,
        "started_at": started_iso,
        "completed_at": completed_iso,
        "run": {
            "run_id": run_id,
            "started_at": started_iso,
            "completed_at": completed_iso,
            "terminal_status": terminal_status,
        },
        "claims": claims,
        "evidence": evidence_list,
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
    return envelope
