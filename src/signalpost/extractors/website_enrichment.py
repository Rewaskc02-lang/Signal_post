"""Secondary source enrichment from company websites with strict identity verification."""

from datetime import date, datetime, timezone
import re
import time
import urllib.parse
import urllib.robotparser
from typing import Any
import httpx

from signalpost.brreg_client import log_request_event
from signalpost.models import CompanyFact
from signalpost.orgnr import sanitize_orgnr

DEFAULT_USER_AGENT = "SignalpostBot/0.1.0 (+https://github.com/Rewaskc02-lang/Signal_post; Norwegian Fact Agent)"
CANDIDATE_SUBPATHS = ["", "/om-oss", "/om", "/about", "/about-us", "/kontakt", "/contact"]


def normalize_url(url: str) -> str:
    """Ensure URL has an HTTP/HTTPS scheme."""
    cleaned = url.strip()
    if not cleaned.startswith(("http://", "https://")):
        cleaned = f"https://{cleaned}"
    return cleaned


def normalize_text_for_matching(text: str) -> str:
    """Normalize text by collapsing whitespace and lowercasing."""
    return re.sub(r"\s+", " ", text).strip().lower()


def is_identity_verified_on_page(page_content: str, orgnr: str, legal_name: str) -> bool:
    """Verify that a web page actually belongs to the given company.

    Anti-Hallucination Guardrail:
    Checks for presence of:
    1. The 9-digit orgnr (e.g. 923609016, 923 609 016, NO923609016MVA).
    2. Or an exact normalized match of the official legal name (e.g. 'Equinor ASA').
    """
    cleaned_orgnr = sanitize_orgnr(orgnr)
    if not cleaned_orgnr or len(cleaned_orgnr) != 9:
        return False

    # Check for raw 9-digit orgnr
    if cleaned_orgnr in page_content:
        return True

    # Check for space-formatted orgnr (e.g. "923 609 016")
    spaced_orgnr = f"{cleaned_orgnr[:3]} {cleaned_orgnr[3:6]} {cleaned_orgnr[6:]}"
    if spaced_orgnr in page_content:
        return True

    # Check for MVA format (e.g. "NO 923609016 MVA" or "NO923609016MVA")
    mva_pattern = rf"NO\s*{cleaned_orgnr}\s*MVA"
    if re.search(mva_pattern, page_content, re.IGNORECASE):
        return True

    # Check for exact legal name match
    norm_content = normalize_text_for_matching(page_content)
    norm_legal_name = normalize_text_for_matching(legal_name)
    if norm_legal_name and norm_legal_name in norm_content:
        return True

    return False


def extract_meta_description(html: str) -> str | None:
    """Extract page description from meta description or og:description tags."""
    # Try standard meta description
    match = re.search(r'<meta\s+name=["\']description["\']\s+content=["\']([^"\']+)["\']', html, re.IGNORECASE)
    if not match:
        # Try alternate ordering: content before name
        match = re.search(r'<meta\s+content=["\']([^"\']+)["\']\s+name=["\']description["\']', html, re.IGNORECASE)
    if not match:
        # Try og:description
        match = re.search(r'<meta\s+property=["\']og:description["\']\s+content=["\']([^"\']+)["\']', html, re.IGNORECASE)

    if match:
        desc = match.group(1).strip()
        # Clean up any HTML entities or excess whitespace
        return re.sub(r"\s+", " ", desc) if desc else None

    return None


async def is_url_allowed_by_robots(base_origin: str, target_url: str, user_agent: str, client: httpx.AsyncClient) -> bool:
    """Check robots.txt permissions before crawling."""
    robots_url = urllib.parse.urljoin(base_origin, "/robots.txt")
    rp = urllib.robotparser.RobotFileParser()
    try:
        resp = await client.get(robots_url, timeout=5.0)
        if resp.status_code == 200:
            rp.parse(resp.text.splitlines())
            return rp.can_fetch(user_agent, target_url)
        elif resp.status_code in (401, 403):
            return False
        return True
    except Exception:
        # If robots.txt cannot be fetched or times out, default to allowed for public GET
        return True


async def enrich_from_website(
    website_url: str,
    orgnr: str,
    legal_name: str,
    client: httpx.AsyncClient | None = None,
    user_agent: str = DEFAULT_USER_AGENT,
) -> list[CompanyFact]:
    """Fetch company website, verify identity proof, and extract secondary facts.

    Anti-Hallucination & Provenance Rules:
    - If neither org number nor exact legal name is found on the fetched pages,
      returns 0 facts and logs a structured 'match_unverified' event.
    - All extracted secondary facts carry confidence='unverified_secondary'.
    - Exact source URL of the verifying page is attached.

    Args:
        website_url: Homepage URL.
        orgnr: 9-digit organisation number.
        legal_name: Official legal name from Enhetsregisteret.
        client: Optional httpx.AsyncClient.
        user_agent: Custom User-Agent header string.

    Returns:
        List of CompanyFact instances (empty if unverified or unreachable).
    """
    if not website_url or not orgnr:
        return []

    normalized_base = normalize_url(website_url)
    parsed = urllib.parse.urlparse(normalized_base)
    base_origin = f"{parsed.scheme}://{parsed.netloc}"

    headers = {
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml",
    }

    should_close_client = False
    http_client = client
    if http_client is None:
        http_client = httpx.AsyncClient(timeout=httpx.Timeout(10.0), follow_redirects=True, headers=headers)
        should_close_client = True

    start_time = time.perf_counter()
    verified_url: str | None = None
    extracted_description: str | None = None
    fetch_time = datetime.now(timezone.utc)

    try:
        # Check robots.txt first
        allowed = await is_url_allowed_by_robots(base_origin, normalized_base, user_agent, http_client)
        if not allowed:
            log_request_event(
                orgnr=orgnr,
                endpoint="website_enrichment",
                method="GET",
                status_code=403,
                latency_ms=(time.perf_counter() - start_time) * 1000.0,
                attempt=1,
                error="robots_disallowed",
            )
            return []

        # Iterate candidate pages to find verification
        for subpath in CANDIDATE_SUBPATHS:
            target_url = urllib.parse.urljoin(base_origin, subpath) if subpath else normalized_base
            try:
                resp = await http_client.get(target_url)
                if resp.status_code == 200 and "text/html" in resp.headers.get("content-type", ""):
                    html_content = resp.text
                    if is_identity_verified_on_page(html_content, orgnr, legal_name):
                        verified_url = str(resp.url)
                        extracted_description = extract_meta_description(html_content)
                        break
            except Exception:
                continue

        latency_ms = (time.perf_counter() - start_time) * 1000.0

        if not verified_url:
            # Identity proof missing -> fail safe, return zero facts
            log_request_event(
                orgnr=orgnr,
                endpoint="website_enrichment",
                method="GET",
                status_code=None,
                latency_ms=latency_ms,
                attempt=1,
                error="match_unverified",
            )
            return []

        # Verified! Log successful verification
        log_request_event(
            orgnr=orgnr,
            endpoint="website_enrichment",
            method="GET",
            status_code=200,
            latency_ms=latency_ms,
            attempt=1,
            error=None,
        )

        facts: list[CompanyFact] = []
        if extracted_description:
            facts.append(
                CompanyFact(
                    field_name="website_description",
                    value=extracted_description,
                    source_name="Company Website",
                    source_url=verified_url,
                    as_of=date.today(),
                    retrieved_at=fetch_time,
                    confidence="unverified_secondary",
                )
            )

        return facts

    finally:
        if should_close_client and not http_client.is_closed:
            await http_client.aclose()
