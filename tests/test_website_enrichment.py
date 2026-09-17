"""Unit tests for website enrichment and anti-hallucination verification."""

import pytest
import respx

from signalpost.extractors.website_enrichment import (
    enrich_from_website,
    is_identity_verified_on_page,
)


def test_is_identity_verified_on_page() -> None:
    orgnr = "923609016"
    legal_name = "EQUINOR ASA"

    # 1. Matches raw 9 digits
    assert is_identity_verified_on_page("Welcome to our site. Org nr: 923609016.", orgnr, legal_name) is True

    # 2. Matches spaced format
    assert is_identity_verified_on_page("Org.nr: 923 609 016 MVA", orgnr, legal_name) is True

    # 3. Matches MVA format
    assert is_identity_verified_on_page("Foretaksregisteret: NO 923609016 MVA", orgnr, legal_name) is True

    # 4. Matches exact legal name (case-insensitive)
    assert is_identity_verified_on_page("Copyright 2026 Equinor ASA. All rights reserved.", orgnr, legal_name) is True

    # 5. Fails when neither matches
    assert is_identity_verified_on_page("Welcome to Acme Corp. Call us at 12345.", orgnr, legal_name) is False

    # 6. Fails with different company name
    assert is_identity_verified_on_page("Telenor ASA is a great company.", orgnr, legal_name) is False


@pytest.mark.asyncio
async def test_enrich_from_website_verified_extracts_description(capsys: pytest.CaptureFixture[str]) -> None:
    website = "https://www.equinor.com"
    orgnr = "923609016"
    legal_name = "EQUINOR ASA"

    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <meta name="description" content="Equinor is an international energy company committed to long-term value creation.">
    </head>
    <body>
        <h1>Welcome</h1>
        <footer>Org.nr: 923 609 016</footer>
    </body>
    </html>
    """

    with respx.mock() as respx_mock:
        # Mock robots.txt (allow all)
        respx_mock.get(f"{website}/robots.txt").respond(status_code=200, text="User-agent: *\nAllow: /")
        # Mock homepage
        respx_mock.get(website).respond(status_code=200, text=html, headers={"content-type": "text/html"})

        facts = await enrich_from_website(website, orgnr, legal_name)

    assert len(facts) == 1
    assert facts[0].field_name == "website_description"
    assert facts[0].value == "Equinor is an international energy company committed to long-term value creation."
    assert facts[0].confidence == "unverified_secondary"
    assert facts[0].source_name == "Company Website"
    assert facts[0].source_url == website


@pytest.mark.asyncio
async def test_enrich_from_website_unverified_returns_zero_facts(capsys: pytest.CaptureFixture[str]) -> None:
    website = "https://www.random-blog.com"
    orgnr = "923609016"
    legal_name = "EQUINOR ASA"

    unrelated_html = """
    <!DOCTYPE html>
    <html>
    <head><meta name="description" content="A random personal blog about cooking."></head>
    <body><h1>Cooking recipes</h1></body>
    </html>
    """

    with respx.mock(assert_all_called=False) as respx_mock:
        respx_mock.get(f"{website}/robots.txt").respond(status_code=200, text="User-agent: *\nAllow: /")
        respx_mock.get(website).respond(status_code=200, text=unrelated_html, headers={"content-type": "text/html"})
        # Candidate pages return 404
        respx_mock.get(f"{website}/om-oss").respond(status_code=404)
        respx_mock.get(f"{website}/om").respond(status_code=404)
        respx_mock.get(f"{website}/about").respond(status_code=404)
        respx_mock.get(f"{website}/about-us").respond(status_code=404)
        respx_mock.get(f"{website}/kontakt").respond(status_code=404)
        respx_mock.get(f"{website}/contact").respond(status_code=404)

        facts = await enrich_from_website(website, orgnr, legal_name)

    # ANTI-HALLUCINATION GUARANTEE: Must return 0 facts when identity cannot be verified
    assert len(facts) == 0

    captured = capsys.readouterr()
    assert "match_unverified" in captured.out


@pytest.mark.asyncio
async def test_enrich_from_website_respects_robots_txt() -> None:
    website = "https://www.secret-site.com"
    orgnr = "923609016"
    legal_name = "EQUINOR ASA"

    with respx.mock() as respx_mock:
        # Disallow all bots
        respx_mock.get(f"{website}/robots.txt").respond(status_code=200, text="User-agent: *\nDisallow: /")

        facts = await enrich_from_website(website, orgnr, legal_name)

    assert len(facts) == 0


@pytest.mark.asyncio
async def test_enrich_from_website_with_status_explicit_states() -> None:
    from signalpost.extractors.website_enrichment import enrich_from_website_with_status

    orgnr = "923609016"
    legal_name = "EQUINOR ASA"

    # 1. Blocked via robots.txt
    with respx.mock() as respx_mock:
        respx_mock.get("https://www.blocked.com/robots.txt").respond(status_code=200, text="User-agent: *\nDisallow: /")
        facts, status = await enrich_from_website_with_status("https://www.blocked.com", orgnr, legal_name)
    assert len(facts) == 0
    assert status == "blocked"

    # 2. Ambiguous (no identity proof found)
    with respx.mock(assert_all_called=False) as respx_mock:
        respx_mock.get("https://www.ambiguous.com/robots.txt").respond(status_code=200, text="User-agent: *\nAllow: /")
        respx_mock.get("https://www.ambiguous.com").respond(status_code=200, text="<html>No proof</html>", headers={"content-type": "text/html"})
        for p in ["/om-oss", "/om", "/about", "/about-us", "/kontakt", "/contact"]:
            respx_mock.get(f"https://www.ambiguous.com{p}").respond(status_code=404)
        facts, status = await enrich_from_website_with_status("https://www.ambiguous.com", orgnr, legal_name)
    assert len(facts) == 0
    assert status == "ambiguous"

    # 3. Available (verified with content hash)
    html = '<html><head><meta name="description" content="Official site."></head><body>NO 923609016 MVA</body></html>'
    with respx.mock() as respx_mock:
        respx_mock.get("https://www.verified.com/robots.txt").respond(status_code=200, text="User-agent: *\nAllow: /")
        respx_mock.get("https://www.verified.com").respond(status_code=200, text=html, headers={"content-type": "text/html"})
        facts, status = await enrich_from_website_with_status("https://www.verified.com", orgnr, legal_name)
    assert len(facts) == 1
    assert status == "available"
    assert facts[0].content_hash is not None
    assert len(facts[0].content_hash) == 64
    assert facts[0].extraction_method == "html_meta"
