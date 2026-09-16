"""Unit tests for Norwegian organisation number MOD-11 checksum validation."""

import pytest
from signalpost.orgnr import sanitize_orgnr, validate_orgnr


@pytest.mark.parametrize(
    "orgnr,expected",
    [
        # 1. Real valid Norwegian organisation numbers
        ("923609016", True),  # Equinor ASA
        ("984851006", True),  # DNB Bank ASA
        ("986228608", True),  # Yara International ASA
        ("910747711", True),  # Orkla ASA
        ("984661185", True),  # Posten Bring AS
        # 2. Valid orgnr with whitespace
        (" 923609016 ", True),
        ("923 609 016", True),
        # 3. Valid orgnr with remainder == 0 (check digit is 0)
        ("100000040", True),  # 1*3 + 4*2 = 11, 11 % 11 = 0 -> control digit 0
        # 4. Valid orgnr with leading zero
        ("012345674", True),  # Weighted sum 106, 106 % 11 = 7 -> 11 - 7 = 4
        # 5. Invalid checksum (wrong control digit)
        ("923609017", False),
        ("984851007", False),
        ("998848340", False),
        # 6. Remainder == 1 case (mathematically impossible control digit 10)
        ("400000000", False),  # 4*3 = 12, 12 % 11 = 1 -> invalid
        ("400000001", False),
        ("400000009", False),
        # 7. Wrong length (too short / too long)
        ("92360901", False),  # 8 digits
        ("12345678", False),  # 8 digits
        ("12345", False),  # 5 digits
        ("9236090160", False),  # 10 digits
        ("12345678901", False),  # 11 digits
        # 8. Non-digit characters
        ("92360901A", False),
        ("NO923609016MVA", False),
        ("abcdefghi", False),
        ("923-609-016", False),
        # 9. Empty and whitespace-only strings
        ("", False),
        ("   ", False),
        # 10. Non-string inputs
        (None, False),
        (923609016, False),  # type: ignore[arg-type]
        ([9, 2, 3, 6, 0, 9, 0, 1, 6], False),  # type: ignore[arg-type]
    ],
)
def test_validate_orgnr(orgnr: str, expected: bool) -> None:
    assert validate_orgnr(orgnr) is expected


def test_sanitize_orgnr() -> None:
    assert sanitize_orgnr(" 923 609 016 ") == "923609016"
    assert sanitize_orgnr("923609016") == "923609016"
    assert sanitize_orgnr("") == ""
    assert sanitize_orgnr(None) == ""  # type: ignore[arg-type]
