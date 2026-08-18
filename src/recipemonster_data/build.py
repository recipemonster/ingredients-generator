from __future__ import annotations

import csv
import os
import tempfile
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from .adapters import NutritionRecord, SourceAdapter
from .ciqual import CIQUALAdapter
from .config import Source, load_nutrient_mappings
from .download import verify_downloads
from .fineli import FineliAdapter
from .normalize import stable_id
from .taxonomy import (
    TaxonomyIngredient,
    localized_names,
    mapped_base_ingredients,
    nutrition_references,
    read_ingredient_taxonomy,
)
from .usda import USDAAdapter, read_ndb_to_fdc

ADAPTERS: dict[str, SourceAdapter] = {
    "ciqual": CIQUALAdapter(),
    "fineli": FineliAdapter(),
    "usda-sr-legacy": USDAAdapter(),
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
NAME_COLUMNS = ("ingredient_id", "name")
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
    "ATTRIBUTIONS.txt",
    "DATA_LICENSE.md",
    "SOURCES.md",
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
    "nutrient_values.draft.csv",
    "nutrition.csv",
    "nutrition.draft.csv",
    "nutrition_profiles.draft.csv",
    "portions.draft.csv",
    "recipemonster-catalog.zip",
    "source_labels.draft.csv",
    "unresolved.draft.csv",
)


@dataclass(frozen=True)
class CatalogIngredient:
    ingredient_id: str
    taxonomy_key: str
    names: dict[str, tuple[str, ...]]
    nutrition: dict[str, str]


@dataclass(frozen=True)
class BuildResult:
    ingredients: tuple[CatalogIngredient, ...]
    unresolved: tuple[dict[str, str], ...]
    sources: tuple[Source, ...]


def build_catalog(
    sources: tuple[Source, ...],
    raw_directory: Path,
    output_directory: Path,
    nutrient_mappings_path: Path,
) -> dict[str, object]:
    verify_downloads(sources, raw_directory)
    result = prepare_catalog(sources, raw_directory, nutrient_mappings_path)
    clean_output(output_directory)
    if result.unresolved:
        write_csv(output_directory / "unresolved.draft.csv", unresolved_columns(), result.unresolved)
        raise ValueError(f"nutrition could not be resolved for {len(result.unresolved)} ingredients")
    write_catalog_files(output_directory, result.ingredients, draft=False)
    write_attributions(output_directory, result.sources)
    write_release_documents(output_directory)
    return manifest(result, release_eligible=True)


def build_catalog_draft(
    sources: tuple[Source, ...],
    raw_directory: Path,
    output_directory: Path,
    nutrient_mappings_path: Path,
) -> dict[str, object]:
    verify_downloads(sources, raw_directory)
    result = prepare_catalog(sources, raw_directory, nutrient_mappings_path)
    clean_output(output_directory)
    write_catalog_files(output_directory, result.ingredients, draft=True)
    write_attributions(output_directory, result.sources)
    write_release_documents(output_directory)
    if result.unresolved:
        write_csv(output_directory / "unresolved.draft.csv", unresolved_columns(), result.unresolved)
    return manifest(result, release_eligible=False)


def prepare_catalog(
    sources: tuple[Source, ...],
    raw_directory: Path,
    nutrient_mappings_path: Path,
) -> BuildResult:
    identity_sources = [source for source in sources if source.role == "identity"]
    if len(identity_sources) != 1:
        raise ValueError("catalog requires exactly one identity taxonomy")
    identity_source = identity_sources[0]
    taxonomy = read_ingredient_taxonomy(raw_directory / identity_source.assets[0].file)
    ingredients = {
        ingredient.key: simplify_source_names(ingredient)
        for ingredient in mapped_base_ingredients(taxonomy)
    }
    apply_ingredient_overrides(ingredients, nutrient_mappings_path.parent / "ingredients.csv")
    for language in SUPPORTED_LANGUAGES:
        apply_name_mappings(ingredients, nutrient_mappings_path.parent / f"names_{language}.csv", language)
    records, ndb_to_fdc = read_nutrition_sources(sources, raw_directory, nutrient_mappings_path)
    overrides = read_nutrition_links(nutrient_mappings_path.parent / "nutrition-links.csv")
    catalog_ingredients: list[CatalogIngredient] = []
    unresolved: list[dict[str, str]] = []
    used_sources = {identity_source.source_id}
    for ingredient in ingredients.values():
        record = resolve_nutrition_record(ingredient, records, ndb_to_fdc, overrides)
        if record is None:
            unresolved.append(
                {
                    "taxonomy_key": ingredient.key,
                    "references": "|".join(f"{dataset}:{record_id}" for dataset, record_id in nutrition_references(ingredient)),
                }
            )
            continue
        catalog_ingredients.append(catalog_ingredient(ingredient, record))
        used_sources.add(record.dataset)
    selected_sources = tuple(source for source in sources if source.source_id in used_sources)
    return BuildResult(
        ingredients=tuple(sorted(catalog_ingredients, key=lambda item: (item.taxonomy_key, item.ingredient_id))),
        unresolved=tuple(sorted(unresolved, key=lambda row: row["taxonomy_key"])),
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
) -> tuple[dict[tuple[str, str], NutritionRecord], dict[str, str]]:
    mappings = load_nutrient_mappings(nutrient_mappings_path)
    records: dict[tuple[str, str], NutritionRecord] = {}
    ndb_to_fdc: dict[str, str] = {}
    for source in sources:
        if source.role != "nutrition":
            continue
        adapter = ADAPTERS.get(source.source_id)
        if adapter is None:
            raise ValueError(f"no nutrition adapter for source {source.source_id}")
        source_records = adapter.read(source, raw_directory, mappings)
        for record in source_records:
            records[(record.dataset, record.record_id)] = record
        if source.source_id == "usda-sr-legacy":
            ndb_to_fdc = read_ndb_to_fdc(raw_directory / source.assets[0].file)
    return records, ndb_to_fdc


def resolve_nutrition_record(
    ingredient,
    records: dict[tuple[str, str], NutritionRecord],
    ndb_to_fdc: dict[str, str],
    overrides: dict[str, tuple[str, str, str]],
) -> NutritionRecord | None:
    override = overrides.get(ingredient.key)
    if override is not None:
        dataset, id_type, source_id = override
        record_id = ndb_to_fdc.get(source_id, "") if id_type == "usda_ndb" else source_id
        return records.get((dataset, record_id))
    for dataset, source_id in nutrition_references(ingredient):
        record_id = ndb_to_fdc.get(source_id, "") if dataset == "usda-sr-legacy" else source_id
        record = records.get((dataset, record_id))
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
            if not key or not dataset or not record_id or id_type not in {"record", "usda_ndb"}:
                raise ValueError("nutrition-links.csv has an invalid row")
            if key in links:
                raise ValueError(f"duplicate nutrition link: {key}")
            links[key] = dataset, id_type, record_id
        return links


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
            if not properties:
                raise ValueError(f"new ingredient {key} has no nutrition identity")
            ingredients[key] = TaxonomyIngredient(
                key=key,
                names=dict(existing.names) if existing is not None else {},
                parents=parents,
                properties=properties,
            )


def nutrition_property(dataset: str, id_type: str) -> str:
    if dataset == "ciqual" and id_type == "record":
        return "ciqual_food_code"
    if dataset == "usda-sr-legacy" and id_type == "usda_ndb":
        return "usda_ndb_code"
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


def catalog_ingredient(ingredient, record: NutritionRecord) -> CatalogIngredient:
    names: dict[str, tuple[str, ...]] = {}
    for language in SUPPORTED_LANGUAGES:
        values = localized_names(ingredient, language)
        if values:
            names[language] = values
    missing_languages = [language for language in SUPPORTED_LANGUAGES if not names.get(language)]
    if missing_languages:
        raise ValueError(f"ingredient {ingredient.key} has no names for: {', '.join(missing_languages)}")
    ingredient_id = stable_id("ingredient", ingredient.key)
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
    return CatalogIngredient(
        ingredient_id=ingredient_id,
        taxonomy_key=ingredient.key,
        names=names,
        nutrition=nutrition,
    )


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


def unresolved_columns() -> tuple[str, ...]:
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


def write_attributions(output_directory: Path, sources: tuple[Source, ...]) -> None:
    sections = [
        "RecipeMonster generated ingredient catalog",
        "",
        "Database license: Open Database License 1.0",
        "https://opendatacommons.org/licenses/odbl/1-0/",
        "",
    ]
    for source in sources:
        license_data = source.license
        sections.extend(
            (
                source.name,
                f"Version: {source.version}",
                f"Source: {source.homepage}",
                f"Attribution: {source.attribution}",
                f"License: {license_data['name']} ({license_data['spdx']})",
                f"License URL: {license_data['url']}",
            )
        )
        if content_name := license_data.get("contentName"):
            sections.extend(
                (
                    f"Content license: {content_name} ({license_data['contentSpdx']})",
                    f"Content license URL: {license_data['contentUrl']}",
                )
            )
        sections.append("")
    text = "\n".join(sections).rstrip() + "\n"
    (output_directory / "ATTRIBUTIONS.txt").write_text(text, encoding="utf-8")


def write_release_documents(output_directory: Path) -> None:
    for filename in ("DATA_LICENSE.md", "SOURCES.md"):
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
        (ingredient.nutrition for ingredient in ingredients),
    )
    for language in SUPPORTED_LANGUAGES:
        rows = (
            {"ingredient_id": ingredient.ingredient_id, "name": name}
            for ingredient in ingredients
            for name in ingredient.names.get(language, ())
        )
        write_csv(output_directory / f"ingredients_{language}{suffix}", NAME_COLUMNS, rows)


def manifest(result: BuildResult, release_eligible: bool) -> dict[str, object]:
    return {
        "schemaVersion": "recipemonster.ingredient-catalog/v4",
        "releaseEligible": release_eligible and not result.unresolved,
        "ingredientCount": len(result.ingredients),
        "nameCounts": {
            language: sum(len(ingredient.names.get(language, ())) for ingredient in result.ingredients)
            for language in SUPPORTED_LANGUAGES
        },
        "unresolvedCount": len(result.unresolved),
        "sources": [source.source_id for source in result.sources],
    }
