"""Unit tests for sampling module and active entity filtration."""

import gzip
import json
import pytest
import respx

from signalpost.sampling import (
    extract_active_orgnrs_from_items,
    is_active_entity,
    sample_active_orgnrs,
)


def test_is_active_entity() -> None:
    # 1. Normal active company
    active = {
        "organisasjonsnummer": "923609016",
        "navn": "ACTIVE AS",
        "slettedato": None,
        "konkurs": False,
        "underAvvikling": False,
        "underTvangsavviklingEllerTvangsopplosning": False,
    }
    assert is_active_entity(active) is True

    # 2. Deleted / Struck off
    deleted = {**active, "slettedato": "2023-05-12"}
    assert is_active_entity(deleted) is False

    # 3. Bankrupt
    bankrupt = {**active, "konkurs": True}
    assert is_active_entity(bankrupt) is False

    # 4. Under voluntary liquidation
    in_liquidation = {**active, "underAvvikling": True}
    assert is_active_entity(in_liquidation) is False

    # 5. Under forced liquidation
    forced_liquidation = {**active, "underTvangsavviklingEllerTvangsopplosning": True}
    assert is_active_entity(forced_liquidation) is False


def test_extract_active_orgnrs_from_items() -> None:
    items = [
        # Valid active
        {"organisasjonsnummer": "923609016", "navn": "Equinor", "konkurs": False},
        # Valid active
        {"organisasjonsnummer": "984851006", "navn": "DNB", "konkurs": False},
        # Invalid checksum
        {"organisasjonsnummer": "923609017", "navn": "Bad Checksum", "konkurs": False},
        # Valid orgnr but bankrupt
        {"organisasjonsnummer": "998848344", "navn": "Telenor Bankrupt", "konkurs": True},
        # Valid orgnr but deleted
        {"organisasjonsnummer": "910747711", "navn": "Orkla Deleted", "slettedato": "2022-01-01"},
        # Malformed item
        "not-a-dict",
    ]

    extracted = list(extract_active_orgnrs_from_items(iter(items)))  # type: ignore[arg-type]
    assert extracted == ["923609016", "984851006"]


@pytest.mark.asyncio
async def test_sample_active_orgnrs_plain_json() -> None:
    mock_data = [
        {"organisasjonsnummer": "923609016", "navn": "Company 1", "konkurs": False},
        {"organisasjonsnummer": "984851006", "navn": "Company 2", "konkurs": False},
        {"organisasjonsnummer": "998848344", "navn": "Company 3", "konkurs": False},
        {"organisasjonsnummer": "910747711", "navn": "Company 4", "konkurs": False},
        {"organisasjonsnummer": "984661185", "navn": "Company 5", "konkurs": False},
        # Bankrupt - should be excluded
        {"organisasjonsnummer": "100000040", "navn": "Bankrupt Co", "konkurs": True},
    ]

    mock_url = "https://mock.brreg.no/bulk/enheter"
    with respx.mock() as respx_mock:
        respx_mock.get(mock_url).respond(status_code=200, json=mock_data)

        sampled = await sample_active_orgnrs(count=3, source_url=mock_url, seed=42)

    assert len(sampled) == 3
    assert all(orgnr in ["923609016", "984851006", "998848344", "910747711", "984661185"] for orgnr in sampled)
    assert "100000040" not in sampled


@pytest.mark.asyncio
async def test_sample_active_orgnrs_gzipped() -> None:
    mock_data = [
        {"organisasjonsnummer": "923609016", "navn": "Company 1", "konkurs": False},
        {"organisasjonsnummer": "984851006", "navn": "Company 2", "konkurs": False},
    ]
    json_bytes = json.dumps(mock_data).encode("utf-8")
    gz_bytes = gzip.compress(json_bytes)

    mock_url = "https://mock.brreg.no/bulk/enheter.gz"
    with respx.mock() as respx_mock:
        respx_mock.get(mock_url).respond(
            status_code=200,
            content=gz_bytes,
            headers={"Content-Type": "application/gzip"},
        )

        sampled = await sample_active_orgnrs(count=2, source_url=mock_url, seed=42)

    assert len(sampled) == 2
    assert set(sampled) == {"923609016", "984851006"}
