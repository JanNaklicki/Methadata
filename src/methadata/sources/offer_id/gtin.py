"""Walidacja GTIN (GS1): długości 8/12/13/14 i cyfra kontrolna; postać kanoniczna GTIN-14."""
from __future__ import annotations

import re
from typing import Optional

_LENGTHS = (8, 12, 13, 14)


def check_digit_ok(digits: str) -> bool:
    if not digits.isdigit() or len(digits) not in _LENGTHS:
        return False
    body, chk = digits[:-1], int(digits[-1])
    total = sum(int(d) * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(body)))
    return (10 - total % 10) % 10 == chk


def normalize_gtin(raw: str) -> tuple[Optional[str], bool]:
    """(GTIN-14 lub None gdy nie wygląda na GTIN, czy cyfra kontrolna poprawna)."""
    digits = re.sub(r"[\s\-]", "", raw or "")
    if not digits.isdigit() or len(digits) not in _LENGTHS:
        return None, False
    return digits.zfill(14), check_digit_ok(digits)
