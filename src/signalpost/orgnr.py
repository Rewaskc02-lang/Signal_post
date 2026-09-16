"""Norwegian Organisasjonsnummer (orgnr) validation using MOD-11 algorithm.

Norwegian organisation numbers are 9-digit identifiers where the 9th digit
is a control digit calculated using the Modulus 11 algorithm with weights:
    [3, 2, 7, 6, 5, 4, 3, 2]
"""

from typing import Final

# Modulus 11 weights for the first 8 digits of a Norwegian orgnr
MOD11_WEIGHTS: Final[list[int]] = [3, 2, 7, 6, 5, 4, 3, 2]


def sanitize_orgnr(orgnr: str) -> str:
    """Strip common whitespace and formatting from an org number string.

    Args:
        orgnr: The raw org number string (e.g. "923 609 016").

    Returns:
        The stripped string without whitespace.
    """
    if not isinstance(orgnr, str):
        return ""
    return orgnr.strip().replace(" ", "")


def validate_orgnr(s: str) -> bool:
    """Validate a Norwegian organisation number using the Modulus 11 algorithm.

    Rules:
    1. Must be a 9-digit numeric string.
    2. Weighted sum of first 8 digits with weights [3, 2, 7, 6, 5, 4, 3, 2].
    3. Remainder = sum % 11.
    4. If remainder == 0 -> control digit must be 0.
    5. If remainder == 1 -> invalid (control digit would be 10, which cannot be a single digit).
    6. If remainder > 1 -> control digit must be (11 - remainder).
    7. 9th digit must match the calculated control digit.

    Args:
        s: The candidate organisation number string.

    Returns:
        True if valid, False otherwise.
    """
    if not isinstance(s, str):
        return False

    cleaned = sanitize_orgnr(s)

    # Must be exactly 9 digits
    if len(cleaned) != 9 or not cleaned.isdigit():
        return False

    digits = [int(c) for c in cleaned]

    # Calculate weighted sum of first 8 digits
    weighted_sum = sum(d * w for d, w in zip(digits[:8], MOD11_WEIGHTS))
    remainder = weighted_sum % 11

    if remainder == 0:
        expected_control_digit = 0
    elif remainder == 1:
        # Remainder of 1 results in 10, which cannot be represented as a single digit.
        # Such org numbers are never issued by Brreg and are mathematically invalid.
        return False
    else:
        expected_control_digit = 11 - remainder

    return digits[8] == expected_control_digit
