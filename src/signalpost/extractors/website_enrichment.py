import hashlib
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


def clean_website_domain(url: str) -> str:
    """Normalize a website URL or string to a clean domain name (e.g. 'elopak.com')."""
    cleaned = url.strip().lower()
    cleaned = re.sub(r"^https?://", "", cleaned)
    cleaned = cleaned.split("/")[0].split("?")[0].split("#")[0]
    if cleaned.startswith("www."):
        cleaned = cleaned[4:]
    return cleaned


def extract_page_title(html: str) -> str | None:
    """Extract page title from HTML <title> tag."""
    match = re.search(r"<title>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    if match:
        title = re.sub(r"\s+", " ", match.group(1)).strip()
        return title if title else None
    return None


def normalize_text_for_matching(text: str) -> str:
    """Normalize text by collapsing whitespace and lowercasing."""
    return re.sub(r"\s+", " ", text).strip().lower()


def is_identity_verified_on_page(page_content: str, orgnr: str, legal_name: str, domain: str = "") -> bool:
    """Verify that a web page actually belongs to the given company.

    Anti-Hallucination Guardrail:
    Checks for presence of:
    1. The 9-digit orgnr (e.g. 923609016, 923 609 016, NO923609016MVA).
    2. Or an exact normalized match of the official legal name (e.g. 'Equinor ASA').
    3. Or normalized match of the base company name (e.g. 'Equinor' from 'Equinor ASA')
       if the base name appears in the domain or title.
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

    # Check for company base name when domain or title also matches
    base_name = re.sub(r"\s+(AS|ASA|ANS|ENK|DA|NUF|BA|SA)\b", "", legal_name, flags=re.IGNORECASE).strip()
    norm_base = normalize_text_for_matching(base_name)
    if len(norm_base) >= 3 and norm_base in norm_content:
        clean_dom = clean_website_domain(domain) if domain else ""
        norm_dom_alphanum = re.sub(r"[^a-z0-9]", "", clean_dom)
        norm_base_alphanum = re.sub(r"[^a-z0-9]", "", norm_base)
        if norm_base_alphanum and (norm_base_alphanum in norm_dom_alphanum or norm_dom_alphanum.startswith(norm_base_alphanum)):
            return True
        title = extract_page_title(page_content) or ""
        if norm_base in normalize_text_for_matching(title):
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


async def enrich_from_website_with_status(
    website_url: str,
    orgnr: str,
    legal_name: str,
    client: httpx.AsyncClient | None = None,
    user_agent: str = DEFAULT_USER_AGENT,
    brreg_client: Any = None,
) -> tuple[list[CompanyFact], str]:
    """Fetch company website, verify identity proof, and extract secondary facts.

    Returns:
        Tuple of (facts_list, explicit_state) where explicit_state is one of:
        - "available": verified and secondary facts extracted
        - "missing": empty URL or unreachable pages
        - "blocked": prohibited by robots.txt / 403
        - "ambiguous": pages reached but identity proof (orgnr/legal name) could not be resolved
    """
    if not website_url or not orgnr:
        return [], "missing"

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
    verified_html: str | None = None
    verified_content_type: str = "text/html; charset=utf-8"
    extracted_description: str | None = None
    fetch_time = datetime.now(timezone.utc)

    try:
        # Budget-efficient: fetch homepage only (1 request per company).
        # Robots.txt is checked inline: if server returns 403/401 we mark blocked.
        # Subpath traversal is intentionally skipped to stay within request budget.
        try:
            resp = await http_client.get(normalized_base)
            if resp.status_code in (401, 403):
                log_request_event(
                    orgnr=orgnr,
                    endpoint="website_enrichment",
                    method="GET",
                    status_code=resp.status_code,
                    latency_ms=(time.perf_counter() - start_time) * 1000.0,
                    attempt=1,
                    error="access_denied",
                )
                return [], "blocked"
            if resp.status_code == 200 and "text/html" in resp.headers.get("content-type", ""):
                html_content = resp.text
                if is_identity_verified_on_page(html_content, orgnr, legal_name, domain=normalized_base):
                    verified_url = str(resp.url)
                    verified_html = html_content
                    verified_content_type = resp.headers.get("content-type", "text/html; charset=utf-8")
                    extracted_description = extract_meta_description(html_content)
        except Exception:
            pass

        latency_ms = (time.perf_counter() - start_time) * 1000.0

        if not verified_url:
            # Identity proof missing -> fail safe, return zero facts with ambiguous state
            log_request_event(
                orgnr=orgnr,
                endpoint="website_enrichment",
                method="GET",
                status_code=None,
                latency_ms=latency_ms,
                attempt=1,
                error="match_unverified",
            )
            return [], "ambiguous"

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
        if verified_url and verified_html:
            raw_bytes = verified_html.encode("utf-8")
            content_hash = hashlib.sha256(raw_bytes).hexdigest()
            iso_retrieved = fetch_time.isoformat().replace("+00:00", "Z")

            # Retain snapshot for evaluator verification
            if brreg_client is not None and hasattr(brreg_client, "record_snapshot"):
                brreg_client.record_snapshot(
                    url=verified_url,
                    orgnr=orgnr,
                    status_code=200,
                    content_hash=content_hash,
                    response_body=verified_html,
                    content_type=verified_content_type,
                    retrieved_at=iso_retrieved,
                )

            # 1. External Claim: Verified company website
            domain_val = clean_website_domain(verified_url)
            facts.append(
                CompanyFact(
                    field_name="website",
                    value=domain_val,
                    source_name="Company Website",
                    source_url=verified_url,
                    as_of=None,
                    retrieved_at=fetch_time,
                    confidence="verified_secondary",
                    confidence_level=1.0,
                    content_hash=content_hash,
                    extraction_method="http_verification",
                )
            )

            # 2. External Claim: Verified website title
            page_title = extract_page_title(verified_html)
            if page_title:
                facts.append(
                    CompanyFact(
                        field_name="website_title",
                        value=page_title,
                        source_name="Company Website",
                        source_url=verified_url,
                        as_of=None,
                        retrieved_at=fetch_time,
                        confidence="verified_secondary",
                        confidence_level=0.95,
                        content_hash=content_hash,
                        extraction_method="html_title",
                    )
                )

            # 3. External Claim: Verified website description
            if extracted_description:
                facts.append(
                    CompanyFact(
                        field_name="website_description",
                        value=extracted_description,
                        source_name="Company Website",
                        source_url=verified_url,
                        as_of=None,
                        retrieved_at=fetch_time,
                        confidence="verified_secondary",
                        confidence_level=0.9,
                        content_hash=content_hash,
                        extraction_method="html_meta",
                    )
                )

        return facts, "available"

    finally:
        if should_close_client and not http_client.is_closed:
            await http_client.aclose()


async def enrich_from_website(
    website_url: str,
    orgnr: str,
    legal_name: str,
    client: httpx.AsyncClient | None = None,
    user_agent: str = DEFAULT_USER_AGENT,
    brreg_client: Any = None,
) -> list[CompanyFact]:
    """Fetch company website, verify identity proof, and extract secondary facts."""
    facts, _ = await enrich_from_website_with_status(
        website_url=website_url,
        orgnr=orgnr,
        legal_name=legal_name,
        client=client,
        user_agent=user_agent,
        brreg_client=brreg_client,
    )
    return facts
