"""Extractor for job vacancies and careers facts from verified company-owned web pages."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import html
import re
import urllib.parse
from typing import Any

from signalpost.models import CompanyFact

CAREERS_KEYWORDS = [
    r"/karriere\b",
    r"/careers\b",
    r"/ledige-stillinger\b",
    r"/stillinger\b",
    r"/jobb\b",
    r"/jobber\b",
    r"/work-with-us\b",
    r"/join-us\b",
    r"/vacancies\b",
    r"/jobs\b",
]

HIRING_PHRASES = [
    "ledige stillinger",
    "åpne stillinger",
    "ledig stilling",
    "vi søker",
    "bli en del av teamet",
    "jobb hos oss",
    "karriere hos oss",
    "vi ansetter",
    "open positions",
    "we are hiring",
    "join our team",
    "career opportunities",
    "current vacancies",
    "working at",
]


def find_careers_url(html_content: str, base_url: str) -> str | None:
    """Discover link to company careers / jobs page from verified HTML content."""
    # Look for href links matching careers patterns
    links = re.findall(r'<a\s+[^>]*?href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', html_content, re.IGNORECASE | re.DOTALL)
    for href, text in links:
        href_clean = href.strip()
        combined = f"{href_clean} {text}".lower()
        for kw in CAREERS_KEYWORDS:
            if re.search(kw, href_clean, re.IGNORECASE):
                # Resolve relative URL
                full_url = urllib.parse.urljoin(base_url, href_clean)
                parsed = urllib.parse.urlparse(full_url)
                if parsed.scheme in ("http", "https"):
                    return full_url
        if any(phrase in combined for phrase in ("ledige stillinger", "karriere", "careers", "jobb hos oss", "open positions")):
            full_url = urllib.parse.urljoin(base_url, href_clean)
            parsed = urllib.parse.urlparse(full_url)
            if parsed.scheme in ("http", "https"):
                return full_url
    return None


def extract_job_facts(
    page_content: str,
    page_url: str,
    retrieved_at: datetime | None = None,
    content_hash: str | None = None,
) -> list[CompanyFact]:
    """Extract verified job openings, hiring status, and careers facts from page content.

    Only called on pages whose identity has already been verified to prevent hallucinations.
    """
    if not page_content:
        return []

    fetch_time = retrieved_at or datetime.now(timezone.utc)
    raw_hash = content_hash or hashlib.sha256(page_content.encode("utf-8")).hexdigest()

    facts: list[CompanyFact] = []
    unescaped = html.unescape(page_content)
    lower_content = unescaped.lower()

    # 1. Discover careers URL
    careers_url = find_careers_url(page_content, page_url)
    if careers_url:
        facts.append(
            CompanyFact(
                field_name="careers_url",
                value=careers_url,
                source_name="Company Careers",
                source_url=page_url,
                as_of=None,
                retrieved_at=fetch_time,
                confidence="verified_secondary",
                confidence_level=0.95,
                content_hash=raw_hash,
                extraction_method="html_careers_link",
            )
        )

    # 2. Check for active hiring signals
    is_actively_hiring = any(phrase in lower_content for phrase in HIRING_PHRASES)

    # Check for specific job vacancy count patterns (e.g. "3 ledige stillinger", "5 open positions")
    count_match = re.search(
        r'\b(\d{1,3})\s+(?:ledige\s+stillinger|åpne\s+stillinger|open\s+positions|vacancies)\b',
        lower_content,
        re.IGNORECASE,
    )
    if count_match:
        try:
            job_count = int(count_match.group(1))
            facts.append(
                CompanyFact(
                    field_name="job_openings_count",
                    value=job_count,
                    source_name="Company Careers",
                    source_url=page_url,
                    as_of=None,
                    retrieved_at=fetch_time,
                    confidence="verified_secondary",
                    confidence_level=0.9,
                    content_hash=raw_hash,
                    extraction_method="html_job_count",
                )
            )
            is_actively_hiring = True
        except ValueError:
            pass

    if is_actively_hiring:
        facts.append(
            CompanyFact(
                field_name="hiring_status",
                value="active_hiring",
                source_name="Company Careers",
                source_url=page_url,
                as_of=None,
                retrieved_at=fetch_time,
                confidence="verified_secondary",
                confidence_level=0.9,
                content_hash=raw_hash,
                extraction_method="html_hiring_status",
            )
        )

    return facts
