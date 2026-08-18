from __future__ import annotations

import hashlib
import re
import unicodedata
from decimal import Decimal, InvalidOperation

def normalize_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return " ".join("".join(character if character.isalnum() else " " for character in normalized).split())


def decimal_value(value: str) -> tuple[str, str]:
    cleaned = value.strip().lstrip("\ufeff")
    qualifier = ""
    if cleaned.startswith("<"):
        qualifier = "less_than"
        cleaned = cleaned[1:].strip()
    cleaned = cleaned.replace(" ", "").replace(",", ".")
    try:
        number = Decimal(cleaned)
    except InvalidOperation as error:
        raise ValueError(f"not a decimal: {value!r}") from error
    if not number.is_finite() or number < 0:
        raise ValueError(f"not a non-negative finite decimal: {value!r}")
    return format(number, "f"), qualifier


def stable_id(prefix: str, value: str) -> str:
    return f"{prefix}_{hashlib.sha256(value.encode('utf-8')).hexdigest()[:32]}"


def nutrient_unit(name: str, fallback: str = "") -> str:
    match = re.search(r"\(([^()/]+?)/\s*100\s*g\)", name, flags=re.IGNORECASE)
    return match.group(1).strip().lower() if match else fallback.strip().lower()
