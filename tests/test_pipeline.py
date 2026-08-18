from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from src.build import (
    NAME_COLUMNS,
    NUTRITION_COLUMNS,
    SUPPORTED_LANGUAGES,
    assign_ingredient_ids,
    build_catalog,
    load_previous_ingredient_ids,
    simplify_source_names,
    validate_nutrition_links,
)
from src.config import Asset, Source, VersionCheck
from src.download import download_sources
from src.normalize import decimal_value, normalize_name
from src.taxonomy import TaxonomyIngredient, base_ingredients, localized_names, read_ingredient_taxonomy
from src.validate import validate_catalog


class PipelineTest(unittest.TestCase):
    def test_selects_simple_ingredient_instead_of_preparation_variants(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "ingredients.txt"
            path.write_text(taxonomy_fixture(include_types=True), encoding="utf-8")

            selected = base_ingredients(read_ingredient_taxonomy(path))

            self.assertEqual([ingredient.key for ingredient in selected], ["apple", "cheese", "feta", "potato", "salt"])
            self.assertEqual(selected[3].names["pl"], ("ziemniak", "ziemniaki"))

    def test_normalization_preserves_zero_and_less_than_qualifier(self) -> None:
        self.assertEqual(decimal_value(" 0,00 "), ("0.00", ""))
        self.assertEqual(decimal_value(" < 0,01 "), ("0.01", "less_than"))
        self.assertEqual(normalize_name("  Crème, fraîche! "), "crème fraîche")

    def test_removes_names_that_only_reorder_words(self) -> None:
        ingredient = TaxonomyIngredient(
            key="white sugar",
            names={"pl": ("biały cukier", "cukier biały", "cukier puder")},
            parents=(),
            properties={},
        )

        self.assertEqual(localized_names(ingredient, "pl"), ("biały cukier", "cukier puder"))

    def test_keeps_only_simple_source_name_in_each_language(self) -> None:
        ingredient = TaxonomyIngredient(
            key="tomato",
            names={
                "en": ("tomato", "tomatoes", "tomato-based"),
                "pl": ("pomidor", "pomidory", "pomidorów", "pomidorowy"),
            },
            parents=(),
            properties={},
        )

        simplified = simplify_source_names(ingredient)

        self.assertEqual(simplified.names, {"en": ("tomato",), "pl": ("pomidor",)})

    def test_omits_unreviewed_localized_name_identical_to_english(self) -> None:
        ingredient = TaxonomyIngredient(
            key="black pudding",
            names={
                "en": ("black pudding",),
                "de": ("Black Pudding", "Blutwurst"),
            },
            parents=(),
            properties={},
        )

        self.assertEqual(simplify_source_names(ingredient).names, {"en": ("black pudding",)})

    def test_builds_localized_name_files_and_one_nutrition_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            inputs = root / "inputs"
            inputs.mkdir()
            create_ciqual_files(inputs)
            (inputs / "ingredients.txt").write_text(taxonomy_fixture(), encoding="utf-8")
            sources = test_sources()
            download_sources(
                sources,
                root / "raw",
                {
                    "ciqual/foods": inputs / "foods.xml",
                    "ciqual/nutrients": inputs / "nutrients.xml",
                    "ciqual/values": inputs / "values.xml",
                    "openfoodfacts-ingredients": inputs / "ingredients.txt",
                },
            )
            mappings = create_nutrient_mappings(root)
            previous_catalog = write_previous_catalog(root, {"apple": "ingredient_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"})
            (root / "names_es.csv").write_text(
                "taxonomy_key,names\n"
                "potato,patata|patatas\n",
                encoding="utf-8",
            )
            (root / "names_it.csv").write_text(
                "taxonomy_key,names\n"
                "potato,patata|patate\n",
                encoding="utf-8",
            )

            manifest = build_catalog(sources, root / "raw", root / "dist", mappings, previous_catalog)
            result = validate_catalog(root / "dist")

            self.assertEqual(manifest["ingredientCount"], 3)
            self.assertEqual(manifest["nutritionCount"], 2)
            self.assertEqual(manifest["missingNutritionCount"], 1)
            self.assertEqual(
                result,
                {
                    "ingredients": 3,
                    "nutrition": 2,
                    "names_en": 3,
                    "names_pl": 3,
                    "names_de": 3,
                    "names_es": 4,
                    "names_it": 4,
                },
            )
            nutrition = read_output_csv(root / "dist" / "nutrition.csv")
            self.assertEqual(tuple(nutrition[0]), NUTRITION_COLUMNS)
            self.assertEqual({row["nutrition_source"] for row in nutrition}, {"ciqual"})
            self.assertEqual(
                {row["taxonomy_key"]: row["ingredient_id"] for row in nutrition},
                {
                    "apple": "ingredient_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                    "potato": "ingredient_e91c254ad58860a02c788dfb5c1a65d6",
                },
            )
            self.assertEqual({row["basis_g"] for row in nutrition}, {"100"})
            self.assertEqual({row["energy_kcal_kcal"] for row in nutrition}, {"52", "77"})
            english = read_output_csv(root / "dist" / "ingredients_en.csv")
            polish = read_output_csv(root / "dist" / "ingredients_pl.csv")
            self.assertEqual(tuple(english[0]), NAME_COLUMNS)
            self.assertEqual({row["name"] for row in english}, {"apple", "potato", "salt"})
            self.assertEqual({row["name"] for row in polish}, {"jabłko", "ziemniak", "sól"})
            self.assertNotIn("cooked potato", {row["name"] for row in english})
            self.assertEqual(
                (root / "dist" / "ATTRIBUTIONS.md").read_text(encoding="utf-8"),
                "# Test attributions\n",
            )
            self.assertEqual(
                (root / "dist" / "LICENCE_DATASET.md").read_text(encoding="utf-8"),
                "# Test ODbL license\n",
            )
            generated_files = (
                "ATTRIBUTIONS.md",
                "LICENCE_DATASET.md",
                "nutrition.csv",
                *(f"ingredients_{language}.csv" for language in SUPPORTED_LANGUAGES),
            )
            first_build = {name: (root / "dist" / name).read_bytes() for name in generated_files}
            (root / "dist" / "ingredients_it.csv").write_text(
                "ingredient_id,taxonomy_key,name\n",
                encoding="utf-8",
            )
            self.assertEqual(validate_catalog(root / "dist")["names_it"], 0)
            build_catalog(sources, root / "raw", root / "dist", mappings, previous_catalog)
            self.assertEqual(
                {name: (root / "dist" / name).read_bytes() for name in generated_files},
                first_build,
            )

    def test_build_keeps_ingredient_without_nutrition_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            inputs = root / "inputs"
            inputs.mkdir()
            create_ciqual_files(inputs)
            (inputs / "ingredients.txt").write_text(
                "en:apple\nciqual_food_code:en:missing\n",
                encoding="utf-8",
            )
            sources = test_sources()
            download_sources(
                sources,
                root / "raw",
                {
                    "ciqual/foods": inputs / "foods.xml",
                    "ciqual/nutrients": inputs / "nutrients.xml",
                    "ciqual/values": inputs / "values.xml",
                    "openfoodfacts-ingredients": inputs / "ingredients.txt",
                },
            )

            manifest = build_catalog(sources, root / "raw", root / "dist", create_nutrient_mappings(root))

            self.assertEqual(manifest["ingredientCount"], 1)
            self.assertEqual(manifest["nutritionCount"], 0)
            self.assertEqual(manifest["missingNutritionCount"], 1)
            self.assertEqual(read_output_csv(root / "dist" / "nutrition.csv"), [])
            self.assertEqual(validate_catalog(root / "dist")["ingredients"], 1)

    def test_validator_rejects_changed_header(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "nutrition.csv"
            path.write_text("wrong\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "invalid CSV header"):
                validate_catalog(path.parent)

    def test_validator_requires_an_english_identity_name(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            create_catalog_files(root, english_name="")

            with self.assertRaisesRegex(ValueError, "missing en names"):
                validate_catalog(root)

    def test_rejects_unknown_manual_nutrition_link(self) -> None:
        ingredient = TaxonomyIngredient(key="apple", names={"en": ("apple",)}, parents=(), properties={})

        with self.assertRaisesRegex(ValueError, "unknown record: ciqual/missing"):
            validate_nutrition_links(
                {"apple": ("ciqual", "record", "missing")},
                {"apple": ingredient},
                {},
            )

    def test_reuses_released_ids_and_assigns_new_ids(self) -> None:
        ingredients = {
            key: TaxonomyIngredient(key=key, names={}, parents=(), properties={})
            for key in ("apple", "potato")
        }

        assigned = assign_ingredient_ids(
            ingredients,
            {"apple": "ingredient_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},
        )

        self.assertEqual(assigned["apple"], "ingredient_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
        self.assertEqual(assigned["potato"], "ingredient_e91c254ad58860a02c788dfb5c1a65d6")

    def test_rejects_disappearing_released_ingredient(self) -> None:
        ingredient = TaxonomyIngredient(key="apple", names={}, parents=(), properties={})

        with self.assertRaisesRegex(ValueError, "previously released ingredients disappeared: potato"):
            assign_ingredient_ids(
                {ingredient.key: ingredient},
                {
                    "apple": "ingredient_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                    "potato": "ingredient_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                },
            )

    def test_rejects_duplicate_id_in_previous_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            duplicate_id = "ingredient_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
            path = write_previous_catalog(root, {"apple": duplicate_id, "potato": duplicate_id})

            with self.assertRaisesRegex(ValueError, "is used by apple and potato"):
                load_previous_ingredient_ids(path)

    def test_loads_ids_from_legacy_nutrition_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "nutrition.csv"
            with path.open("w", encoding="utf-8", newline="") as file:
                writer = csv.DictWriter(file, fieldnames=NUTRITION_COLUMNS, lineterminator="\n")
                writer.writeheader()
                row = {column: "" for column in NUTRITION_COLUMNS}
                row.update(
                    {
                        "ingredient_id": "ingredient_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                        "taxonomy_key": "apple",
                    }
                )
                writer.writerow(row)

            self.assertEqual(
                load_previous_ingredient_ids(path),
                {"apple": "ingredient_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},
            )


def test_sources() -> tuple[Source, ...]:
    license_data = {"name": "test", "spdx": "LicenseRef-Test", "url": "https://example.test/license"}
    return (
        Source(
            source_id="ciqual",
            role="nutrition",
            name="Ciqual",
            version="1",
            homepage="https://doi.org/10.57745/RDMHWY",
            attribution="Anses",
            license=license_data,
            assets=(
                Asset("ciqual", "foods", "https://entrepot.recherche.data.gouv.fr/a", "foods.xml", "xml", "", 100_000, False, VersionCheck("etag", "test")),
                Asset("ciqual", "nutrients", "https://entrepot.recherche.data.gouv.fr/b", "nutrients.xml", "xml", "", 100_000, False, VersionCheck("etag", "test")),
                Asset("ciqual", "values", "https://entrepot.recherche.data.gouv.fr/c", "values.xml", "xml", "", 100_000, False, VersionCheck("etag", "test")),
            ),
        ),
        Source(
            source_id="openfoodfacts-ingredients",
            role="identity",
            name="Open Food Facts ingredient taxonomy",
            version="1",
            homepage="https://github.com/openfoodfacts/openfoodfacts-server",
            attribution="Open Food Facts contributors",
            license=license_data,
            assets=(
                Asset(
                    "openfoodfacts-ingredients",
                    "taxonomy",
                    "https://raw.githubusercontent.com/openfoodfacts/openfoodfacts-server/main/taxonomies/food/ingredients.txt",
                    "ingredients.txt",
                    "text",
                    "",
                    1_000_000,
                    False,
                    VersionCheck("etag", "test"),
                ),
            ),
        ),
    )


def taxonomy_fixture(include_types: bool = False) -> str:
    types = (
        "en:cheese\n"
        "pl:ser\n"
        "ciqual_food_code:en:12999\n\n"
        "< en:cheese\n"
        "en:feta\n"
        "pl:feta\n"
        "ciqual_food_code:en:12123\n\n"
        if include_types
        else ""
    )
    base = (
        "en:apple, apples\n"
        "pl:jabłko, jabłka\n"
        "de:Apfel, Äpfel\n"
        "es:manzana, manzanas\n"
        "it:mela, mele\n"
        "ciqual_food_code:en:13050\n\n"
    )
    vegetables = (
        "< en:root vegetable\n"
        "en:potato, potatoes\n"
        "pl:ziemniak, ziemniaki\n"
        "de:Kartoffel, Kartoffeln\n"
        "ciqual_food_code:en:20000\n\n"
        "< en:potato\n"
        "en:cooked potato\n"
        "pl:ziemniak gotowany\n"
        "ciqual_food_code:en:20001\n"
    )
    salt = "\nen:salt\npl:sól\nde:Salz\nes:sal\nit:sale\n"
    return base + types + vegetables + salt


def create_ciqual_files(directory: Path) -> None:
    (directory / "foods.xml").write_text(
        "<TABLE><ALIM><alim_code>13050</alim_code><alim_nom_fr>Pomme, crue</alim_nom_fr>"
        "<alim_nom_eng>Apple, raw</alim_nom_eng></ALIM>"
        "<ALIM><alim_code>20000</alim_code><alim_nom_fr>Pomme de terre, crue</alim_nom_fr>"
        "<alim_nom_eng>Potato, raw</alim_nom_eng></ALIM></TABLE>",
        encoding="utf-8",
    )
    (directory / "nutrients.xml").write_text(
        "<TABLE><CONST><const_code>328</const_code><const_nom_eng>Energy (kcal/100g)</const_nom_eng>"
        "</CONST></TABLE>",
        encoding="utf-8",
    )
    (directory / "values.xml").write_text(
        "<TABLE><COMPO><alim_code>13050</alim_code><const_code>328</const_code><teneur>52</teneur>"
        "<code_confiance>A</code_confiance></COMPO>"
        "<COMPO><alim_code>20000</alim_code><const_code>328</const_code><teneur>77</teneur>"
        "<code_confiance>A</code_confiance></COMPO></TABLE>",
        encoding="utf-8",
    )


def create_nutrient_mappings(root: Path) -> Path:
    (root / "ATTRIBUTIONS.md").write_text("# Test attributions\n", encoding="utf-8")
    (root / "LICENCE_DATASET.md").write_text("# Test ODbL license\n", encoding="utf-8")
    path = root / "nutrients.json"
    path.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "mappings": [
                    {"source": "ciqual", "sourceId": "328", "key": "energy_kcal"},
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def write_previous_catalog(root: Path, identities: dict[str, str]) -> Path:
    path = root / "previous-ingredients-en.csv"
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=NAME_COLUMNS, lineterminator="\n")
        writer.writeheader()
        for taxonomy_key, ingredient_id in identities.items():
            writer.writerow({"ingredient_id": ingredient_id, "taxonomy_key": taxonomy_key, "name": taxonomy_key})
    return path


def read_output_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file))


def create_catalog_files(root: Path, english_name: str) -> None:
    ingredient_id = "ingredient_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    for language in SUPPORTED_LANGUAGES:
        path = root / f"ingredients_{language}.csv"
        with path.open("w", encoding="utf-8", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=NAME_COLUMNS, lineterminator="\n")
            writer.writeheader()
            name = english_name if language == "en" else ("jabłko" if language == "pl" else "")
            if name:
                writer.writerow({"ingredient_id": ingredient_id, "taxonomy_key": "apple", "name": name})
    with (root / "nutrition.csv").open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=NUTRITION_COLUMNS, lineterminator="\n")
        writer.writeheader()
        row = {column: "" for column in NUTRITION_COLUMNS}
        row.update(
            {
                "ingredient_id": ingredient_id,
                "taxonomy_key": "apple",
                "nutrition_source": "ciqual",
                "nutrition_source_version": "1",
                "nutrition_source_record_id": "1",
                "nutrition_source_label": "Apple",
                "basis_g": "100",
                "energy_kcal_kcal": "52",
            }
        )
        writer.writerow(row)


if __name__ == "__main__":
    unittest.main()
