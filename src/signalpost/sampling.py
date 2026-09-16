"""Sampling module for selecting random valid and active Norwegian organisation numbers.

Retrieves or streams the bulk export from Brønnøysundregistrene (Enhetsregisteret)
and applies filtration to extract active, legally sound entities.
"""

import gzip
import io
import json
import random
from typing import Any, AsyncIterator, BinaryIO, Iterator
import httpx

from signalpost.config import Settings, settings as default_settings
from signalpost.orgnr import validate_orgnr


def is_active_entity(entity: dict[str, Any]) -> bool:
    """Determine whether an entity from Enhetsregisteret is active.

    ---------------------------------------------------------------------------
    ACTIVE ENTITY FILTER
    Filters out struck-off, bankrupt, and liquidating entities.
    NOTE: Later phases may want inactive/dissolved entities for historical
    or archival analysis. Keep this filter isolated and configurable.
    ---------------------------------------------------------------------------
    """
    # 1. Struck-off / deleted check: slettedato must be None or absent
    if entity.get("slettedato") is not None:
        return False

    # 2. Bankruptcy check: konkurs must be False
    if entity.get("konkurs", False) is True:
        return False

    # 3. Voluntary liquidation check: underAvvikling must be False
    if entity.get("underAvvikling", False) is True:
        return False

    # 4. Involuntary dissolution check: underTvangsavviklingEllerTvangsopplosning must be False
    if entity.get("underTvangsavviklingEllerTvangsopplosning", False) is True:
        return False

    return True


def extract_active_orgnrs_from_items(
    entities: Iterator[dict[str, Any]],
) -> Iterator[str]:
    """Yield valid org numbers for all entities passing active filtration."""
    for item in entities:
        if not isinstance(item, dict):
            continue

        orgnr = item.get("organisasjonsnummer")
        if not orgnr or not isinstance(orgnr, str):
            continue

        # Enforce MOD-11 checksum validation
        if not validate_orgnr(orgnr):
            continue

        # Enforce active status filter
        if is_active_entity(item):
            yield orgnr


async def sample_active_orgnrs(
    count: int = 5,
    source_url: str | None = None,
    client: httpx.AsyncClient | None = None,
    settings: Settings | None = None,
    seed: int | None = None,
) -> list[str]:
    """Download the bulk export from Enhetsregisteret and return random active org numbers.

    Uses reservoir sampling so memory usage remains bounded regardless of export size.

    Args:
        count: The number of active organisation numbers to sample.
        source_url: URL for the bulk download (defaults to Enhetsregisteret lastned endpoint).
        client: Optional existing httpx.AsyncClient.
        settings: Optional custom Settings.
        seed: Optional random seed for reproducible sampling.

    Returns:
        A list of `count` valid, active organisation numbers.
    """
    if count <= 0:
        return []

    cfg = settings or default_settings
    url = source_url or cfg.brreg_bulk_download_url
    rng = random.Random(seed)

    headers = {
        "Accept": "application/vnd.brreg.enhetsregisteret.enhet.v2+gzip, application/json",
        "User-Agent": "Signalpost/0.1.0",
    }

    should_close_client = False
    http_client = client
    if http_client is None:
        http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(60.0),
            headers=headers,
        )
        should_close_client = True

    try:
        response = await http_client.get(url)
        response.raise_for_status()

        content = response.content
        # Detect and decompress gzip if needed
        if content.startswith(b"\x1f\x8b"):
            decompressed = gzip.decompress(content)
            data = json.loads(decompressed.decode("utf-8"))
        else:
            data = response.json()

        if isinstance(data, list):
            items_iter = iter(data)
        elif isinstance(data, dict) and "enheter" in data:
            items_iter = iter(data["enheter"])
        else:
            items_iter = iter([data] if isinstance(data, dict) else [])

        # Reservoir sampling (Algorithm R)
        reservoir: list[str] = []
        for i, orgnr in enumerate(extract_active_orgnrs_from_items(items_iter)):
            if len(reservoir) < count:
                reservoir.append(orgnr)
            else:
                j = rng.randint(0, i)
                if j < count:
                    reservoir[j] = orgnr

        return reservoir

    finally:
        if should_close_client and not http_client.is_closed:
            await http_client.aclose()
