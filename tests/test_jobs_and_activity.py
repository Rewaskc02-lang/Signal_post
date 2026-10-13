"""Unit tests for jobs enrichment and public activity extractors."""

from datetime import datetime, timezone
import pytest

from signalpost.extractors.jobs_enrichment import extract_job_facts, find_careers_url
from signalpost.extractors.public_activity import (
    extract_registry_public_activity,
    extract_website_public_activity,
    find_news_url,
)


def test_find_careers_url() -> None:
    html = """
    <html>
      <body>
        <nav>
          <a href="/om-oss">Om oss</a>
          <a href="/karriere">Karriere</a>
          <a href="/kontakt">Kontakt</a>
        </nav>
      </body>
    </html>
    """
    url = find_careers_url(html, "https://example.no")
    assert url == "https://example.no/karriere"


def test_extract_job_facts_active_hiring() -> None:
    html = """
    <html>
      <body>
        <h1>Velkommen til Equinor</h1>
        <a href="/careers">Se 5 ledige stillinger hos oss</a>
      </body>
    </html>
    """
    now = datetime.now(timezone.utc)
    facts = extract_job_facts(html, "https://www.equinor.com", retrieved_at=now)
    assert len(facts) >= 2

    fact_fields = {f.field_name: f.value for f in facts}
    assert "careers_url" in fact_fields
    assert fact_fields["careers_url"] == "https://www.equinor.com/careers"
    assert "hiring_status" in fact_fields
    assert fact_fields["hiring_status"] == "active_hiring"
    assert "job_openings_count" in fact_fields
    assert fact_fields["job_openings_count"] == 5


def test_extract_job_facts_empty() -> None:
    html = "<html><body><p>Bare et lite firma uten jobbseksjon.</p></body></html>"
    facts = extract_job_facts(html, "https://smallbiz.no")
    assert facts == []


def test_find_news_url() -> None:
    html = """
    <html>
      <body>
        <header>
          <a href="/nyheter">Nyheter</a>
        </header>
      </body>
    </html>
    """
    url = find_news_url(html, "https://example.no")
    assert url == "https://example.no/nyheter"


def test_extract_website_public_activity() -> None:
    html = """
    <html>
      <body>
        <a href="/pressemeldinger">Pressemeldinger</a>
        <div class="news-feed">
          <h2>Equinor kunngjør nytt fornybarprosjekt i Nordsjøen</h2>
        </div>
      </body>
    </html>
    """
    now = datetime.now(timezone.utc)
    facts = extract_website_public_activity(html, "https://www.equinor.com", retrieved_at=now)
    assert len(facts) >= 2

    fields = {f.field_name: f.value for f in facts}
    assert "press_url" in fields
    assert fields["press_url"] == "https://www.equinor.com/pressemeldinger"
    assert "latest_news_title" in fields
    assert "fornybarprosjekt" in fields["latest_news_title"]


def test_extract_registry_public_activity() -> None:
    enhet_data = {
        "organisasjonsnummer": "923609016",
        "navn": "EQUINOR ASA",
        "konkurs": False,
        "underAvvikling": False,
        "registreringsdatoEnhetsregisteret": "1972-09-18",
    }
    facts = extract_registry_public_activity(enhet_data, "https://data.brreg.no/enhetsregisteret/api/enheter/923609016")
    fields = {f.field_name: f.value for f in facts}
    assert fields["legal_status"] == "Normal drift (Active)"
    assert fields["registry_registration_date"] == "1972-09-18"


def test_extract_registry_public_activity_konkurs() -> None:
    enhet_data = {
        "organisasjonsnummer": "999999999",
        "navn": "KONKURS AS",
        "konkurs": True,
        "underAvvikling": False,
    }
    facts = extract_registry_public_activity(enhet_data, "https://data.brreg.no/enhetsregisteret/api/enheter/999999999")
    fields = {f.field_name: f.value for f in facts}
    assert "Konkurs" in fields["legal_status"]
