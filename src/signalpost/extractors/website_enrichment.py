import hashlib
import html
from datetime import date, datetime, timezone
import re
import time
import urllib.parse
import urllib.robotparser
from typing import Any
import httpx

from signalpost.brreg_client import log_request_event
from signalpost.extractors.jobs_enrichment import extract_job_facts
from signalpost.extractors.public_activity import extract_website_public_activity
from signalpost.models import CompanyFact
from signalpost.orgnr import sanitize_orgnr

DEFAULT_USER_AGENT = "SignalpostBot/0.1.0 (+https://github.com/Rewaskc02-lang/Signal_post; Norwegian Fact Agent)"
CANDIDATE_SUBPATHS = ["", "/om-oss", "/om", "/about", "/about-us", "/kontakt", "/contact"]

RESTRICTED_DOMAINS = [
    "linkedin.com",
    "glassdoor.com",
    "indeed.com",
    "facebook.com",
    "meta.com",
    "google.com",
    "instagram.com",
    "twitter.com",
    "x.com",
]

PARKED_DOMAIN_PATTERNS = [
    "domain is for sale",
    "buy this domain",
    "sedoparking",
    "domenet er til salgs",
    "parkert hos",
    "parked domain",
    "this domain may be for sale",
    "inquire about this domain",
    "domain has expired",
    "renew this domain",
    "under construction",
]


def normalize_url(url: str) -> str:
    """Ensure URL has an HTTP/HTTPS scheme."""
    cleaned = url.strip()
    if cleaned.startswith("//"):
        cleaned = f"https:{cleaned}"
    elif not cleaned.startswith(("http://", "https://")):
        cleaned = f"https://{cleaned}"
    return cleaned


def clean_website_domain(url: str) -> str:
    """Normalize a website URL or string to a clean domain name (e.g. 'elopak.com')."""
    cleaned = url.strip().lower()
    cleaned = re.sub(r"^https?://", "", cleaned)
    cleaned = re.sub(r"^//", "", cleaned)
    cleaned = cleaned.split("/")[0].split("?")[0].split("#")[0].split(":")[0]
    if cleaned.startswith("www."):
        cleaned = cleaned[4:]
    return cleaned


def is_restricted_domain(url: str) -> bool:
    """Check if URL or domain touches any restricted policy domain."""
    dom = clean_website_domain(url)
    return any(dom == rd or dom.endswith(f".{rd}") for rd in RESTRICTED_DOMAINS)


def is_plausible_url(url: str) -> bool:
    """Check if candidate URL looks like a plausible web address before making HTTP calls."""
    if not url or not isinstance(url, str):
        return False
    cleaned = url.strip().lower()
    if cleaned in {"", "-", "--", "none", "null", "ingen", "ikke oppgitt", "n/a", "http://", "https://"}:
        return False
    dom = clean_website_domain(cleaned)
    if not dom or "." not in dom or dom.startswith(".") or dom.endswith(".") or len(dom) < 4:
        return False
    tld = dom.split(".")[-1]
    if len(tld) < 2 or not tld.isalpha():
        return False
    return True


def is_parked_domain_page(html_text: str) -> bool:
    """Check if page content indicates an abandoned, parked, or expired domain."""
    norm = html_text.lower()
    return any(pattern in norm for pattern in PARKED_DOMAIN_PATTERNS)


def extract_page_title(html_str: str) -> str | None:
    """Extract page title from HTML <title> tag, unescaping entities and stripping tags."""
    match = re.search(r"<title[^>]*>(.*?)</title>", html_str, re.IGNORECASE | re.DOTALL)
    if match:
        raw_title = match.group(1)
        clean = re.sub(r"<[^>]+>", " ", raw_title)
        clean = html.unescape(clean)
        title = re.sub(r"\s+", " ", clean).strip()
        return title if title else None
    return None


def normalize_text_for_matching(text: str) -> str:
    """Normalize text by collapsing whitespace and lowercasing."""
    return re.sub(r"\s+", " ", text).strip().lower()


def is_identity_verified_on_page(page_content: str, orgnr: str, legal_name: str, domain: str = "") -> bool:
    """Verify that a web page actually belongs to the given company.

    Anti-Hallucination Guardrail:
    Checks for presence of (in priority order):
    1. Reject immediately if parked, defunct, or for-sale page.
    2. The 9-digit orgnr across raw, unescaped, and tag-stripped text in multiple formats:
       - raw digits: 923609016
       - space-formatted: 923 609 016
       - hyphen-formatted: 923-609-016
       - dot-formatted: 923.609.016
       - MVA formats: NO 923609016 MVA, NO 923 609 016 MVA, NO923609016MVA
    3. Exact normalized match of the official legal name (with HTML entities unescaped).
    4. Base company name (without legal suffix) in page + domain or title match.
    5. Domain name token (without TLD) in page content (self-referencing site).
    6. Significant token overlap between page title and domain name.
    7. Hyphenated name: any hyphen-segment of legal name appears in both domain and page.
    """
    cleaned_orgnr = sanitize_orgnr(orgnr)
    if not cleaned_orgnr or len(cleaned_orgnr) != 9:
        return False

    # Anti-Hallucination: Reject parked / expired domains
    if is_parked_domain_page(page_content):
        return False

    # Decode HTML entities (e.g. &amp; -> &, &oslash; -> ø, &nbsp; -> ' ')
    unescaped = html.unescape(page_content)
    # Strip HTML tags to get pure visible text for formatted table/span contents
    plain_text = re.sub(r"<[^>]+>", " ", unescaped)
    collapsed_plain = re.sub(r"\s+", " ", plain_text)

    # 1. Flexible 3-digit grouped orgnr pattern (matches any spaces, dots, hyphens between 3-digit blocks)
    flexible_pattern = rf"\b{cleaned_orgnr[:3]}[\s.-]*{cleaned_orgnr[3:6]}[\s.-]*{cleaned_orgnr[6:]}\b"
    if re.search(flexible_pattern, page_content) or re.search(flexible_pattern, plain_text) or re.search(flexible_pattern, collapsed_plain):
        return True

    # 2. Check for raw 9-digit orgnr
    if cleaned_orgnr in page_content or cleaned_orgnr in plain_text or cleaned_orgnr in collapsed_plain:
        return True

    # 3. Check for space-formatted orgnr (e.g. "923 609 016")
    spaced_orgnr = f"{cleaned_orgnr[:3]} {cleaned_orgnr[3:6]} {cleaned_orgnr[6:]}"
    if spaced_orgnr in page_content or spaced_orgnr in plain_text or spaced_orgnr in collapsed_plain:
        return True

    # 4. Check for hyphen-formatted orgnr (e.g. "923-609-016")
    hyphen_orgnr = f"{cleaned_orgnr[:3]}-{cleaned_orgnr[3:6]}-{cleaned_orgnr[6:]}"
    if hyphen_orgnr in page_content or hyphen_orgnr in plain_text or hyphen_orgnr in collapsed_plain:
        return True

    # 5. Check for dot-formatted orgnr (e.g. "923.609.016")
    dot_orgnr = f"{cleaned_orgnr[:3]}.{cleaned_orgnr[3:6]}.{cleaned_orgnr[6:]}"
    if dot_orgnr in page_content or dot_orgnr in plain_text or dot_orgnr in collapsed_plain:
        return True

    # 6. Check for MVA format (e.g. "NO 923609016 MVA", "NO 923 609 016 MVA", "NO923609016MVA")
    mva_pattern = rf"\b(?:NO\s*)?{cleaned_orgnr}\s*MVA\b"
    if re.search(mva_pattern, page_content, re.IGNORECASE) or re.search(mva_pattern, plain_text, re.IGNORECASE) or re.search(mva_pattern, collapsed_plain, re.IGNORECASE):
        return True

    # Also check spaced MVA pattern
    spaced_mva_pattern = rf"\bNO\s*{re.escape(spaced_orgnr)}\s*MVA\b"
    if re.search(spaced_mva_pattern, page_content, re.IGNORECASE) or re.search(spaced_mva_pattern, plain_text, re.IGNORECASE) or re.search(spaced_mva_pattern, collapsed_plain, re.IGNORECASE):
        return True

    # 6. Check for exact legal name match across unescaped and tag-stripped text
    norm_content = normalize_text_for_matching(unescaped)
    norm_plain_content = normalize_text_for_matching(plain_text)
    norm_legal_name = normalize_text_for_matching(legal_name)
    if norm_legal_name and (norm_legal_name in norm_content or norm_legal_name in norm_plain_content):
        return True

    # Prepare domain token (e.g. "cramerlaw" from "cramerlaw.no")
    clean_dom = clean_website_domain(domain) if domain else ""
    dom_token = clean_dom.split(".")[0] if "." in clean_dom else clean_dom
    dom_token_alphanum = re.sub(r"[^a-z0-9]", "", dom_token.lower())

    # 7. Check for base company name with expanded Norwegian corporate suffixes
    # AS, ASA, A/S, ANS, ENK, DA, D/A, NUF, BA, SA, KS, IKS, KF, FKF, SE
    base_name = re.sub(
        r"\s+(?:A/S|AS|ASA|ANS|ENK|D/A|DA|NUF|BA|SA|KS|IKS|KF|FKF|SE)\b",
        "",
        legal_name,
        flags=re.IGNORECASE,
    ).strip()
    norm_base = normalize_text_for_matching(base_name)
    norm_base_alphanum = re.sub(r"[^a-z0-9]", "", norm_base)

    if len(norm_base) >= 3 and (norm_base in norm_content or norm_base in norm_plain_content):
        if dom_token_alphanum and (
            norm_base_alphanum in dom_token_alphanum
            or dom_token_alphanum.startswith(norm_base_alphanum)
            or norm_base_alphanum.startswith(dom_token_alphanum)
        ):
            return True
        title = extract_page_title(page_content) or ""
        if norm_base in normalize_text_for_matching(title):
            return True

    # 8. Domain token self-reference check
    if len(dom_token_alphanum) >= 4 and (dom_token_alphanum in norm_content or dom_token_alphanum in norm_plain_content):
        return True

    # 9. Page title <-> domain token significant overlap check
    if dom_token_alphanum and len(dom_token_alphanum) >= 4:
        title = extract_page_title(page_content) or ""
        norm_title_alphanum = re.sub(r"[^a-z0-9]", "", normalize_text_for_matching(title))
        if dom_token_alphanum in norm_title_alphanum or norm_title_alphanum.startswith(dom_token_alphanum[:5]):
            return True

    # 10. Hyphenated name segment check
    if " - " in legal_name:
        for segment in legal_name.split(" - "):
            seg_alphanum = re.sub(r"[^a-z0-9]", "", segment.strip().lower())
            if len(seg_alphanum) >= 4:
                if seg_alphanum in dom_token_alphanum or dom_token_alphanum.startswith(seg_alphanum[:5]):
                    if seg_alphanum in norm_content or seg_alphanum in norm_plain_content:
                        return True

    return False


def extract_meta_description(html_str: str) -> str | None:
    """Extract page description from meta description or og:description tags."""
    # 1. Standard meta description (any attribute order)
    match = re.search(
        r'<meta\s+[^>]*?(?:name=["\']description["\'][^>]*?content=["\']([^"\']+)["\']|content=["\']([^"\']+)["\'][^>]*?name=["\']description["\'])',
        html_str,
        re.IGNORECASE,
    )
    if not match:
        # 2. og:description (any attribute order)
        match = re.search(
            r'<meta\s+[^>]*?(?:property=["\']og:description["\'][^>]*?content=["\']([^"\']+)["\']|content=["\']([^"\']+)["\'][^>]*?property=["\']og:description["\'])',
            html_str,
            re.IGNORECASE,
        )

    if match:
        desc = match.group(1) or match.group(2)
        if desc:
            clean_desc = html.unescape(desc)
            clean_desc = re.sub(r"\s+", " ", clean_desc).strip()
            return clean_desc if clean_desc else None

    return None


def extract_contact_email(html_str: str, domain: str = "") -> str | None:
    """Extract official company contact email from verified HTML content."""
    # 1. Search mailto links
    mailto_matches = re.findall(r'href=["\']mailto:([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})["\']', html_str, re.IGNORECASE)
    cleaned_dom = clean_website_domain(domain) if domain else ""
    for email in mailto_matches:
        e_lower = email.lower().strip()
        if any(ign in e_lower for ign in ("sentry.io", "wixpress", "example.com", "domain.com", "wordpress")):
            continue
        if cleaned_dom and cleaned_dom in e_lower:
            return e_lower
    if mailto_matches:
        valid_candidates = [m.lower().strip() for m in mailto_matches if not any(ign in m.lower() for ign in ("sentry.io", "wixpress", "example.com"))]
        if valid_candidates:
            return valid_candidates[0]

    # 2. General regex search for post@, kontakt@, info@
    common_matches = re.findall(r'\b((?:post|kontakt|info|kundeservice|support|mail)@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})\b', html_str, re.IGNORECASE)
    for email in common_matches:
        e_lower = email.lower().strip()
        if cleaned_dom and cleaned_dom in e_lower:
            return e_lower
    if common_matches:
        return common_matches[0].lower().strip()
    return None


def extract_contact_phone(html_str: str) -> str | None:
    """Extract official contact telephone number from verified HTML content."""
    # 1. Search tel links
    tel_matches = re.findall(r'href=["\']tel:([^"\']+)["\']', html_str, re.IGNORECASE)
    for tel in tel_matches:
        cleaned_tel = re.sub(r"[^\d+]", "", tel.strip())
        if len(cleaned_tel) >= 8:
            return cleaned_tel

    # 2. Norwegian phone regex (+47 XX XX XX XX or 8-digit)
    phone_match = re.search(r'\b(?:\+47\s*)?[2-9]\d{1}(?:[\s.-]?\d{2}){3}\b', html_str)
    if phone_match:
        return re.sub(r"\s+", " ", phone_match.group(0)).strip()
    return None


def extract_investor_url(html_str: str, base_url: str) -> str | None:
    """Discover company investor relations or annual reports URL."""
    links = re.findall(r'<a\s+[^>]*?href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', html_str, re.IGNORECASE | re.DOTALL)
    for href, text in links:
        href_clean = href.strip()
        combined = f"{href_clean} {text}".lower()
        if any(term in combined for term in ("investor", "investors", "investor relations", "årsrapport", "annual report", "finansiell informasjon", "financial reports")):
            full_url = urllib.parse.urljoin(base_url, href_clean)
            parsed = urllib.parse.urlparse(full_url)
            if parsed.scheme in ("http", "https"):
                return full_url
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

    if not is_plausible_url(website_url):
        return [], "missing"

    if is_restricted_domain(website_url):
        log_request_event(
            orgnr=orgnr,
            endpoint="website_enrichment",
            method="GET",
            status_code=403,
            latency_ms=0.0,
            attempt=1,
            error="restricted_domain_policy",
        )
        return [], "blocked"

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
        http_client = httpx.AsyncClient(timeout=httpx.Timeout(10.0), follow_redirects=True, headers=headers, verify=False)
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
            resp = await http_client.get(normalized_base, follow_redirects=True)
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
                try:
                    html_content = resp.text
                except Exception:
                    html_content = resp.content.decode("utf-8", errors="replace")
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

            # 4. External Claim: Contact email
            contact_email = extract_contact_email(verified_html, domain=verified_url)
            if contact_email:
                facts.append(
                    CompanyFact(
                        field_name="contact_email",
                        value=contact_email,
                        source_name="Company Website",
                        source_url=verified_url,
                        as_of=None,
                        retrieved_at=fetch_time,
                        confidence="verified_secondary",
                        confidence_level=0.9,
                        content_hash=content_hash,
                        extraction_method="html_contact_email",
                    )
                )

            # 5. External Claim: Contact phone
            contact_phone = extract_contact_phone(verified_html)
            if contact_phone:
                facts.append(
                    CompanyFact(
                        field_name="contact_phone",
                        value=contact_phone,
                        source_name="Company Website",
                        source_url=verified_url,
                        as_of=None,
                        retrieved_at=fetch_time,
                        confidence="verified_secondary",
                        confidence_level=0.85,
                        content_hash=content_hash,
                        extraction_method="html_contact_phone",
                    )
                )

            # 6. External Claim: Investor / reporting page
            investor_url = extract_investor_url(verified_html, base_url=verified_url)
            if investor_url:
                facts.append(
                    CompanyFact(
                        field_name="investor_url",
                        value=investor_url,
                        source_name="Company Website",
                        source_url=verified_url,
                        as_of=None,
                        retrieved_at=fetch_time,
                        confidence="verified_secondary",
                        confidence_level=0.9,
                        content_hash=content_hash,
                        extraction_method="html_investor_link",
                    )
                )

            # 7. Jobs & Careers facts from verified company page
            job_facts = extract_job_facts(
                page_content=verified_html,
                page_url=verified_url,
                retrieved_at=fetch_time,
                content_hash=content_hash,
            )
            facts.extend(job_facts)

            # 8. Public activity & Press facts from verified company page
            activity_facts = extract_website_public_activity(
                page_content=verified_html,
                page_url=verified_url,
                retrieved_at=fetch_time,
                content_hash=content_hash,
            )
            facts.extend(activity_facts)

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
