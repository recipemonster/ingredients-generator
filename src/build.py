from __future__ import annotations

import csv
import os
import re
import tempfile
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from .adapters import NutritionRecord, SourceAdapter
from .ciqual import CIQUALAdapter
from .config import Source, load_nutrient_mappings
from .download import verify_downloads
from .normalize import stable_id
from .taxonomy import (
    TaxonomyIngredient,
    base_ingredients,
    localized_names,
    nutrition_references,
    read_ingredient_taxonomy,
)

ADAPTERS: dict[str, SourceAdapter] = {
    "ciqual": CIQUALAdapter(),
}
NUTRIENTS = (
    ("energy_kcal", "kcal"),
    ("energy_kj", "kj"),
    ("water", "g"),
    ("protein", "g"),
    ("carbohydrate", "g"),
    ("fiber", "g"),
    ("total_fat", "g"),
    ("sodium", "mg"),
    ("calcium", "mg"),
    ("iron", "mg"),
    ("magnesium", "mg"),
    ("phosphorus", "mg"),
    ("potassium", "mg"),
    ("zinc", "mg"),
    ("copper", "mg"),
    ("selenium", "ug"),
    ("vitamin_a_rae", "ug"),
    ("vitamin_e", "mg"),
    ("vitamin_d_d2_d3", "ug"),
    ("vitamin_c", "mg"),
    ("thiamin", "mg"),
    ("riboflavin", "mg"),
    ("niacin", "mg"),
    ("pantothenic_acid", "mg"),
    ("vitamin_b6", "mg"),
    ("folate_total", "ug"),
    ("vitamin_b12", "ug"),
    ("vitamin_k", "ug"),
    ("caffeine", "mg"),
)
SUPPORTED_LANGUAGES = ("en", "pl", "de", "es", "it")
INGREDIENT_ID_PATTERN = re.compile(r"^ingredient_[0-9a-f]{32}$")
NAME_COLUMNS = ("ingredient_id", "taxonomy_key", "name")
NUTRITION_COLUMNS = (
    "ingredient_id",
    "taxonomy_key",
    "nutrition_source",
    "nutrition_source_version",
    "nutrition_source_record_id",
    "nutrition_source_label",
    "basis_g",
    *(f"{key}_{unit}" for key, unit in NUTRIENTS),
)
GENERATED_FILENAMES = (
    "ATTRIBUTIONS.md",
    "LICENCE_DATASET.md",
    "ambiguous_merges.draft.csv",
    "catalog.draft.csv",
    "catalog_assets.draft.csv",
    "catalog_sources.draft.csv",
    "catalog_tables.draft.csv",
    "ingredient_names.draft.csv",
    "ingredient_sources.draft.csv",
    "ingredients.csv",
    "ingredients.draft.csv",
    *(f"ingredients_{language}.csv" for language in SUPPORTED_LANGUAGES),
    *(f"ingredients_{language}.draft.csv" for language in SUPPORTED_LANGUAGES),
    "merge_source_counts.draft.csv",
    "merge_summary.draft.csv",
    "missing-pl.csv",
    "missing_nutrition.draft.csv",
    "nutrient_values.draft.csv",
    "nutrition.csv",
    "nutrition.draft.csv",
    "nutrition_profiles.draft.csv",
    "portions.draft.csv",
    "recipemonster-catalog.zip",
    "source_labels.draft.csv",
)


@dataclass(frozen=True)
class CatalogIngredient:
    ingredient_id: str
    taxonomy_key: str
    names: dict[str, tuple[str, ...]]
    nutrition: dict[str, str] | None


@dataclass(frozen=True)
class BuildResult:
    ingredients: tuple[CatalogIngredient, ...]
    missing_nutrition: tuple[dict[str, str], ...]
    sources: tuple[Source, ...]


def build_catalog(
    sources: tuple[Source, ...],
    raw_directory: Path,
    output_directory: Path,
    nutrient_mappings_path: Path,
    previous_catalog_path: Path | None = None,
) -> dict[str, object]:
    verify_downloads(sources, raw_directory)
    result = prepare_catalog(sources, raw_directory, nutrient_mappings_path, previous_catalog_path)
    clean_output(output_directory)
    write_catalog_files(output_directory, result.ingredients, draft=False)
    write_release_documents(output_directory)
    return manifest(result, release_eligible=True)


def build_catalog_draft(
    sources: tuple[Source, ...],
    raw_directory: Path,
    output_directory: Path,
    nutrient_mappings_path: Path,
    previous_catalog_path: Path | None = None,
) -> dict[str, object]:
    verify_downloads(sources, raw_directory)
    result = prepare_catalog(sources, raw_directory, nutrient_mappings_path, previous_catalog_path)
    clean_output(output_directory)
    write_catalog_files(output_directory, result.ingredients, draft=True)
    write_release_documents(output_directory)
    if result.missing_nutrition:
        write_csv(
            output_directory / "missing_nutrition.draft.csv",
            missing_nutrition_columns(),
            result.missing_nutrition,
        )
    return manifest(result, release_eligible=False)


def prepare_catalog(
    sources: tuple[Source, ...],
    raw_directory: Path,
    nutrient_mappings_path: Path,
    previous_catalog_path: Path | None,
) -> BuildResult:
    identity_sources = [source for source in sources if source.role == "identity"]
    if len(identity_sources) != 1:
        raise ValueError("catalog requires exactly one identity taxonomy")
    identity_source = identity_sources[0]
    taxonomy = read_ingredient_taxonomy(raw_directory / identity_source.assets[0].file)
    available_ingredients = {
        ingredient.key: simplify_source_names(ingredient)
        for ingredient in taxonomy.values()
    }
    apply_ingredient_overrides(available_ingredients, nutrient_mappings_path.parent / "ingredients.csv")
    for language in SUPPORTED_LANGUAGES:
        apply_name_mappings(available_ingredients, nutrient_mappings_path.parent / f"names_{language}.csv", language)
    ingredients = {
        ingredient.key: ingredient
        for ingredient in base_ingredients(available_ingredients)
    }
    ingredient_ids = assign_ingredient_ids(ingredients, load_previous_ingredient_ids(previous_catalog_path))
    records = read_nutrition_sources(sources, raw_directory, nutrient_mappings_path)
    overrides = read_nutrition_links(nutrient_mappings_path.parent / "nutrition-links.csv")
    validate_nutrition_links(overrides, ingredients, records)
    catalog_ingredients: list[CatalogIngredient] = []
    missing_nutrition: list[dict[str, str]] = []
    used_sources = {identity_source.source_id}
    for ingredient in ingredients.values():
        record = resolve_nutrition_record(ingredient, records, overrides)
        if record is None:
            missing_nutrition.append(
                {
                    "taxonomy_key": ingredient.key,
                    "references": "|".join(f"{dataset}:{record_id}" for dataset, record_id in nutrition_references(ingredient)),
                }
            )
        catalog_ingredients.append(catalog_ingredient(ingredient, record, ingredient_ids[ingredient.key]))
        if record is not None:
            used_sources.add(record.dataset)
    selected_sources = tuple(source for source in sources if source.source_id in used_sources)
    return BuildResult(
        ingredients=tuple(sorted(catalog_ingredients, key=lambda item: (item.taxonomy_key, item.ingredient_id))),
        missing_nutrition=tuple(sorted(missing_nutrition, key=lambda row: row["taxonomy_key"])),
        sources=selected_sources,
    )


def simplify_source_names(ingredient: TaxonomyIngredient) -> TaxonomyIngredient:
    names: dict[str, tuple[str, ...]] = {}
    for language in ingredient.names:
        values = localized_names(ingredient, language)
        if values:
            names[language] = (values[0],)
    return TaxonomyIngredient(
        key=ingredient.key,
        names=names,
        parents=ingredient.parents,
        properties=ingredient.properties,
    )


def read_nutrition_sources(
    sources: tuple[Source, ...],
    raw_directory: Path,
    nutrient_mappings_path: Path,
) -> dict[tuple[str, str], NutritionRecord]:
    mappings = load_nutrient_mappings(nutrient_mappings_path)
    records: dict[tuple[str, str], NutritionRecord] = {}
    for source in sources:
        if source.role != "nutrition":
            continue
        adapter = ADAPTERS.get(source.source_id)
        if adapter is None:
            raise ValueError(f"no nutrition adapter for source {source.source_id}")
        source_records = adapter.read(source, raw_directory, mappings)
        for record in source_records:
            records[(record.dataset, record.record_id)] = record
    return records


def resolve_nutrition_record(
    ingredient,
    records: dict[tuple[str, str], NutritionRecord],
    overrides: dict[str, tuple[str, str, str]],
) -> NutritionRecord | None:
    override = overrides.get(ingredient.key)
    if override is not None:
        dataset, id_type, source_id = override
        return records.get((dataset, source_id))
    for dataset, source_id in nutrition_references(ingredient):
        record = records.get((dataset, source_id))
        if record is not None:
            return record
    return None


def read_nutrition_links(path: Path) -> dict[str, tuple[str, str, str]]:
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)
        expected = ("taxonomy_key", "dataset", "id_type", "record_id")
        if tuple(reader.fieldnames or ()) != expected:
            raise ValueError("nutrition-links.csv has an invalid header")
        links: dict[str, tuple[str, str, str]] = {}
        for row in reader:
            key = row["taxonomy_key"].strip()
            dataset = row["dataset"].strip()
            id_type = row["id_type"].strip()
            record_id = row["record_id"].strip()
            if not key or not dataset or not record_id or id_type != "record":
                raise ValueError("nutrition-links.csv has an invalid row")
            if key in links:
                raise ValueError(f"duplicate nutrition link: {key}")
            links[key] = dataset, id_type, record_id
        return links


def validate_nutrition_links(
    links: dict[str, tuple[str, str, str]],
    ingredients: dict[str, TaxonomyIngredient],
    records: dict[tuple[str, str], NutritionRecord],
) -> None:
    for key, (dataset, _, record_id) in links.items():
        if key not in ingredients:
            raise ValueError(f"nutrition link references unknown ingredient: {key}")
        if (dataset, record_id) not in records:
            raise ValueError(f"nutrition link references unknown record: {dataset}/{record_id}")


def apply_ingredient_overrides(ingredients: dict[str, TaxonomyIngredient], path: Path) -> None:
    if not path.is_file():
        return
    columns = (
        "action",
        "taxonomy_key",
        "dataset",
        "id_type",
        "record_id",
    )
    with path.open(encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)
        if tuple(reader.fieldnames or ()) != columns:
            raise ValueError("mappings/ingredients.csv has an invalid header")
        seen: set[str] = set()
        for row in reader:
            action = row["action"].strip()
            key = row["taxonomy_key"].strip()
            if action == "exclude":
                if not key or key in seen:
                    raise ValueError("mappings/ingredients.csv has an invalid exclusion")
                seen.add(key)
                ingredients.pop(key, None)
                continue
            if action != "upsert":
                raise ValueError(f"unsupported ingredient mapping action: {action}")
            dataset = row["dataset"].strip()
            id_type = row["id_type"].strip()
            record_id = row["record_id"].strip()
            existing = ingredients.get(key)
            if not key:
                raise ValueError("mappings/ingredients.csv has an incomplete row")
            if key in seen:
                raise ValueError(f"duplicate ingredient override: {key}")
            seen.add(key)
            properties = dict(existing.properties) if existing is not None else {}
            parents = existing.parents if existing is not None else ()
            if dataset or id_type or record_id:
                if not dataset or not id_type or not record_id:
                    raise ValueError("mappings/ingredients.csv has an incomplete nutrition identity")
                properties = {nutrition_property(dataset, id_type): record_id}
            ingredients[key] = TaxonomyIngredient(
                key=key,
                names=dict(existing.names) if existing is not None else {},
                parents=parents,
                properties=properties,
            )


def load_previous_ingredient_ids(path: Path | None) -> dict[str, str]:
    if path is None:
        return {}
    with path.open(encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)
        columns = tuple(reader.fieldnames or ())
        if columns not in {NAME_COLUMNS, NUTRITION_COLUMNS}:
            raise ValueError("previous ingredient catalog has an invalid header")
        ingredient_ids: dict[str, str] = {}
        used_ids: dict[str, str] = {}
        for row in reader:
            key = row["taxonomy_key"].strip()
            ingredient_id = row["ingredient_id"].strip()
            if not key or INGREDIENT_ID_PATTERN.fullmatch(ingredient_id) is None:
                raise ValueError("previous ingredient catalog has an invalid ingredient identity")
            existing_id = ingredient_ids.get(key)
            if existing_id is not None and existing_id != ingredient_id:
                raise ValueError(f"previous ingredient catalog contains conflicting taxonomy key: {key}")
            existing_key = used_ids.get(ingredient_id)
            if existing_key is not None and existing_key != key:
                raise ValueError(f"previous ingredient ID {ingredient_id} is used by {existing_key} and {key}")
            ingredient_ids[key] = ingredient_id
            used_ids[ingredient_id] = key
        return ingredient_ids


def assign_ingredient_ids(
    ingredients: dict[str, TaxonomyIngredient],
    previous_ids: dict[str, str],
) -> dict[str, str]:
    missing = sorted(set(previous_ids) - set(ingredients))
    if missing:
        preview = ", ".join(missing[:10])
        if len(missing) > 10:
            preview += f", and {len(missing) - 10} more"
        raise ValueError(f"previously released ingredients disappeared: {preview}")
    assigned = {
        key: previous_ids.get(key, stable_id("ingredient", key))
        for key in ingredients
    }
    if len(set(assigned.values())) != len(assigned):
        raise ValueError("ingredient ID collision")
    return assigned


def nutrition_property(dataset: str, id_type: str) -> str:
    if dataset == "ciqual" and id_type == "record":
        return "ciqual_food_code"
    raise ValueError(f"unsupported supplemental nutrition identity: {dataset}/{id_type}")


def apply_name_mappings(
    ingredients: dict[str, TaxonomyIngredient],
    path: Path,
    language: str,
) -> None:
    if not path.is_file():
        return
    columns = ("taxonomy_key", "names")
    with path.open(encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)
        if tuple(reader.fieldnames or ()) != columns:
            raise ValueError(f"mappings/names_{language}.csv has an invalid header")
        seen: set[str] = set()
        for row in reader:
            key = row["taxonomy_key"].strip()
            names = split_names(row["names"])
            if not key or not names or key in seen:
                raise ValueError(f"mappings/names_{language}.csv has an invalid row")
            ingredient = ingredients.get(key)
            if ingredient is None:
                raise ValueError(f"{language} names reference unknown ingredient: {key}")
            seen.add(key)
            localized = dict(ingredient.names)
            localized[language] = names
            ingredients[key] = TaxonomyIngredient(
                key=ingredient.key,
                names=localized,
                parents=ingredient.parents,
                properties=ingredient.properties,
            )


def split_names(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in value.split("|") if part.strip())


def catalog_ingredient(
    ingredient: TaxonomyIngredient,
    record: NutritionRecord | None,
    ingredient_id: str,
) -> CatalogIngredient:
    names: dict[str, tuple[str, ...]] = {}
    for language in SUPPORTED_LANGUAGES:
        values = localized_names(ingredient, language)
        if values:
            names[language] = values
    if not names.get("en"):
        raise ValueError(f"ingredient {ingredient.key} has no English name")
    nutrition = nutrition_row(ingredient, record, ingredient_id) if record is not None else None
    return CatalogIngredient(
        ingredient_id=ingredient_id,
        taxonomy_key=ingredient.key,
        names=names,
        nutrition=nutrition,
    )


def nutrition_row(
    ingredient: TaxonomyIngredient,
    record: NutritionRecord,
    ingredient_id: str,
) -> dict[str, str]:
    nutrition = {column: "" for column in NUTRITION_COLUMNS}
    nutrition.update(
        {
            "ingredient_id": ingredient_id,
            "taxonomy_key": ingredient.key,
            "nutrition_source": record.dataset,
            "nutrition_source_version": record.version,
            "nutrition_source_record_id": record.record_id,
            "nutrition_source_label": record.label,
            "basis_g": "100",
        }
    )
    values = {value.key: value for value in record.values if not value.qualifier}
    for key, unit in NUTRIENTS:
        value = values.get(key)
        if value is not None:
            nutrition[f"{key}_{unit}"] = convert_unit(value.amount, value.unit, unit)
    return nutrition


def convert_unit(amount: str, source_unit: str, target_unit: str) -> str:
    normalized = source_unit.casefold().replace("µ", "u").replace("μ", "u")
    target = target_unit.casefold()
    if normalized == target:
        return amount
    factors = {
        ("g", "mg"): Decimal("1000"),
        ("g", "ug"): Decimal("1000000"),
        ("mg", "g"): Decimal("0.001"),
        ("mg", "ug"): Decimal("1000"),
        ("ug", "g"): Decimal("0.000001"),
        ("ug", "mg"): Decimal("0.001"),
    }
    factor = factors.get((normalized, target))
    if factor is None:
        raise ValueError(f"cannot convert nutrient unit {source_unit} to {target_unit}")
    converted = Decimal(amount) * factor
    return format(converted.normalize(), "f")


def missing_nutrition_columns() -> tuple[str, ...]:
    return "taxonomy_key", "references"


def clean_output(output_directory: Path) -> None:
    output_directory.mkdir(parents=True, exist_ok=True)
    for filename in GENERATED_FILENAMES:
        (output_directory / filename).unlink(missing_ok=True)


def write_csv(path: Path, columns: tuple[str, ...], rows) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=columns, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
        os.replace(temporary_name, path)
        path.chmod(0o644)
    finally:
        Path(temporary_name).unlink(missing_ok=True)


def write_release_documents(output_directory: Path) -> None:
    for filename in ("ATTRIBUTIONS.md", "LICENCE_DATASET.md"):
        source = output_directory.parent / filename
        if not source.is_file():
            raise ValueError(f"{filename} is missing")
        destination = output_directory / filename
        destination.write_bytes(source.read_bytes())
        destination.chmod(0o644)


def write_catalog_files(
    output_directory: Path,
    ingredients: tuple[CatalogIngredient, ...],
    draft: bool,
) -> None:
    suffix = ".draft.csv" if draft else ".csv"
    write_csv(
        output_directory / f"nutrition{suffix}",
        NUTRITION_COLUMNS,
        (ingredient.nutrition for ingredient in ingredients if ingredient.nutrition is not None),
    )
    for language in SUPPORTED_LANGUAGES:
        rows = (
            {
                "ingredient_id": ingredient.ingredient_id,
                "taxonomy_key": ingredient.taxonomy_key,
                "name": name,
            }
            for ingredient in ingredients
            for name in ingredient.names.get(language, ())
        )
        write_csv(output_directory / f"ingredients_{language}{suffix}", NAME_COLUMNS, rows)


def manifest(result: BuildResult, release_eligible: bool) -> dict[str, object]:
    return {
        "schemaVersion": "recipemonster.ingredient-catalog/v5",
        "releaseEligible": release_eligible,
        "ingredientCount": len(result.ingredients),
        "nutritionCount": sum(ingredient.nutrition is not None for ingredient in result.ingredients),
        "nameCounts": {
            language: sum(len(ingredient.names.get(language, ())) for ingredient in result.ingredients)
            for language in SUPPORTED_LANGUAGES
        },
        "missingNutritionCount": len(result.missing_nutrition),
        "sources": [source.source_id for source in result.sources],
    }
