from __future__ import annotations

import csv
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .build import NAME_COLUMNS, NUTRIENTS, NUTRITION_COLUMNS, SUPPORTED_LANGUAGES


def validate_catalog(directory: Path) -> dict[str, int]:
    nutrition_rows = read_csv(directory / "nutrition.csv", NUTRITION_COLUMNS)
    name_rows = {
        language: read_csv(directory / f"ingredients_{language}.csv", NAME_COLUMNS)
        for language in SUPPORTED_LANGUAGES
    }
    ingredient_identities = validate_name_identities(name_rows)
    validate_nutrition_rows(nutrition_rows, ingredient_identities)
    result = {"ingredients": len(ingredient_identities), "nutrition": len(nutrition_rows)}
    language_ids: dict[str, set[str]] = {}
    for language in SUPPORTED_LANGUAGES:
        rows = name_rows[language]
        language_ids[language] = validate_name_rows(rows, language, ingredient_identities)
        result[f"names_{language}"] = len(rows)
    missing_english = set(ingredient_identities) - language_ids["en"]
    if missing_english:
        raise ValueError(f"catalog is missing en names for {len(missing_english)} ingredients")
    return result


def read_csv(path: Path, columns: tuple[str, ...]) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)
        if tuple(reader.fieldnames or ()) != columns:
            raise ValueError(f"invalid CSV header in {path.name}")
        return list(reader)


def validate_name_identities(
    rows_by_language: dict[str, list[dict[str, str]]],
) -> dict[str, str]:
    identities: dict[str, str] = {}
    taxonomy_keys: dict[str, str] = {}
    for rows in rows_by_language.values():
        for row in rows:
            ingredient_id = required(row, "ingredient_id")
            taxonomy_key = required(row, "taxonomy_key")
            existing_key = identities.get(ingredient_id)
            if existing_key is not None and existing_key != taxonomy_key:
                raise ValueError(f"ingredient {ingredient_id} has conflicting taxonomy keys")
            existing_id = taxonomy_keys.get(taxonomy_key)
            if existing_id is not None and existing_id != ingredient_id:
                raise ValueError(f"taxonomy key {taxonomy_key} has conflicting ingredient IDs")
            identities[ingredient_id] = taxonomy_key
            taxonomy_keys[taxonomy_key] = ingredient_id
    if not identities:
        raise ValueError("catalog has no ingredients")
    return identities


def validate_nutrition_rows(
    rows: list[dict[str, str]],
    ingredient_identities: dict[str, str],
) -> None:
    ingredient_ids: set[str] = set()
    taxonomy_keys: set[str] = set()
    for row in rows:
        ingredient_id = required(row, "ingredient_id")
        taxonomy_key = required(row, "taxonomy_key")
        if ingredient_id in ingredient_ids:
            raise ValueError(f"duplicate ingredient ID: {ingredient_id}")
        if taxonomy_key in taxonomy_keys:
            raise ValueError(f"duplicate taxonomy key: {taxonomy_key}")
        expected_key = ingredient_identities.get(ingredient_id)
        if expected_key is None:
            raise ValueError(f"nutrition references unknown ingredient: {ingredient_id}")
        if expected_key != taxonomy_key:
            raise ValueError(f"nutrition has wrong taxonomy key for ingredient {ingredient_id}")
        ingredient_ids.add(ingredient_id)
        taxonomy_keys.add(taxonomy_key)
        for field in (
            "nutrition_source",
            "nutrition_source_version",
            "nutrition_source_record_id",
            "nutrition_source_label",
        ):
            required(row, field)
        if row.get("basis_g") != "100":
            raise ValueError(f"ingredient {ingredient_id} must use a 100 g basis")
        if not any(row.get(f"{key}_{unit}") for key, unit in NUTRIENTS):
            raise ValueError(f"ingredient {ingredient_id} has no nutrition values")
        for key, unit in NUTRIENTS:
            value = row.get(f"{key}_{unit}", "")
            if value:
                non_negative_decimal(value)
def validate_name_rows(
    rows: list[dict[str, str]],
    language: str,
    ingredient_identities: dict[str, str],
) -> set[str]:
    seen: set[tuple[str, str]] = set()
    named_ingredients: set[str] = set()
    for row in rows:
        ingredient_id = required(row, "ingredient_id")
        taxonomy_key = required(row, "taxonomy_key")
        name = required(row, "name")
        expected_key = ingredient_identities.get(ingredient_id)
        if expected_key != taxonomy_key:
            raise ValueError(f"{language} name has wrong taxonomy key for ingredient {ingredient_id}")
        identity = ingredient_id, name.casefold()
        if identity in seen:
            raise ValueError(f"duplicate {language} name for ingredient {ingredient_id}: {name}")
        seen.add(identity)
        named_ingredients.add(ingredient_id)
    return named_ingredients


def required(row: dict[str, str], field: str) -> str:
    value = row.get(field, "").strip()
    if not value:
        raise ValueError(f"ingredient has empty {field}")
    return value


def non_negative_decimal(value: str) -> Decimal:
    try:
        number = Decimal(value)
    except InvalidOperation as error:
        raise ValueError(f"invalid decimal: {value}") from error
    if not number.is_finite() or number < 0:
        raise ValueError(f"invalid non-negative decimal: {value}")
    return number
