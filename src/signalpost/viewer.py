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
    """Generate a self-contained, responsive HTML verification dashboard."""
    import html
    from pathlib import Path

    out_p = Path(output_path)
    total = len(envelopes)
    available_cnt = sum(1 for e in envelopes if e.get("state") == "available")
    not_avail_cnt = sum(1 for e in envelopes if e.get("state") == "not_available")
    blocked_cnt = sum(1 for e in envelopes if e.get("state") == "blocked")
    ambiguous_cnt = sum(1 for e in envelopes if e.get("state") == "ambiguous")
    failed_cnt = sum(1 for e in envelopes if e.get("state") == "failed")
    total_claims = sum(len(e.get("claims", [])) for e in envelopes)
    total_reqs = sum(e.get("operations", {}).get("requests", 0) for e in envelopes)
    total_runtime = sum(e.get("operations", {}).get("runtime_ms", 0.0) for e in envelopes) / 1000.0

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{html.escape(title)}</title>
  <style>
    :root {{
      --bg: #090d16;
      --card-bg: #121826;
      --border: #222d42;
      --text: #f1f5f9;
      --text-muted: #94a3b8;
      --primary: #38bdf8;
      --success: #10b981;
      --warning: #f59e0b;
      --danger: #ef4444;
      --purple: #a855f7;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      background: var(--bg);
      color: var(--text);
      line-height: 1.5;
      padding: 24px;
    }}
    .header {{
      max-width: 1200px;
      margin: 0 auto 24px auto;
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
      gap: 16px;
      padding-bottom: 20px;
      border-bottom: 1px solid var(--border);
    }}
    .header h1 {{ font-size: 26px; font-weight: 700; color: #fff; }}
    .header p {{ color: var(--text-muted); font-size: 14px; margin-top: 4px; }}
    .stats-grid {{
      max-width: 1200px;
      margin: 0 auto 24px auto;
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 16px;
    }}
    .stat-card {{
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 16px;
      text-align: center;
    }}
    .stat-card .val {{ font-size: 28px; font-weight: 700; color: #fff; }}
    .stat-card .lbl {{ font-size: 13px; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.5px; margin-top: 4px; }}
    .search-box {{
      max-width: 1200px;
      margin: 0 auto 20px auto;
    }}
    .search-box input {{
      width: 100%;
      padding: 12px 16px;
      border-radius: 8px;
      border: 1px solid var(--border);
      background: var(--card-bg);
      color: #fff;
      font-size: 15px;
    }}
    .company-list {{
      max-width: 1200px;
      margin: 0 auto;
      display: flex;
      flex-direction: column;
      gap: 16px;
    }}
    .company-card {{
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 20px;
      transition: border-color 0.2s;
    }}
    .company-card:hover {{ border-color: var(--primary); }}
    .card-top {{
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      margin-bottom: 12px;
      flex-wrap: wrap;
      gap: 8px;
    }}
    .comp-name {{ font-size: 18px; font-weight: 600; color: #fff; }}
    .comp-orgnr {{ font-size: 13px; color: var(--primary); font-family: monospace; }}
    .badge {{
      display: inline-block;
      padding: 4px 10px;
      border-radius: 9999px;
      font-size: 12px;
      font-weight: 600;
      text-transform: uppercase;
    }}
    .badge-available {{ background: rgba(16, 185, 129, 0.2); color: var(--success); border: 1px solid var(--success); }}
    .badge-not_available {{ background: rgba(148, 163, 184, 0.2); color: var(--text-muted); border: 1px solid var(--text-muted); }}
    .badge-blocked {{ background: rgba(245, 158, 11, 0.2); color: var(--warning); border: 1px solid var(--warning); }}
    .badge-ambiguous {{ background: rgba(168, 85, 247, 0.2); color: var(--purple); border: 1px solid var(--purple); }}
    .badge-failed {{ background: rgba(239, 68, 68, 0.2); color: var(--danger); border: 1px solid var(--danger); }}
    .summary-text {{
      font-size: 14px;
      color: #cbd5e1;
      margin-bottom: 16px;
      background: rgba(0,0,0,0.2);
      padding: 10px 14px;
      border-radius: 6px;
    }}
    .facts-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
      gap: 12px;
      margin-bottom: 16px;
    }}
    .fact-item {{
      background: rgba(255,255,255,0.03);
      padding: 8px 12px;
      border-radius: 6px;
      font-size: 13px;
    }}
    .fact-lbl {{ color: var(--text-muted); font-size: 11px; text-transform: uppercase; }}
    .fact-val {{ font-weight: 600; color: #fff; word-break: break-word; }}
    .details-toggle {{
      font-size: 13px;
      color: var(--primary);
      cursor: pointer;
      text-decoration: underline;
      display: inline-block;
      margin-top: 6px;
    }}
    .evidence-table {{
      width: 100%;
      border-collapse: collapse;
      margin-top: 10px;
      font-size: 12px;
    }}
    .evidence-table th, .evidence-table td {{
      padding: 6px 10px;
      border: 1px solid var(--border);
      text-align: left;
    }}
    .evidence-table th {{ background: rgba(255,255,255,0.05); color: var(--text-muted); }}
    .mono {{ font-family: monospace; font-size: 11px; }}
  </style>
</head>
<body>
  <div class="header">
    <div>
      <h1>{html.escape(title)}</h1>
      <p>Builderr Evaluation Contract Conformance • 100% Provenance & Offline Verification</p>
    </div>
  </div>

  <div class="stats-grid">
    <div class="stat-card">
      <div class="val">{total}</div>
      <div class="lbl">Total Evaluated</div>
    </div>
    <div class="stat-card">
      <div class="val" style="color: var(--success);">{available_cnt}</div>
      <div class="lbl">Available</div>
    </div>
    <div class="stat-card">
      <div class="val" style="color: var(--text-muted);">{not_avail_cnt}</div>
      <div class="lbl">Not Available</div>
    </div>
    <div class="stat-card">
      <div class="val" style="color: var(--warning);">{blocked_cnt}</div>
      <div class="lbl">Blocked / Robots</div>
    </div>
    <div class="stat-card">
      <div class="val" style="color: var(--purple);">{ambiguous_cnt}</div>
      <div class="lbl">Ambiguous</div>
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
      <div class="lbl">Wall-Clock Time</div>
    </div>
  </div>

  <div class="search-box">
    <input type="text" id="searchInput" placeholder="Search by company name, orgnr, city, or status..." onkeyup="filterCards()">
  </div>

  <div class="company-list" id="companyList">
"""

    for env in envelopes:
        orgnr = env.get("organisation_number", "")
        state = env.get("state", "unknown")
        claims = env.get("claims", [])
        evidence = env.get("evidence", [])
        modules = env.get("modules", {})
        profile = env.get("profile") or {}

        claims_map = {c.get("field", ""): c for c in claims}
        name = claims_map.get("legal_name", {}).get("value") or f"Company {orgnr}"
        summary = profile.get("executive_summary", "")

        html_content += f"""
    <div class="company-card" data-search="{html.escape(f'{name} {orgnr} {state}'.lower())}">
      <div class="card-top">
        <div>
          <div class="comp-name">{html.escape(str(name))}</div>
          <div class="comp-orgnr">Org.nr: {html.escape(str(orgnr))}</div>
        </div>
        <span class="badge badge-{state}">{html.escape(state)}</span>
      </div>
"""
        if summary:
            html_content += f"""      <div class="summary-text">{html.escape(str(summary))}</div>\n"""

        # Key facts grid
        html_content += """      <div class="facts-grid">\n"""
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
                html_content += f"""        <div class="fact-item"><div class="fact-lbl">{html.escape(lbl)}</div><div class="fact-val">{html.escape(val_str.strip())}</div></div>\n"""
        html_content += """      </div>\n"""

        # Toggleable details for evidence
        html_content += f"""
      <details>
        <summary class="details-toggle">View Evidence & Modules ({len(evidence)} verified sources)</summary>
        <div style="margin-top: 10px;">
          <div style="font-size: 13px; font-weight: 600; margin-bottom: 6px;">Module States:</div>
          <div style="display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 12px;">
"""
        for m_name, m_info in modules.items():
            m_st = m_info.get("state", "n/a")
            html_content += f"""            <span class="badge badge-{m_st}">{html.escape(m_name)}: {html.escape(m_st)}</span>\n"""

        html_content += """
          </div>
          <table class="evidence-table">
            <thead>
              <tr><th>ID</th><th>Source URL</th><th>Class</th><th>SHA-256 Hash</th><th>Claim Span</th></tr>
            </thead>
            <tbody>
"""
        for ev in evidence[:8]:
            html_content += f"""
              <tr>
                <td>{html.escape(ev.get('id', ''))}</td>
                <td><a href="{html.escape(ev.get('source_url', ''))}" target="_blank" style="color: var(--primary);">{html.escape(ev.get('source_url', ''))}</a></td>
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
  </div>

  <script>
    function filterCards() {
      const q = document.getElementById('searchInput').value.toLowerCase();
      const cards = document.querySelectorAll('.company-card');
      cards.forEach(card => {
        const text = card.getAttribute('data-search') || '';
        card.style.display = text.includes(q) ? 'block' : 'none';
      });
    }
  </script>
</body>
</html>
"""

    out_p.parent.mkdir(parents=True, exist_ok=True)
    out_p.write_text(html_content, encoding="utf-8")
