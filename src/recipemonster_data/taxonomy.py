from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .normalize import normalize_name

PREPARATION_WORDS = {
    "baked",
    "boiled",
    "braised",
    "cooked",
    "fried",
    "grilled",
    "pasteurised",
    "raw",
    "refined",
    "roasted",
    "steamed",
    "stewed",
    "uht",
}
PREPARATION_PHRASES = {
    "from concentrate",
    "husked and grilled",
    "low fat",
}


@dataclass(frozen=True)
class TaxonomyIngredient:
    key: str
    names: dict[str, tuple[str, ...]]
    parents: tuple[str, ...]
    properties: dict[str, str]


def read_ingredient_taxonomy(path: Path) -> dict[str, TaxonomyIngredient]:
    ingredients: dict[str, TaxonomyIngredient] = {}
    for block in path.read_text(encoding="utf-8").split("\n\n"):
        ingredient = parse_ingredient_block(block)
        if ingredient is None:
            continue
        if ingredient.key in ingredients:
            ingredients[ingredient.key] = merge_taxonomy_ingredients(ingredients[ingredient.key], ingredient)
        else:
            ingredients[ingredient.key] = ingredient
    return ingredients


def merge_taxonomy_ingredients(
    left: TaxonomyIngredient,
    right: TaxonomyIngredient,
) -> TaxonomyIngredient:
    languages = set(left.names) | set(right.names)
    names = {
        language: tuple(dict.fromkeys((*left.names.get(language, ()), *right.names.get(language, ()))))
        for language in languages
    }
    properties = dict(right.properties)
    properties.update(left.properties)
    return TaxonomyIngredient(
        key=left.key,
        names=names,
        parents=tuple(dict.fromkeys((*left.parents, *right.parents))),
        properties=properties,
    )


def parse_ingredient_block(block: str) -> TaxonomyIngredient | None:
    names: dict[str, tuple[str, ...]] = {}
    parents: list[str] = []
    properties: dict[str, str] = {}
    for raw_line in block.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("synonyms:") or line.startswith("stopwords:"):
            continue
        if line.startswith("< "):
            language, separator, value = line[2:].partition(":")
            if separator and language == "en" and value.strip():
                parents.append(normalize_taxonomy_key(value))
            continue
        field, separator, value = line.partition(":")
        if not separator or not value.strip():
            continue
        if len(field) in {2, 3} and field.isalpha():
            names[field] = tuple(split_taxonomy_names(value))
            continue
        language, language_separator, property_value = value.partition(":")
        if language_separator and language == "en" and property_value.strip():
            properties[field] = property_value.strip()
    english = names.get("en", ())
    if not english:
        return None
    return TaxonomyIngredient(
        key=normalize_taxonomy_key(english[0]),
        names=names,
        parents=tuple(dict.fromkeys(parents)),
        properties=properties,
    )


def split_taxonomy_names(value: str) -> list[str]:
    names: list[str] = []
    current: list[str] = []
    escaped = False
    for character in value.strip():
        if escaped:
            current.append(character)
            escaped = False
        elif character == "\\":
            escaped = True
        elif character == ",":
            name = "".join(current).strip()
            if name:
                names.append(name)
            current = []
        else:
            current.append(character)
    name = "".join(current).strip()
    if name:
        names.append(name)
    return names


def normalize_taxonomy_key(value: str) -> str:
    return normalize_name(value.removeprefix("en:").strip())


def base_ingredients(
    ingredients: dict[str, TaxonomyIngredient],
) -> list[TaxonomyIngredient]:
    candidates = set(ingredients)
    selected: list[TaxonomyIngredient] = []
    for key in sorted(candidates):
        if is_preparation_variant(key, ingredients, candidates):
            continue
        selected.append(ingredients[key])
    return selected


def is_preparation_variant(
    key: str,
    ingredients: dict[str, TaxonomyIngredient],
    mapped: set[str],
) -> bool:
    if not any(ancestor in mapped for ancestor in ingredient_ancestors(key, ingredients)):
        return False
    words = set(key.split())
    return bool(words & PREPARATION_WORDS) or any(phrase in key for phrase in PREPARATION_PHRASES)


def ingredient_ancestors(
    key: str,
    ingredients: dict[str, TaxonomyIngredient],
) -> set[str]:
    ancestors: set[str] = set()
    pending = list(ingredients[key].parents)
    while pending:
        parent = pending.pop()
        if parent in ancestors:
            continue
        ancestors.add(parent)
        if parent in ingredients:
            pending.extend(ingredients[parent].parents)
    return ancestors


def nutrition_references(ingredient: TaxonomyIngredient) -> tuple[tuple[str, str], ...]:
    references: list[tuple[str, str]] = []
    for property_name, dataset in (
        ("ciqual_food_code", "ciqual"),
        ("ciqual_proxy_food_code", "ciqual"),
    ):
        value = ingredient.properties.get(property_name, "").strip()
        if value:
            references.append((dataset, value))
    return tuple(dict.fromkeys(references))


def localized_names(ingredient: TaxonomyIngredient, language: str) -> tuple[str, ...]:
    values: list[str] = []
    normalized: set[str] = set()
    word_signatures: set[tuple[str, ...]] = set()
    for value in ingredient.names.get(language, ()):
        name = " ".join(value.replace("\u00a0", " ").split()).strip(" ,")
        if not name:
            continue
        if name[0].isupper() and not name.isupper():
            name = name[0].lower() + name[1:]
        key = normalize_name(name)
        signature = tuple(sorted(key.split()))
        if key and key not in normalized and signature not in word_signatures:
            values.append(name)
            normalized.add(key)
            word_signatures.add(signature)
    return tuple(values)
