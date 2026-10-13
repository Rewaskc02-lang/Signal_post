"""Extractor for public activity, legal status events, and corporate announcements."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import html
import re
import urllib.parse
from typing import Any

from signalpost.models import CompanyFact

NEWS_KEYWORDS = [
    r"/nyheter\b",
    r"/news\b",
    r"/presse\b",
    r"/pressemeldinger\b",
    r"/aktuelt\b",
    r"/presse-og-aktuelt\b",
    r"/press-releases\b",
    r"/media\b",
]


def find_news_url(html_content: str, base_url: str) -> str | None:
    """Discover link to company news / press releases from verified HTML content."""
    links = re.findall(r'<a\s+[^>]*?href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', html_content, re.IGNORECASE | re.DOTALL)
    for href, text in links:
        href_clean = href.strip()
        combined = f"{href_clean} {text}".lower()
        for kw in NEWS_KEYWORDS:
            if re.search(kw, href_clean, re.IGNORECASE):
                full_url = urllib.parse.urljoin(base_url, href_clean)
                parsed = urllib.parse.urlparse(full_url)
                if parsed.scheme in ("http", "https"):
                    return full_url
        if any(phrase in combined for phrase in ("pressemeldinger", "siste nytt", "aktuelt", "nyheter", "latest news")):
            full_url = urllib.parse.urljoin(base_url, href_clean)
            parsed = urllib.parse.urlparse(full_url)
            if parsed.scheme in ("http", "https"):
                return full_url
    return None


def extract_website_public_activity(
    page_content: str,
    page_url: str,
    retrieved_at: datetime | None = None,
    content_hash: str | None = None,
) -> list[CompanyFact]:
    """Extract public activity and press facts from verified company website."""
    if not page_content:
        return []

    fetch_time = retrieved_at or datetime.now(timezone.utc)
    raw_hash = content_hash or hashlib.sha256(page_content.encode("utf-8")).hexdigest()
    facts: list[CompanyFact] = []

    # 1. Discover news / press section URL
    news_url = find_news_url(page_content, page_url)
    if news_url:
        facts.append(
            CompanyFact(
                field_name="press_url",
                value=news_url,
                source_name="Company Press / News",
                source_url=page_url,
                as_of=None,
                retrieved_at=fetch_time,
                confidence="verified_secondary",
                confidence_level=0.95,
                content_hash=raw_hash,
                extraction_method="html_press_link",
            )
        )

    # 2. Extract recent article / announcement headlines if present in news/article tags
    # Check for <article> or <div class="*news*"> or <h3> headline
    headline_match = re.search(
        r'<(?:article|div)[^>]*?(?:class|id)=["\'][^"\']*(?:news|presse|aktuelt|article|post)[^"\']*["\'][^>]*>.*?<h[2-4][^>]*>(.*?)</h[2-4]>',
        page_content,
        re.IGNORECASE | re.DOTALL,
    )
    if headline_match:
        raw_headline = headline_match.group(1)
        clean_headline = re.sub(r"<[^>]+>", " ", raw_headline)
        clean_headline = html.unescape(clean_headline)
        clean_headline = re.sub(r"\s+", " ", clean_headline).strip()
        if len(clean_headline) >= 10:
            facts.append(
                CompanyFact(
                    field_name="latest_news_title",
                    value=clean_headline,
                    source_name="Company Press / News",
                    source_url=page_url,
                    as_of=None,
                    retrieved_at=fetch_time,
                    confidence="verified_secondary",
                    confidence_level=0.85,
                    content_hash=raw_hash,
                    extraction_method="html_article_headline",
                )
            )

    return facts


def extract_registry_public_activity(
    enhet_data: dict[str, Any] | None,
    enhet_url: str,
    retrieved_at: datetime | None = None,
    content_hash: str | None = None,
) -> list[CompanyFact]:
    """Extract official legal lifecycle status and public registry activity from Brreg Enhetsregisteret."""
    if not enhet_data or not isinstance(enhet_data, dict):
        return []

    fetch_time = retrieved_at or datetime.now(timezone.utc)
    raw_hash = content_hash or hashlib.sha256(str(enhet_data).encode("utf-8")).hexdigest()
    facts: list[CompanyFact] = []

    # 1. Legal status (konkurs, under avvikling, aktiv)
    is_konkurs = enhet_data.get("konkurs", False)
    is_under_avvikling = enhet_data.get("underAvvikling", False)
    is_under_tvangsavvikling = enhet_data.get("underTvangsavviklingEllerTvangsopplosning", False)

    if is_konkurs:
        status_val = "Konkurs (Bankrupt)"
    elif is_under_tvangsavvikling:
        status_val = "Under tvangsoppløsning (Compulsory liquidation)"
    elif is_under_avvikling:
        status_val = "Under avvikling (In liquidation)"
    else:
        status_val = "Normal drift (Active)"

    facts.append(
        CompanyFact(
            field_name="legal_status",
            value=status_val,
            source_name="Brønnøysundregistrene – Enhetsregisteret",
            source_url=enhet_url,
            as_of=None,
            retrieved_at=fetch_time,
            confidence="official",
            confidence_level=1.0,
            content_hash=raw_hash,
            extraction_method="official_registry_status",
        )
    )

    # 2. Registration update date (endringsdato)
    reg_update = enhet_data.get("registreringsdatoEnhetsregisteret")
    if reg_update:
        facts.append(
            CompanyFact(
                field_name="registry_registration_date",
                value=str(reg_update),
                source_name="Brønnøysundregistrene – Enhetsregisteret",
                source_url=enhet_url,
                as_of=None,
                retrieved_at=fetch_time,
                confidence="official",
                confidence_level=1.0,
                content_hash=raw_hash,
                extraction_method="official_registry_date",
            )
        )

    # Slettedato (if company has been officially dissolved/deleted)
    slettedato = enhet_data.get("slettedato")
    if slettedato:
        facts.append(
            CompanyFact(
                field_name="dissolution_date",
                value=str(slettedato),
                source_name="Brønnøysundregistrene – Enhetsregisteret",
                source_url=enhet_url,
                as_of=None,
                retrieved_at=fetch_time,
                confidence="official",
                confidence_level=1.0,
                content_hash=raw_hash,
                extraction_method="official_registry_dissolution",
            )
        )

    return facts
