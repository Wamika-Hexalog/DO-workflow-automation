"""ISO 6346 container numbers: a 3-letter owner code, a category letter
(U, J or Z), a 6-digit serial number and a check digit."""
from __future__ import annotations

import re

_FORMAT = re.compile(r"^[A-Z]{3}[UJZ][0-9]{7}$")
_SEPARATORS = re.compile(r"[\n,;]+")


def _letter_values() -> dict[str, int]:
    values: dict[str, int] = {}
    value = 10
    for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        if value % 11 == 0:  # the standard skips multiples of 11
            value += 1
        values[letter] = value
        value += 1
    return values


_LETTER_VALUES = _letter_values()


def check_digit(first_ten: str) -> int:
    total = sum(
        (int(ch) if ch.isdigit() else _LETTER_VALUES[ch]) * 2**position
        for position, ch in enumerate(first_ten)
    )
    return total % 11 % 10


def with_check_digit(prefix: str, serial: str) -> str:
    base = prefix + serial
    return base + str(check_digit(base))


EXAMPLE = with_check_digit("MSCU", "482913")


def split(value: object) -> list[str]:
    """Container numbers from free text: one per line, or separated by commas."""
    if not value:
        return []
    return [re.sub(r"\s+", "", part).upper() for part in _SEPARATORS.split(str(value)) if part.strip()]


def problem(code: str) -> str | None:
    if not _FORMAT.match(code):
        return f"{code}: use 4 letters and 7 digits, like {EXAMPLE}."
    if check_digit(code[:10]) != int(code[10]):
        return f"{code}: check digit does not match. Compare it with the BL."
    return None


def problems(value: object) -> list[str]:
    return [message for code in split(value) if (message := problem(code))]
