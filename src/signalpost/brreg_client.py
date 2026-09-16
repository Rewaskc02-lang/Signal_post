"""Asynchronous HTTP client for Brønnøysundregistrene (Brreg) APIs."""

import asyncio
import json
import sys
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from signalpost.config import Settings, settings as default_settings
from signalpost.orgnr import sanitize_orgnr, validate_orgnr


class BrregError(Exception):
    """Base exception for all Brreg client errors."""

    def __init__(self, message: str, status_code: int | None = None, orgnr: str | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.orgnr = orgnr


class InvalidOrgnrError(BrregError, ValueError):
    """Raised when an organisation number fails MOD-11 validation."""


class NotFoundError(BrregError):
    """Raised when an organisation is not found (HTTP 404)."""


class RateLimitedError(BrregError):
    """Raised when requests are rate-limited (HTTP 429)."""


class BrregServerError(BrregError):
    """Raised when Brreg returns a 5xx server error after exhausting retries."""


def log_request_event(
    orgnr: str,
    endpoint: str,
    method: str,
    status_code: int | None,
    latency_ms: float,
    attempt: int,
    error: str | None = None,
) -> None:
    """Log structured JSON-lines output to stdout for budget tracking and observability."""
    event = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "orgnr": orgnr,
        "endpoint": endpoint,
        "method": method,
        "status_code": status_code,
        "latency_ms": round(latency_ms, 2),
        "attempt": attempt,
        "error": error,
    }
    sys.stdout.write(json.dumps(event) + "\n")
    sys.stdout.flush()


class BrregClient:
    """Async client for interacting with Enhetsregisteret and Regnskapsregisteret."""

    def __init__(
        self,
        settings: Settings | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings or default_settings
        self._external_client = client is not None
        self._semaphore = asyncio.Semaphore(self.settings.brreg_max_concurrency)

        if client is not None:
            self._client = client
        else:
            # Shared AsyncClient with connection pool limits
            limits = httpx.Limits(
                max_connections=50,
                max_keepalive_connections=20,
            )
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.settings.brreg_request_timeout_seconds),
                limits=limits,
                headers={"Accept": "application/json", "User-Agent": "Signalpost/0.1.0"},
            )

    async def __aenter__(self) -> "BrregClient":
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()

    async def close(self) -> None:
        """Close the underlying HTTP client if created internally."""
        if not self._external_client and not self._client.is_closed:
            await self._client.aclose()

    async def _request_with_retry(
        self,
        url: str,
        orgnr: str,
        endpoint_name: str,
    ) -> Any:
        """Execute an HTTP GET request with concurrency control, retries, and structured logging."""
        max_retries = self.settings.brreg_max_retries
        backoff_factor = self.settings.brreg_retry_backoff_factor

        for attempt in range(1, max_retries + 1):
            start_time = time.perf_counter()
            status_code: int | None = None
            error_message: str | None = None

            try:
                async with self._semaphore:
                    response = await self._client.get(url)

                latency_ms = (time.perf_counter() - start_time) * 1000.0
                status_code = response.status_code

                if response.status_code == 200:
                    log_request_event(
                        orgnr=orgnr,
                        endpoint=endpoint_name,
                        method="GET",
                        status_code=status_code,
                        latency_ms=latency_ms,
                        attempt=attempt,
                        error=None,
                    )
                    return response.json()

                if response.status_code == 404:
                    error_message = "NotFoundError"
                    log_request_event(
                        orgnr=orgnr,
                        endpoint=endpoint_name,
                        method="GET",
                        status_code=status_code,
                        latency_ms=latency_ms,
                        attempt=attempt,
                        error=error_message,
                    )
                    raise NotFoundError(
                        f"Organisasjonsnummer {orgnr} was not found on endpoint {endpoint_name}",
                        status_code=404,
                        orgnr=orgnr,
                    )

                if response.status_code == 429:
                    error_message = "RateLimitedError"
                    log_request_event(
                        orgnr=orgnr,
                        endpoint=endpoint_name,
                        method="GET",
                        status_code=status_code,
                        latency_ms=latency_ms,
                        attempt=attempt,
                        error=error_message,
                    )
                    raise RateLimitedError(
                        f"Rate limited by Brreg while querying {endpoint_name} for orgnr {orgnr}",
                        status_code=429,
                        orgnr=orgnr,
                    )

                # For 5xx server errors, retry if attempts remain
                if 500 <= response.status_code < 600:
                    error_message = f"HTTP_{response.status_code}"
                    log_request_event(
                        orgnr=orgnr,
                        endpoint=endpoint_name,
                        method="GET",
                        status_code=status_code,
                        latency_ms=latency_ms,
                        attempt=attempt,
                        error=error_message,
                    )
                    if attempt < max_retries:
                        sleep_time = backoff_factor * (2 ** (attempt - 1))
                        await asyncio.sleep(sleep_time)
                        continue
                    raise BrregServerError(
                        f"Brreg server error ({response.status_code}) on {endpoint_name} after {max_retries} attempts",
                        status_code=response.status_code,
                        orgnr=orgnr,
                    )

                # Any other unexpected status codes (e.g. 400, 403)
                error_message = f"HTTP_{response.status_code}"
                log_request_event(
                    orgnr=orgnr,
                    endpoint=endpoint_name,
                    method="GET",
                    status_code=status_code,
                    latency_ms=latency_ms,
                    attempt=attempt,
                    error=error_message,
                )
                raise BrregError(
                    f"Unexpected HTTP {response.status_code} on {endpoint_name} for orgnr {orgnr}: {response.text}",
                    status_code=response.status_code,
                    orgnr=orgnr,
                )

            except (httpx.TransportError, httpx.TimeoutException) as exc:
                latency_ms = (time.perf_counter() - start_time) * 1000.0
                error_message = exc.__class__.__name__
                log_request_event(
                    orgnr=orgnr,
                    endpoint=endpoint_name,
                    method="GET",
                    status_code=status_code,
                    latency_ms=latency_ms,
                    attempt=attempt,
                    error=error_message,
                )
                if attempt < max_retries:
                    sleep_time = backoff_factor * (2 ** (attempt - 1))
                    await asyncio.sleep(sleep_time)
                    continue
                raise BrregError(
                    f"Network error on {endpoint_name} for orgnr {orgnr} after {max_retries} attempts: {exc}",
                    status_code=None,
                    orgnr=orgnr,
                ) from exc

    async def fetch_enhet(self, orgnr: str) -> dict[str, Any]:
        """Fetch core company data from Enhetsregisteret.

        Args:
            orgnr: 9-digit Norwegian organisation number.

        Returns:
            Raw dictionary containing entity details from Brreg.

        Raises:
            InvalidOrgnrError: If orgnr fails checksum validation.
            NotFoundError: If orgnr is not found (404).
            RateLimitedError: If rate-limited (429).
            BrregServerError: If 5xx persists after retries.
            BrregError: On other errors.
        """
        cleaned_orgnr = sanitize_orgnr(orgnr)
        if not validate_orgnr(cleaned_orgnr):
            raise InvalidOrgnrError(f"Invalid organisation number: {orgnr}")

        url = f"{self.settings.brreg_enhet_base_url}/{cleaned_orgnr}"
        result = await self._request_with_retry(
            url=url,
            orgnr=cleaned_orgnr,
            endpoint_name="enhetsregisteret",
        )
        return result

    async def fetch_regnskap(self, orgnr: str) -> list[dict[str, Any]]:
        """Fetch annual financial accounts from Regnskapsregisteret.

        Brreg returns a list of yearly filings. This method returns that list as-is.

        Args:
            orgnr: 9-digit Norwegian organisation number.

        Returns:
            Raw list of financial statements/filings from Brreg.

        Raises:
            InvalidOrgnrError: If orgnr fails checksum validation.
            NotFoundError: If financial statements are not found (404).
            RateLimitedError: If rate-limited (429).
            BrregServerError: If 5xx persists after retries.
            BrregError: On other errors.
        """
        cleaned_orgnr = sanitize_orgnr(orgnr)
        if not validate_orgnr(cleaned_orgnr):
            raise InvalidOrgnrError(f"Invalid organisation number: {orgnr}")

        url = f"{self.settings.brreg_regnskap_base_url}/{cleaned_orgnr}"
        result = await self._request_with_retry(
            url=url,
            orgnr=cleaned_orgnr,
            endpoint_name="regnskapsregisteret",
        )
        # Brreg regnskap returns a list of accounts
        if isinstance(result, list):
            return result
        elif isinstance(result, dict):
            # In case an API wrapper object is returned
            return [result]
        return []
