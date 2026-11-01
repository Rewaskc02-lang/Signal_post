"""Regression tests verifying correctness for employee counts, financial ordering, and viewers."""

from datetime import date, datetime, timezone
from pathlib import Path
import pytest

from signalpost.envelope import build_terminal_envelope, validate_envelope
from signalpost.explain import generate_deterministic_summary
from signalpost.extractors.brreg_enhet import extract_enhet_facts
from signalpost.extractors.brreg_regnskap import extract_regnskap_facts
from signalpost.extractors.public_activity import extract_website_public_activity
from signalpost.matcher import deduplicate_facts
from signalpost.models import CompanyFact, CompanyProfile
from signalpost.viewer import generate_html_report, render_terminal_profile


def test_employee_count_absent_vs_explicit_zero():
    """Missing or null employee count must NEVER become 0; explicit 0 must be preserved."""
    # Case A: antallAnsatte absent/None
    raw_missing = {"navn": "Test AS", "organisasjonsform": {"kode": "AS", "beskrivelse": "Aksjeselskap"}}
    facts_missing = extract_enhet_facts(raw_missing, "https://api.test/enhet/123")
    emp_missing = [f for f in facts_missing if f.field_name == "employee_count"]
    assert len(emp_missing) == 0, "Missing employee count should not produce an employee_count fact"

    profile_missing = CompanyProfile(orgnr="123456789", facts=facts_missing)
    envelope_missing = build_terminal_envelope(
        orgnr="123456789",
        run_id="run-1",
        started_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
        profile=profile_missing,
    )
    view_missing = render_terminal_profile(envelope_missing)
    # Must NOT show "Registered Employees: 0" when missing
    assert "Registered Employees: 0" not in view_missing
    assert ("Not reported" in view_missing or "not_available" in view_missing.lower() or "N/A" in view_missing)

    # Case B: antallAnsatte explicitly 0
    raw_zero = {"navn": "Zero AS", "organisasjonsform": {"kode": "AS"}, "antallAnsatte": 0}
    facts_zero = extract_enhet_facts(raw_zero, "https://api.test/enhet/123")
    emp_zero = [f for f in facts_zero if f.field_name == "employee_count"]
    assert len(emp_zero) == 1
    assert emp_zero[0].value == 0

    profile_zero = CompanyProfile(orgnr="123456789", facts=facts_zero)
    envelope_zero = build_terminal_envelope(
        orgnr="123456789",
        run_id="run-1",
        started_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
        profile=profile_zero,
    )
    view_zero = render_terminal_profile(envelope_zero)
    assert "Registered Employees: 0" in view_zero


def test_financial_period_ordering_ascending_and_descending():
    """Summary and viewers must select the latest financial period by greatest as_of date, NOT input order."""
    filing_2023 = {
        "regnskapsperiode": {"tilDato": "2023-12-31"},
        "valuta": "NOK",
        "resultatregnskapResultat": {
            "driftsresultat": {"driftsinntekter": {"sumDriftsinntekter": 5000000.0}}
        },
    }
    filing_2024 = {
        "regnskapsperiode": {"tilDato": "2024-12-31"},
        "valuta": "NOK",
        "resultatregnskapResultat": {
            "driftsresultat": {"driftsinntekter": {"sumDriftsinntekter": 9000000.0}}
        },
    }

    # Order 1: Ascending [2023, 2024]
    facts_asc = extract_regnskap_facts([filing_2023, filing_2024], "https://api.test/regnskap/123")
    profile_asc = CompanyProfile(orgnr="123456789", facts=facts_asc)
    summary_asc = generate_deterministic_summary(profile_asc)

    # Order 2: Descending [2024, 2023]
    facts_desc = extract_regnskap_facts([filing_2024, filing_2023], "https://api.test/regnskap/123")
    profile_desc = CompanyProfile(orgnr="123456789", facts=facts_desc)
    summary_desc = generate_deterministic_summary(profile_desc)

    # Both must select 2024 with 9,000,000 NOK
    assert ("9 000 000" in summary_asc or "9,000,000" in summary_asc)
    assert "2024" in summary_asc
    assert ("9 000 000" in summary_desc or "9,000,000" in summary_desc)
    assert "2024" in summary_desc


def test_multiple_filings_and_missing_period_dates():
    """Filings with missing tilDato must be skipped or marked ambiguous, not crash or override valid filings."""
    valid_filing = {
        "regnskapsperiode": {"tilDato": "2024-12-31"},
        "valuta": "NOK",
        "resultatregnskapResultat": {
            "driftsresultat": {"driftsinntekter": {"sumDriftsinntekter": 3000000.0}}
        },
    }
    invalid_filing = {
        "regnskapsperiode": {},  # missing tilDato
        "valuta": "NOK",
        "resultatregnskapResultat": {
            "driftsresultat": {"driftsinntekter": {"sumDriftsinntekter": 1000000.0}}
        },
    }

    facts = extract_regnskap_facts([valid_filing, invalid_filing], "https://api.test/regnskap/123")
    profile = CompanyProfile(orgnr="123456789", facts=facts)
    summary = generate_deterministic_summary(profile)

    assert ("3 000 000" in summary or "3,000,000" in summary)
    assert "2024" in summary


def test_latest_accounting_year_disagreement():
    """When latest_accounting_year in Enhet disagrees with Regnskap latest filing, summary notes reconciliation."""
    facts = [
        CompanyFact(
            field_name="latest_accounting_year",
            value="2024",
            source_name="Enhetsregisteret",
            source_url="https://api.test/enhet",
        ),
        CompanyFact(
            field_name="revenue",
            value=2500000.0,
            unit="NOK",
            as_of=date(2023, 12, 31),
            source_name="Regnskapsregisteret",
            source_url="https://api.test/regnskap",
        ),
    ]
    profile = CompanyProfile(orgnr="123456789", facts=facts)
    summary = generate_deterministic_summary(profile)
    # Must mention 2023 fiscal year and acknowledge 2024 latest reported filing
    assert "2023" in summary
    assert "2024" in summary


def test_nested_address_display_in_viewers():
    """Viewers must parse dict business_address and extract city and street properly."""
    facts = [
        CompanyFact(
            field_name="legal_name",
            value="Oslo Havn KF",
            source_name="Enhetsregisteret",
            source_url="https://api.test/enhet",
        ),
        CompanyFact(
            field_name="org_form_code",
            value="KF",
            source_name="Enhetsregisteret",
            source_url="https://api.test/enhet",
        ),
        CompanyFact(
            field_name="org_form_description",
            value="Kommunalt foretak",
            source_name="Enhetsregisteret",
            source_url="https://api.test/enhet",
        ),
        CompanyFact(
            field_name="business_address",
            value={
                "street": "Akershusstranda 19",
                "postal_code": "0150",
                "city": "OSLO",
                "country": "Norge",
            },
            source_name="Enhetsregisteret",
            source_url="https://api.test/enhet",
        ),
        CompanyFact(
            field_name="foundation_date",
            value="2004-01-01",
            source_name="Enhetsregisteret",
            source_url="https://api.test/enhet",
        ),
    ]
    profile = CompanyProfile(orgnr="986348827", facts=facts)
    envelope = build_terminal_envelope(
        orgnr="986348827",
        run_id="run-addr",
        started_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
        profile=profile,
    )

    view = render_terminal_profile(envelope)
    assert "Registered City:      OSLO" in view
    assert ("Legal Form:           Kommunalt foretak" in view or "KF" in view)
    assert "Founded:              2004-01-01" in view


def test_account_exemptions_and_module_mapping():
    """ENK (sole proprietorship) without accounting obligation must be not_applicable for financials."""
    facts = [
        CompanyFact(
            field_name="legal_name",
            value="Ola Nordmann ENK",
            source_name="Enhetsregisteret",
            source_url="https://api.test/enhet",
        ),
        CompanyFact(
            field_name="org_form_code",
            value="ENK",
            source_name="Enhetsregisteret",
            source_url="https://api.test/enhet",
        ),
        CompanyFact(
            field_name="org_form_description",
            value="Enkeltpersonforetak",
            source_name="Enhetsregisteret",
            source_url="https://api.test/enhet",
        ),
    ]
    profile = CompanyProfile(orgnr="912345678", facts=facts)
    envelope = build_terminal_envelope(
        orgnr="912345678",
        run_id="run-enk",
        started_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
        profile=profile,
    )

    validate_envelope(envelope)
    assert envelope["modules"]["financials"]["state"] == "not_applicable"


def test_generated_html_uses_actual_field_names(tmp_path: Path):
    """Generated HTML report must render city, legal form, and employee count from actual emitted claims."""
    facts = [
        CompanyFact(
            field_name="legal_name",
            value="Bergen Energi AS",
            source_name="Enhetsregisteret",
            source_url="https://api.test/enhet",
        ),
        CompanyFact(
            field_name="org_form_description",
            value="Aksjeselskap",
            source_name="Enhetsregisteret",
            source_url="https://api.test/enhet",
        ),
        CompanyFact(
            field_name="business_address",
            value={"city": "BERGEN", "street": "Bryggen 1"},
            source_name="Enhetsregisteret",
            source_url="https://api.test/enhet",
        ),
        CompanyFact(
            field_name="employee_count",
            value=42,
            source_name="Enhetsregisteret",
            source_url="https://api.test/enhet",
        ),
        CompanyFact(
            field_name="annual_net_result",
            value=1250000.0,
            unit="NOK",
            as_of=date(2024, 12, 31),
            source_name="Regnskapsregisteret",
            source_url="https://api.test/regnskap",
        ),
    ]
    profile = CompanyProfile(orgnr="987654321", facts=facts)
    envelope = build_terminal_envelope(
        orgnr="987654321",
        run_id="run-html",
        started_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
        profile=profile,
    )

    out_file = tmp_path / "dashboard.html"
    generate_html_report([envelope], out_file)
    content = out_file.read_text(encoding="utf-8")

    assert "BERGEN" in content
    assert "Aksjeselskap" in content
    assert "42" in content
    assert ("1 250 000" in content or "1,250,000" in content)


def test_atomic_address_and_expanded_financial_facts():
    """Verify that atomic address fields and additional financial items are extracted properly."""
    raw_enhet = {
        "navn": "Test Corporate AS",
        "forretningsadresse": {
            "poststed": "TRONDHEIM",
            "postnummer": "7010",
            "kommune": "TRONDHEIM",
            "kommunenummer": "5001",
            "adresse": ["Munkegata 1"],
        },
        "naeringskode1": {"kode": "62.010", "beskrivelse": "Programmeringstjenester"},
        "naeringskode2": {"kode": "62.020", "beskrivelse": "Konsulentvirksomhet"},
        "registrertIForetaksregisteret": True,
        "maalform": "Bokmål",
    }
    facts = extract_enhet_facts(raw_enhet, "https://api.test/enhet/123")
    f_map = {f.field_name: f.value for f in facts}

    assert f_map.get("city") == "TRONDHEIM"
    assert f_map.get("postal_code") == "7010"
    assert f_map.get("municipality") == "TRONDHEIM"
    assert f_map.get("street_address") == "Munkegata 1"
    assert f_map.get("secondary_nace_code") == "62.020"
    assert f_map.get("foretaksregister_registered") is True
    assert f_map.get("language") == "Bokmål"


def test_envelope_synthesis_summary_propagation():
    """Verify that executive summary propagates to top-level summary, synthesis, and claims."""
    facts = [
        CompanyFact(
            field_name="legal_name",
            value="Synthesized AS",
            source_name="Enhetsregisteret",
            source_url="https://api.test/enhet",
        )
    ]
    summary_text = "Synthesized AS is a verified Norwegian entity with full test coverage."
    profile = CompanyProfile(orgnr="987654321", facts=facts, executive_summary=summary_text)

    envelope = build_terminal_envelope(
        orgnr="987654321",
        run_id="run-synth",
        started_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
        profile=profile,
    )

    validate_envelope(envelope)
    assert envelope["summary"] == summary_text
    assert envelope["synthesis"] == summary_text
    assert envelope["profile"]["executive_summary"] == summary_text

    synth_claim = next((c for c in envelope["claims"] if c.get("field") == "executive_summary"), None)
    assert synth_claim is not None
    assert synth_claim["value"] == summary_text
    assert synth_claim["availability"] == "available"


def test_summary_multi_source_evidence_attribution():
    """Verify executive_summary cites only used sources and references financial evidence when present."""
    enhet_url = "https://data.brreg.no/enhetsregisteret/api/enheter/923609016"
    regnskap_url = "https://data.brreg.no/regnskapsregisteret/regnskap/923609016"

    # Case A: Profile with only Enhetsregisteret facts
    facts_no_fin = [
        CompanyFact(
            field_name="legal_name",
            value="Norsk Test AS",
            source_name="Enhetsregisteret",
            source_url=enhet_url,
        ),
        CompanyFact(
            field_name="org_form_description",
            value="Aksjeselskap",
            source_name="Enhetsregisteret",
            source_url=enhet_url,
        ),
    ]
    profile_no_fin = CompanyProfile(orgnr="923609016", facts=facts_no_fin)
    summary_no_fin = generate_deterministic_summary(profile_no_fin)
    profile_no_fin.executive_summary = summary_no_fin

    # Must NOT cite Regnskapsregisteret or Roller
    assert "Regnskapsregisteret" not in summary_no_fin
    assert "Roller" not in summary_no_fin
    assert "Enhetsregisteret" in summary_no_fin

    envelope_no_fin = build_terminal_envelope(
        orgnr="923609016",
        run_id="run-attr-1",
        started_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
        profile=profile_no_fin,
    )
    synth_claim_no_fin = next(c for c in envelope_no_fin["claims"] if c.get("field") == "executive_summary")
    # Only references Enhetsregisteret evidence ID (1 evidence)
    assert len(synth_claim_no_fin["evidence_ids"]) == 1

    # Case B: Profile WITH Regnskapsregisteret revenue fact
    facts_with_fin = list(facts_no_fin) + [
        CompanyFact(
            field_name="revenue",
            value=15000000.0,
            unit="NOK",
            as_of=date(2024, 12, 31),
            source_name="Regnskapsregisteret",
            source_url=regnskap_url,
        )
    ]
    profile_with_fin = CompanyProfile(orgnr="923609016", facts=facts_with_fin)
    summary_with_fin = generate_deterministic_summary(profile_with_fin)
    profile_with_fin.executive_summary = summary_with_fin

    # Must cite Enhetsregisteret AND Regnskapsregisteret, but NOT Roller
    assert "Enhetsregisteret" in summary_with_fin
    assert "Regnskapsregisteret" in summary_with_fin
    assert "Roller" not in summary_with_fin

    envelope_with_fin = build_terminal_envelope(
        orgnr="923609016",
        run_id="run-attr-2",
        started_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
        profile=profile_with_fin,
    )
    synth_claim_with_fin = next(c for c in envelope_with_fin["claims"] if c.get("field") == "executive_summary")
    # Must reference both Enhetsregisteret and Regnskapsregisteret evidence IDs
    assert len(synth_claim_with_fin["evidence_ids"]) >= 2
    # Source URL must include both registry references
    assert enhet_url in synth_claim_with_fin["source_url"]
    assert regnskap_url in synth_claim_with_fin["source_url"]


def test_missing_status_not_defaulted_to_active():
    """Missing or null status in profile must not default to 'active'."""
    facts = [
        CompanyFact(
            field_name="legal_name",
            value="Statusless AS",
            source_name="Enhetsregisteret",
            source_url="https://api.test/enhet",
        )
    ]
    profile = CompanyProfile(orgnr="912345678", facts=facts)
    summary = generate_deterministic_summary(profile)
    assert "active" not in summary.lower()
    assert "no legal status reported" in summary.lower()


def test_multiple_jobs_deduplication_preserves_distinct_jobs():
    """Distinct job titles with None as_of must not collapse into a single record."""
    job1 = CompanyFact(
        field_name="job_title",
        value="Senior Python Developer",
        source_name="Company Careers",
        source_url="https://example.com/careers",
        as_of=None,
        retrieved_at=datetime(2026, 11, 1, 12, 0, 0, tzinfo=timezone.utc),
    )
    job2 = CompanyFact(
        field_name="job_title",
        value="Frontend Engineer",
        source_name="Company Careers",
        source_url="https://example.com/careers",
        as_of=None,
        retrieved_at=datetime(2026, 11, 1, 12, 0, 0, tzinfo=timezone.utc),
    )
    job1_dup = CompanyFact(
        field_name="job_title",
        value="senior python developer",  # Duplicate case variation
        source_name="Company Careers",
        source_url="https://example.com/careers",
        as_of=None,
        retrieved_at=datetime(2026, 11, 1, 12, 5, 0, tzinfo=timezone.utc),
    )

    deduped = deduplicate_facts([job1, job2, job1_dup])
    # Must preserve both distinct titles, while pruning the exact duplicate
    assert len(deduped) == 2
    titles = {f.value.lower() for f in deduped}
    assert "senior python developer" in titles
    assert "frontend engineer" in titles


def test_news_parser_avoids_generic_headings():
    """Generic site headings without dates or article markers must not be extracted as news."""
    html_without_dates = """
    <html>
      <body>
        <nav><a href="/">Home</a></nav>
        <h2>Our Services and Product Offerings</h2>
        <h3>About Us and Our Mission</h3>
        <h4>Contact Us Today for a Quote</h4>
      </body>
    </html>
    """
    facts = extract_website_public_activity(html_without_dates, "https://example.com/news")
    news_titles = [f for f in facts if f.field_name == "latest_news_title"]
    # Generic headings must be ignored when no article classes or dates exist
    assert len(news_titles) == 0

    html_with_valid_news = """
    <html>
      <body>
        <div class="news-item">
          <h2>Quarterly Expansion and Revenue Milestone</h2>
          <time datetime="2026-10-15">October 15, 2026</time>
          <p>We are excited to share our latest results.</p>
        </div>
      </body>
    </html>
    """
    facts_valid = extract_website_public_activity(html_with_valid_news, "https://example.com/news")
    news_titles_valid = [f for f in facts_valid if f.field_name == "latest_news_title"]
    assert len(news_titles_valid) == 1
    assert "Quarterly Expansion" in str(news_titles_valid[0].value)
    assert news_titles_valid[0].as_of == date(2026, 10, 15)


def test_financial_differing_currencies_and_unequal_periods_no_false_trend():
    """Different currencies or non-consecutive periods must not produce false comparison trends."""
    # Case A: Different currencies (NOK vs EUR)
    rev_2024_nok = CompanyFact(
        field_name="revenue",
        value=10000000.0,
        unit="NOK",
        as_of=date(2024, 12, 31),
        source_name="Regnskap",
        source_url="https://api.test/regnskap",
    )
    rev_2023_eur = CompanyFact(
        field_name="revenue",
        value=800000.0,
        unit="EUR",
        as_of=date(2023, 12, 31),
        source_name="Regnskap",
        source_url="https://api.test/regnskap",
    )
    profile_diff_curr = CompanyProfile(orgnr="912345678", facts=[rev_2024_nok, rev_2023_eur])
    summary_diff_curr = generate_deterministic_summary(profile_diff_curr)
    # Must NOT claim an increase or decrease when comparing NOK to EUR
    assert "increase" not in summary_diff_curr.lower()
    assert "down" not in summary_diff_curr.lower()

    # Case B: Non-consecutive years (2024 vs 2021)
    rev_2021_nok = CompanyFact(
        field_name="revenue",
        value=5000000.0,
        unit="NOK",
        as_of=date(2021, 12, 31),
        source_name="Regnskap",
        source_url="https://api.test/regnskap",
    )
    profile_non_consec = CompanyProfile(orgnr="912345678", facts=[rev_2024_nok, rev_2021_nok])
    summary_non_consec = generate_deterministic_summary(profile_non_consec)
    assert "increase" not in summary_non_consec.lower()
    assert "down" not in summary_non_consec.lower()
