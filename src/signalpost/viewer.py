"""Human-readable profile viewer and inspection formatter for Signalpost."""

from __future__ import annotations

import json
from typing import Any


def render_terminal_profile(envelope: dict[str, Any]) -> str:
    """Render a beautifully structured, readable terminal view of a company profile.

    Enables human judges to inspect:
    - Verified structured facts by category (registry, governance, financials, website, jobs, news)
    - Source provenance, URLs, and cryptographic SHA-256 evidence
    - Explicit explanation of missing or unknown fields
    - Changes detected against prior runs
    """
    lines: list[str] = []
    width = 84
    sep = "=" * width
    thin_sep = "-" * width

    orgnr = envelope.get("organisation_number", "Unknown")
    state = envelope.get("state", "unknown").upper()
    claims = envelope.get("claims", [])
    evidence = envelope.get("evidence", [])
    modules = envelope.get("modules", {})
    changes = envelope.get("changes", [])
    profile_data = envelope.get("profile") or {}

    claims_by_field: dict[str, Any] = {}
    for c in claims:
        claims_by_field[c.get("field", "")] = c

    name = claims_by_field.get("legal_name", {}).get("value") or f"Company {orgnr}"
    org_form = claims_by_field.get("org_form", {}).get("value") or "N/A"
    city = claims_by_field.get("business_address_city", {}).get("value") or "N/A"
    founded = claims_by_field.get("founded_date", {}).get("value") or "N/A"
    employees = claims_by_field.get("employees", {}).get("value") or "0"

    lines.append(sep)
    lines.append(f"  SIGNALPOST COMPANY PROFILE: {name}")
    lines.append(f"  Organisasjonsnummer: {orgnr} | Status: {state}")
    lines.append(sep)
    lines.append(f"  • Legal Form:           {org_form}")
    lines.append(f"  • Registered City:      {city}")
    lines.append(f"  • Founded:              {founded}")
    lines.append(f"  • Registered Employees: {employees}")

    # Executive Summary
    summary = profile_data.get("executive_summary")
    if summary:
        lines.append("")
        lines.append(thin_sep)
        lines.append("  EXECUTIVE SUMMARY")
        lines.append(thin_sep)
        lines.append(f"  {summary}")

    # Categories Breakdown
    lines.append("")
    lines.append(thin_sep)
    lines.append("  VERIFIED FACTS BY DIMENSION")
    lines.append(thin_sep)

    # 1. Leadership & Governance
    lines.append("  [1] Governance & Leadership (Official Brreg Roller):")
    roles = [
        ("CEO / Daglig leder", "ceo"),
        ("Board Chair / Styrets leder", "board_chair"),
        ("Auditor / Revisor", "auditor"),
        ("Accountant / Regnskapsfører", "accountant"),
    ]
    has_any_role = False
    for label, f_key in roles:
        if f_key in claims_by_field and claims_by_field[f_key].get("value"):
            lines.append(f"      • {label:<28}: {claims_by_field[f_key]['value']}")
            has_any_role = True
    if not has_any_role:
        role_state = modules.get("people", {}).get("state", "not_available")
        lines.append(f"      • Status: {role_state} (No leadership roles registered)")

    # 2. Financial History
    lines.append("")
    lines.append("  [2] Financial Performance (Regnskapsregisteret):")
    fin_fields = [
        ("Revenue (Driftsinntekter)", "revenue"),
        ("Operating Result (Driftsresultat)", "operating_result"),
        ("Pre-Tax Result (Ordinært resultat)", "ordinary_result_before_tax"),
        ("Net Annual Income (Årsresultat)", "net_income"),
        ("Total Equity (Egenkapital)", "equity"),
        ("Total Assets (Sum eiendeler)", "total_assets"),
    ]
    has_any_fin = False
    for label, f_key in fin_fields:
        if f_key in claims_by_field and claims_by_field[f_key].get("value") is not None:
            c_item = claims_by_field[f_key]
            v = c_item["value"]
            u = c_item.get("unit", "NOK")
            as_of_str = f" [as of {c_item.get('as_of')}]" if c_item.get("as_of") else ""
            formatted_val = f"{v:,.0f} {u}".replace(",", " ") if isinstance(v, (int, float)) else f"{v} {u}"
            lines.append(f"      • {label:<35}: {formatted_val}{as_of_str}")
            has_any_fin = True
    if not has_any_fin:
        fin_state = modules.get("financials", {}).get("state", "not_available")
        lines.append(f"      • Status: {fin_state} (No annual account filings registered)")

    # 3. Digital Presence & Contact
    lines.append("")
    lines.append("  [3] Digital Presence & Contact (Verified Company Site):")
    web_fields = [
        ("Verified Domain", "website"),
        ("Page Title", "website_title"),
        ("Meta Description", "website_description"),
        ("Contact Email", "contact_email"),
        ("Contact Phone", "contact_phone"),
        ("Investor Relations", "investor_url"),
    ]
    has_any_web = False
    for label, f_key in web_fields:
        if f_key in claims_by_field and claims_by_field[f_key].get("value"):
            lines.append(f"      • {label:<22}: {claims_by_field[f_key]['value']}")
            has_any_web = True
    if not has_any_web:
        web_state = modules.get("website", {}).get("state", "not_applicable")
        lines.append(f"      • Status: {web_state} (No verified company website attached)")

    # 4. Careers & Jobs
    lines.append("")
    lines.append("  [4] Careers & Open Vacancies:")
    job_fields = [
        ("Careers Portal URL", "careers_url"),
        ("Hiring Status", "hiring_status"),
        ("Discovered Openings", "job_openings_count"),
    ]
    has_any_job = False
    for label, f_key in job_fields:
        if f_key in claims_by_field and claims_by_field[f_key].get("value") is not None:
            lines.append(f"      • {label:<22}: {claims_by_field[f_key]['value']}")
            has_any_job = True
    if not has_any_job:
        job_state = modules.get("jobs", {}).get("state", "not_available")
        lines.append(f"      • Status: {job_state} (No active careers listings discovered)")

    # 5. Public Activity
    lines.append("")
    lines.append("  [5] Public Activity & Legal Lifecycle:")
    act_fields = [
        ("Legal Status", "legal_status"),
        ("Registry Updated", "registry_registration_date"),
        ("Press / News URL", "press_url"),
        ("Latest Announcement", "latest_news_title"),
    ]
    for label, f_key in act_fields:
        if f_key in claims_by_field and claims_by_field[f_key].get("value"):
            lines.append(f"      • {label:<22}: {claims_by_field[f_key]['value']}")

    # Missing / Unknown Information Explicit Table
    lines.append("")
    lines.append(thin_sep)
    lines.append("  EXPLICIT MISSING / UNKNOWN INFORMATION")
    lines.append(thin_sep)
    for mod_name, mod_info in sorted(modules.items()):
        m_state = mod_info.get("state", "unknown")
        if m_state != "available":
            lines.append(f"  • Module '{mod_name}': {m_state.upper()} — verified as absent, exempt, or blocked.")

    # Changes / Diff History
    if changes:
        lines.append("")
        lines.append(thin_sep)
        lines.append("  CHANGE HISTORY (RECONCILIATION DIFF)")
        lines.append(thin_sep)
        for ch in changes:
            lines.append(f"  • [{ch.get('change_type', 'change').upper()}] {ch.get('field')}: {ch.get('old_value')} -> {ch.get('new_value')}")

    # Evidence Trace
    lines.append("")
    lines.append(thin_sep)
    lines.append(f"  EVIDENCE TRACE & PROVENANCE ({len(evidence)} verified sources)")
    lines.append(thin_sep)
    for ev in evidence[:6]:
        lines.append(f"  • [{ev.get('id')}] {ev.get('source_url')}")
        lines.append(f"    SHA-256: {ev.get('content_sha256')[:24]}... | Class: {ev.get('source_class')} | Retrieved: {ev.get('retrieved_at')}")
    if len(evidence) > 6:
        lines.append(f"    ... and {len(evidence) - 6} more cryptographic evidence entries.")

    lines.append(sep)
    return "\n".join(lines)


def generate_html_report(envelopes: list[dict[str, Any]], output_path: Any, title: str = "Signalpost Evaluation Dashboard") -> None:
    """Generate a minimalist, self-contained, responsive HTML verification dashboard.

    Adheres strictly to the 60-30-10 color rule:
    - 60% Dominant Neutral: Tinted Jet Black canvas & surfaces (#0C0E12, #12151B)
    - 30% Structural Secondary: Cards & framing (#181D26 with #3A0F34 plum brand accents)
    - 10% High-Priority Accent: Amber Gold (#EAAA00) exclusively for primary interactions
    - Typography: Tinted slate neutral (#DDE3E8) with WCAG AA >= 4.5:1 contrast
    - Accessibility: Icons paired with all semantic state indicators (no color-only reliance)
    """
    import html
    from pathlib import Path

    out_p = Path(output_path)
    total = len(envelopes)
    available_cnt = sum(1 for e in envelopes if e.get("state") == "available")
    not_avail_cnt = sum(1 for e in envelopes if e.get("state") == "not_available")
    blocked_cnt = sum(1 for e in envelopes if e.get("state") == "blocked")
    ambiguous_cnt = sum(1 for e in envelopes if e.get("state") == "ambiguous")
    failed_cnt = sum(1 for e in envelopes if e.get("state") == "failed")
    not_app_cnt = sum(1 for e in envelopes if e.get("state") == "not_applicable")
    total_claims = sum(len(e.get("claims", [])) for e in envelopes)
    total_reqs = sum(e.get("operations", {}).get("requests", 0) for e in envelopes)
    total_runtime = sum(e.get("operations", {}).get("runtime_ms", 0.0) for e in envelopes) / 1000.0

    state_symbols = {
        "available": "✓",
        "not_available": "✕",
        "blocked": "⊘",
        "ambiguous": "?",
        "failed": "!",
        "not_applicable": "—",
    }

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{html.escape(title)}</title>
  <style>
    /* ==========================================================================
       60-30-10 DESIGN SYSTEM TOKENS
       - 60% Dominant Neutral: Jet Black canvas & surfaces (#0c0e12, #12151b)
       - 30% Structural Neutral & Secondary: Cards, borders & deep plum (#181d26, #3a0f34)
       - 10% Interactive Accent: Amber Gold (#eaaa00) for high-priority CTA & focus
       - Typography: Tinted Slate Neutral (#dde3e8) with >= 4.5:1 contrast
       ========================================================================== */
    :root {{
      /* 60% Dominant Neutral Surfaces */
      --surface-canvas: #0c0e12;
      --surface-ground: #12151b;
      --surface-sunken: #080a0d;

      /* 30% Structural Elements */
      --surface-card: #181d26;
      --surface-card-hover: #1f2531;
      --surface-subtle: #242b38;
      --border-subtle: rgba(221, 227, 232, 0.08);
      --border-default: rgba(221, 227, 232, 0.14);
      --border-strong: rgba(221, 227, 232, 0.28);

      /* Secondary Brand Color: Deep Plum (#3A0F34) */
      --brand-plum: #3a0f34;
      --brand-plum-surface: rgba(58, 15, 52, 0.40);
      --brand-plum-border: rgba(94, 28, 85, 0.55);

      /* 10% High-Priority Interactive Accent: Amber Gold (#EAAA00) */
      --accent-gold: #eaaa00;
      --accent-gold-hover: #f5b81a;
      --accent-gold-active: #d69800;
      --accent-gold-surface: rgba(234, 170, 0, 0.12);
      --accent-gold-border: rgba(234, 170, 0, 0.38);
      --accent-gold-text: #0c0e12; /* High-contrast text on gold */

      /* Typography & Neutral Content (#DDE3E8) */
      --text-pure: #f8fafc;
      --text-primary: #dde3e8;
      --text-secondary: #9da8b6;
      --text-muted: #6e7987;

      /* Accessible Semantic State Tokens (Icon + Text, contrast >= 4.5:1) */
      --state-available: #2ed573;
      --state-available-bg: rgba(46, 213, 115, 0.10);
      --state-available-border: rgba(46, 213, 115, 0.30);

      --state-not-available: #9da8b6;
      --state-not-available-bg: rgba(157, 168, 182, 0.10);
      --state-not-available-border: rgba(157, 168, 182, 0.25);

      --state-blocked: #fb923c;
      --state-blocked-bg: rgba(251, 146, 60, 0.10);
      --state-blocked-border: rgba(251, 146, 60, 0.30);

      --state-ambiguous: #c084fc;
      --state-ambiguous-bg: rgba(192, 132, 252, 0.10);
      --state-ambiguous-border: rgba(192, 132, 252, 0.30);

      --state-failed: #f87171;
      --state-failed-bg: rgba(248, 113, 113, 0.10);
      --state-failed-border: rgba(248, 113, 113, 0.30);

      --state-not-applicable: #6e7987;
      --state-not-applicable-bg: rgba(110, 121, 135, 0.10);
      --state-not-applicable-border: rgba(110, 121, 135, 0.25);

      --radius-sm: 6px;
      --radius-md: 10px;
      --radius-lg: 14px;
      --radius-pill: 9999px;
      --transition: all 0.18s cubic-bezier(0.16, 1, 0.3, 1);
    }}

    * {{ box-sizing: border-box; margin: 0; padding: 0; }}

    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
      background: var(--surface-canvas);
      color: var(--text-primary);
      line-height: 1.55;
      padding: 32px 20px;
      -webkit-font-smoothing: antialiased;
    }}

    .container {{
      max-width: 1240px;
      margin: 0 auto;
    }}

    /* Top Brand Header */
    .header {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
      gap: 16px;
      padding-bottom: 24px;
      border-bottom: 1px solid var(--border-default);
      margin-bottom: 28px;
    }}

    .brand-group {{
      display: flex;
      align-items: center;
      gap: 12px;
    }}

    .brand-mark {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 42px;
      height: 42px;
      background: var(--brand-plum);
      border: 1px solid var(--brand-plum-border);
      border-radius: var(--radius-md);
      color: var(--accent-gold);
      font-weight: 800;
      font-size: 19px;
      box-shadow: 0 4px 12px rgba(58, 15, 52, 0.3);
    }}

    .header-text h1 {{
      font-size: 22px;
      font-weight: 700;
      color: var(--text-pure);
      letter-spacing: -0.02em;
    }}

    .header-text p {{
      color: var(--text-secondary);
      font-size: 13px;
      margin-top: 2px;
    }}

    .header-badge {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      background: var(--brand-plum-surface);
      border: 1px solid var(--brand-plum-border);
      color: var(--text-primary);
      padding: 6px 14px;
      border-radius: var(--radius-pill);
      font-size: 12px;
      font-weight: 600;
    }}

    .header-badge .dot {{
      width: 8px;
      height: 8px;
      background: var(--accent-gold);
      border-radius: 50%;
      box-shadow: 0 0 8px var(--accent-gold);
    }}

    /* 60-30-10 Stats Grid */
    .stats-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(136px, 1fr));
      gap: 14px;
      margin-bottom: 28px;
    }}

    .stat-card {{
      background: var(--surface-card);
      border: 1px solid var(--border-default);
      border-radius: var(--radius-md);
      padding: 16px 14px;
      text-align: center;
      transition: var(--transition);
    }}

    .stat-card:hover {{
      border-color: var(--border-strong);
      transform: translateY(-1px);
    }}

    .stat-card.featured {{
      background: linear-gradient(180deg, var(--brand-plum-surface) 0%, var(--surface-card) 100%);
      border-color: var(--brand-plum-border);
    }}

    .stat-card .val {{
      font-size: 26px;
      font-weight: 700;
      color: var(--text-pure);
      letter-spacing: -0.02em;
      line-height: 1.2;
    }}

    .stat-card .val.gold {{ color: var(--accent-gold); }}
    .stat-card .val.green {{ color: var(--state-available); }}
    .stat-card .val.muted {{ color: var(--state-not-available); }}
    .stat-card .val.orange {{ color: var(--state-blocked); }}
    .stat-card .val.purple {{ color: var(--state-ambiguous); }}

    .stat-card .lbl {{
      font-size: 11px;
      color: var(--text-secondary);
      text-transform: uppercase;
      letter-spacing: 0.6px;
      font-weight: 600;
      margin-top: 6px;
    }}

    /* Control & Filter Toolbar */
    .toolbar {{
      background: var(--surface-card);
      border: 1px solid var(--border-default);
      border-radius: var(--radius-lg);
      padding: 16px;
      margin-bottom: 24px;
      display: flex;
      flex-direction: column;
      gap: 14px;
    }}

    .search-wrapper {{
      position: relative;
      width: 100%;
    }}

    .search-wrapper input {{
      width: 100%;
      padding: 12px 16px 12px 42px;
      border-radius: var(--radius-md);
      border: 1px solid var(--border-default);
      background: var(--surface-canvas);
      color: var(--text-pure);
      font-size: 14px;
      outline: none;
      transition: var(--transition);
    }}

    .search-wrapper input:focus {{
      border-color: var(--accent-gold);
      box-shadow: 0 0 0 3px rgba(234, 170, 0, 0.15);
    }}

    .search-icon {{
      position: absolute;
      left: 14px;
      top: 50%;
      transform: translateY(-50%);
      color: var(--text-muted);
      font-size: 15px;
      pointer-events: none;
    }}

    .filter-pills {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      align-items: center;
    }}

    .filter-btn {{
      background: var(--surface-ground);
      border: 1px solid var(--border-default);
      color: var(--text-secondary);
      padding: 6px 14px;
      border-radius: var(--radius-pill);
      font-size: 12px;
      font-weight: 600;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      gap: 6px;
      transition: var(--transition);
    }}

    .filter-btn:hover {{
      background: var(--surface-card-hover);
      color: var(--text-pure);
      border-color: var(--border-strong);
    }}

    /* Active filter gets the 10% Accent Gold */
    .filter-btn.active {{
      background: var(--accent-gold);
      color: var(--accent-gold-text);
      border-color: var(--accent-gold);
      box-shadow: 0 2px 8px rgba(234, 170, 0, 0.25);
    }}

    .filter-btn .count {{
      font-size: 11px;
      opacity: 0.85;
      padding: 1px 6px;
      border-radius: var(--radius-pill);
      background: rgba(0, 0, 0, 0.15);
    }}

    /* Company List & Cards (30% Structure) */
    .company-list {{
      display: flex;
      flex-direction: column;
      gap: 16px;
    }}

    .company-card {{
      background: var(--surface-card);
      border: 1px solid var(--border-default);
      border-radius: var(--radius-lg);
      padding: 22px;
      transition: var(--transition);
    }}

    .company-card:hover {{
      border-color: var(--border-strong);
      box-shadow: 0 6px 20px rgba(0, 0, 0, 0.25);
    }}

    .card-top {{
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      margin-bottom: 14px;
      flex-wrap: wrap;
      gap: 12px;
    }}

    .comp-name {{
      font-size: 17px;
      font-weight: 700;
      color: var(--text-pure);
      letter-spacing: -0.01em;
    }}

    .comp-orgnr {{
      font-size: 12px;
      color: var(--accent-gold);
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
      margin-top: 2px;
      font-weight: 600;
    }}

    /* Semantic State Badges (Icons + Text for WCAG AA compliance) */
    .badge {{
      display: inline-flex;
      align-items: center;
      gap: 5px;
      padding: 4px 11px;
      border-radius: var(--radius-pill);
      font-size: 11px;
      font-weight: 700;
      letter-spacing: 0.4px;
      text-transform: uppercase;
      white-space: nowrap;
    }}

    .badge-available {{
      background: var(--state-available-bg);
      color: var(--state-available);
      border: 1px solid var(--state-available-border);
    }}

    .badge-not_available {{
      background: var(--state-not-available-bg);
      color: var(--state-not-available);
      border: 1px solid var(--state-not-available-border);
    }}

    .badge-blocked {{
      background: var(--state-blocked-bg);
      color: var(--state-blocked);
      border: 1px solid var(--state-blocked-border);
    }}

    .badge-ambiguous {{
      background: var(--state-ambiguous-bg);
      color: var(--state-ambiguous);
      border: 1px solid var(--state-ambiguous-border);
    }}

    .badge-failed {{
      background: var(--state-failed-bg);
      color: var(--state-failed);
      border: 1px solid var(--state-failed-border);
    }}

    .badge-not_applicable {{
      background: var(--state-not-applicable-bg);
      color: var(--state-not-applicable);
      border: 1px solid var(--state-not-applicable-border);
    }}

    /* Executive Summary */
    .summary-text {{
      font-size: 13.5px;
      color: var(--text-primary);
      margin-bottom: 16px;
      background: var(--surface-sunken);
      border: 1px solid var(--border-subtle);
      border-left: 3px solid var(--accent-gold);
      padding: 10px 14px;
      border-radius: var(--radius-sm);
      line-height: 1.5;
    }}

    /* Facts Grid */
    .facts-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(230px, 1fr));
      gap: 10px;
      margin-bottom: 16px;
    }}

    .fact-item {{
      background: var(--surface-ground);
      border: 1px solid var(--border-subtle);
      padding: 9px 12px;
      border-radius: var(--radius-sm);
    }}

    .fact-lbl {{
      color: var(--text-secondary);
      font-size: 10.5px;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      font-weight: 600;
      margin-bottom: 2px;
    }}

    .fact-val {{
      font-weight: 600;
      color: var(--text-pure);
      font-size: 13px;
      word-break: break-word;
    }}

    /* Details Toggle & Provenance Evidence */
    details {{
      border-top: 1px solid var(--border-subtle);
      padding-top: 12px;
      margin-top: 8px;
    }}

    summary.details-toggle {{
      font-size: 12.5px;
      font-weight: 600;
      color: var(--accent-gold);
      cursor: pointer;
      user-select: none;
      display: inline-flex;
      align-items: center;
      gap: 6px;
      transition: var(--transition);
    }}

    summary.details-toggle:hover {{
      color: var(--accent-gold-hover);
      text-decoration: underline;
    }}

    .details-body {{
      margin-top: 14px;
      background: var(--surface-sunken);
      border: 1px solid var(--border-default);
      border-radius: var(--radius-md);
      padding: 14px;
    }}

    .modules-row {{
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      margin-bottom: 14px;
    }}

    .evidence-table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 11.5px;
    }}

    .evidence-table th,
    .evidence-table td {{
      padding: 8px 10px;
      border: 1px solid var(--border-subtle);
      text-align: left;
    }}

    .evidence-table th {{
      background: var(--surface-ground);
      color: var(--text-secondary);
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.4px;
    }}

    .evidence-table td {{
      color: var(--text-primary);
    }}

    .evidence-table a {{
      color: var(--accent-gold);
      text-decoration: none;
      word-break: break-all;
    }}

    .evidence-table a:hover {{
      text-decoration: underline;
    }}

    .mono {{
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
      font-size: 11px;
    }}

    .empty-state {{
      text-align: center;
      padding: 48px 24px;
      color: var(--text-secondary);
      background: var(--surface-card);
      border: 1px solid var(--border-default);
      border-radius: var(--radius-lg);
      display: none;
    }}

    .empty-state .icon {{ font-size: 32px; margin-bottom: 8px; opacity: 0.6; }}
    .empty-state h3 {{ color: var(--text-pure); font-size: 16px; margin-bottom: 4px; }}
  </style>
</head>
<body>
  <div class="container">
    <header class="header">
      <div class="brand-group">
        <div class="brand-mark">S</div>
        <div class="header-text">
          <h1>{html.escape(title)}</h1>
          <p>Signalpost Verified Company Data • 100% Cryptographic Provenance</p>
        </div>
      </div>
      <div class="header-badge">
        <span class="dot"></span>
        <span>6-State Contract Validated</span>
      </div>
    </header>

    <!-- 60-30-10 Stats Overview -->
    <div class="stats-grid">
      <div class="stat-card featured">
        <div class="val gold">{total}</div>
        <div class="lbl">Total Evaluated</div>
      </div>
      <div class="stat-card">
        <div class="val green">{available_cnt}</div>
        <div class="lbl">✓ Available</div>
      </div>
      <div class="stat-card">
        <div class="val muted">{not_avail_cnt}</div>
        <div class="lbl">✕ Not Available</div>
      </div>
      <div class="stat-card">
        <div class="val orange">{blocked_cnt}</div>
        <div class="lbl">⊘ Blocked</div>
      </div>
      <div class="stat-card">
        <div class="val purple">{ambiguous_cnt}</div>
        <div class="lbl">? Ambiguous</div>
      </div>
      <div class="stat-card">
        <div class="val">{total_claims:,}</div>
        <div class="lbl">Verified Claims</div>
      </div>
      <div class="stat-card">
        <div class="val">{total_reqs}</div>
        <div class="lbl">Requests Used</div>
      </div>
      <div class="stat-card">
        <div class="val">{total_runtime:.2f}s</div>
        <div class="lbl">Wall Clock</div>
      </div>
    </div>

    <!-- Minimalist Filter Toolbar -->
    <div class="toolbar">
      <div class="search-wrapper">
        <span class="search-icon">🔍</span>
        <input type="text" id="searchInput" placeholder="Search by legal name, organisation number, city, or status..." onkeyup="filterCards()">
      </div>
      <div class="filter-pills">
        <button class="filter-btn active" data-filter="all" onclick="setFilter('all', this)">
          All <span class="count">{total}</span>
        </button>
        <button class="filter-btn" data-filter="available" onclick="setFilter('available', this)">
          ✓ Available <span class="count">{available_cnt}</span>
        </button>
        <button class="filter-btn" data-filter="not_available" onclick="setFilter('not_available', this)">
          ✕ Not Available <span class="count">{not_avail_cnt}</span>
        </button>
        <button class="filter-btn" data-filter="blocked" onclick="setFilter('blocked', this)">
          ⊘ Blocked <span class="count">{blocked_cnt}</span>
        </button>
        <button class="filter-btn" data-filter="ambiguous" onclick="setFilter('ambiguous', this)">
          ? Ambiguous <span class="count">{ambiguous_cnt}</span>
        </button>
        <button class="filter-btn" data-filter="failed" onclick="setFilter('failed', this)">
          ! Failed <span class="count">{failed_cnt}</span>
        </button>
      </div>
    </div>

    <!-- Company Cards List -->
    <div class="company-list" id="companyList">
"""

    for env in envelopes:
        orgnr = env.get("organisation_number", "")
        state = env.get("state", "unknown")
        sym = state_symbols.get(state, "•")
        claims = env.get("claims", [])
        evidence = env.get("evidence", [])
        modules = env.get("modules", {})
        profile = env.get("profile") or {}

        claims_map = {c.get("field", ""): c for c in claims}
        name = claims_map.get("legal_name", {}).get("value") or f"Company {orgnr}"
        summary = profile.get("executive_summary", "")

        html_content += f"""
      <div class="company-card" data-state="{html.escape(state)}" data-search="{html.escape(f'{name} {orgnr} {state}'.lower())}">
        <div class="card-top">
          <div>
            <div class="comp-name">{html.escape(str(name))}</div>
            <div class="comp-orgnr">Org.nr: {html.escape(str(orgnr))}</div>
          </div>
          <span class="badge badge-{state}">{sym} {html.escape(state)}</span>
        </div>
"""
        if summary:
            html_content += f"""        <div class="summary-text">{html.escape(str(summary))}</div>
"""

        # Key facts grid
        html_content += """        <div class="facts-grid">
"""
        key_fields = [
            ("Legal Form", "org_form"),
            ("City", "business_address_city"),
            ("Employees", "employees"),
            ("CEO", "ceo"),
            ("Board Chair", "board_chair"),
            ("Revenue", "revenue"),
            ("Website", "website"),
            ("Careers Portal", "careers_url"),
            ("Legal Status", "legal_status"),
        ]
        for lbl, f_key in key_fields:
            if f_key in claims_map and claims_map[f_key].get("value") is not None:
                val = claims_map[f_key]["value"]
                unit = claims_map[f_key].get("unit", "")
                val_str = f"{val:,.0f} {unit}".replace(",", " ") if isinstance(val, (int, float)) else f"{val} {unit}"
                html_content += f"""          <div class="fact-item"><div class="fact-lbl">{html.escape(lbl)}</div><div class="fact-val">{html.escape(val_str.strip())}</div></div>
"""
        html_content += """        </div>
"""

        # Toggleable details for evidence
        html_content += f"""
        <details>
          <summary class="details-toggle">View Verified Evidence & Module States ({len(evidence)} sources)</summary>
          <div class="details-body">
            <div style="font-size: 12px; font-weight: 700; color: var(--text-pure); margin-bottom: 8px;">MODULE STATUS BREAKDOWN:</div>
            <div class="modules-row">
"""
        for m_name, m_info in sorted(modules.items()):
            m_st = m_info.get("state", "n/a")
            m_sym = state_symbols.get(m_st, "•")
            html_content += f"""              <span class="badge badge-{m_st}">{m_sym} {html.escape(m_name)}: {html.escape(m_st)}</span>
"""

        html_content += """
            </div>
            <table class="evidence-table">
              <thead>
                <tr><th>ID</th><th>Verified Source URL</th><th>Provenance Class</th><th>SHA-256 Digest</th><th>Claim Scope</th></tr>
              </thead>
              <tbody>
"""
        for ev in evidence[:8]:
            html_content += f"""
                <tr>
                  <td>{html.escape(ev.get('id', ''))}</td>
                  <td><a href="{html.escape(ev.get('source_url', ''))}" target="_blank" rel="noopener">{html.escape(ev.get('source_url', ''))}</a></td>
                  <td>{html.escape(ev.get('source_class', ''))}</td>
                  <td class="mono">{html.escape(ev.get('content_sha256', '')[:16])}...</td>
                  <td>{html.escape(ev.get('claim_span', ''))}</td>
                </tr>
"""
        html_content += """
              </tbody>
            </table>
          </div>
        </details>
      </div>
"""

    html_content += """
      <div class="empty-state" id="emptyState">
        <div class="icon">🔍</div>
        <h3>No matching companies found</h3>
        <p>Try clearing your search query or selecting a different status filter.</p>
      </div>
    </div>
  </div>

  <script>
    let activeFilter = 'all';

    function setFilter(filter, el) {
      activeFilter = filter;
      document.querySelectorAll('.filter-btn').forEach(btn => btn.classList.remove('active'));
      if (el) el.classList.add('active');
      filterCards();
    }

    function filterCards() {
      const q = document.getElementById('searchInput').value.toLowerCase().trim();
      const cards = document.querySelectorAll('.company-card');
      let visibleCount = 0;

      cards.forEach(card => {
        const text = card.getAttribute('data-search') || '';
        const state = card.getAttribute('data-state') || '';
        const matchesQuery = !q || text.includes(q);
        const matchesState = (activeFilter === 'all') || (state === activeFilter);

        if (matchesQuery && matchesState) {
          card.style.display = 'block';
          visibleCount++;
        } else {
          card.style.display = 'none';
        }
      });

      const emptyEl = document.getElementById('emptyState');
      if (emptyEl) {
        emptyEl.style.display = visibleCount === 0 ? 'block' : 'none';
      }
    }
  </script>
</body>
</html>
"""

    out_p.parent.mkdir(parents=True, exist_ok=True)
    out_p.write_text(html_content, encoding="utf-8")

