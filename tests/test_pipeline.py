from __future__ import annotations

import csv
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from recipemonster_data.build import (
    NAME_COLUMNS,
    NUTRITION_COLUMNS,
    SUPPORTED_LANGUAGES,
    build_catalog,
    build_catalog_draft,
    simplify_source_names,
)
from recipemonster_data.config import Asset, Source
from recipemonster_data.download import download_sources
from recipemonster_data.normalize import decimal_value, normalize_name
from recipemonster_data.taxonomy import TaxonomyIngredient, localized_names, mapped_base_ingredients, read_ingredient_taxonomy
from recipemonster_data.validate import validate_catalog


class PipelineTest(unittest.TestCase):
    def test_selects_simple_ingredient_instead_of_preparation_variants(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "ingredients.txt"
            path.write_text(taxonomy_fixture(include_types=True), encoding="utf-8")

            selected = mapped_base_ingredients(read_ingredient_taxonomy(path))

            self.assertEqual([ingredient.key for ingredient in selected], ["apple", "cheese", "feta", "potato"])
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

    def test_builds_localized_name_files_and_one_nutrition_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            inputs = root / "inputs"
            inputs.mkdir()
            create_usda_archive(inputs / "usda.zip")
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
                    "usda-sr-legacy": inputs / "usda.zip",
                    "openfoodfacts-ingredients": inputs / "ingredients.txt",
                },
            )
            mappings = create_nutrient_mappings(root)
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

            manifest = build_catalog(sources, root / "raw", root / "dist", mappings)
            result = validate_catalog(root / "dist")

            self.assertEqual(manifest["ingredientCount"], 2)
            self.assertEqual(
                result,
                {"ingredients": 2, "names_en": 2, "names_pl": 2, "names_de": 2, "names_es": 3, "names_it": 3},
            )
            nutrition = read_output_csv(root / "dist" / "nutrition.csv")
            self.assertEqual(tuple(nutrition[0]), NUTRITION_COLUMNS)
            self.assertEqual({row["nutrition_source"] for row in nutrition}, {"ciqual", "usda-sr-legacy"})
            self.assertEqual({row["basis_g"] for row in nutrition}, {"100"})
            self.assertEqual({row["energy_kcal_kcal"] for row in nutrition}, {"52", "77"})
            english = read_output_csv(root / "dist" / "ingredients_en.csv")
            polish = read_output_csv(root / "dist" / "ingredients_pl.csv")
            self.assertEqual(tuple(english[0]), NAME_COLUMNS)
            self.assertEqual({row["name"] for row in english}, {"apple", "potato"})
            self.assertEqual({row["name"] for row in polish}, {"jabłko", "ziemniak"})
            self.assertNotIn("cooked potato", {row["name"] for row in english})
            attributions = (root / "dist" / "ATTRIBUTIONS.txt").read_text(encoding="utf-8")
            self.assertIn("Database license: Open Database License 1.0", attributions)
            self.assertIn("License: test (LicenseRef-Test)", attributions)
            self.assertEqual(
                (root / "dist" / "DATA_LICENSE.md").read_text(encoding="utf-8"),
                "# Test data license\n",
            )
            self.assertEqual(
                (root / "dist" / "SOURCES.md").read_text(encoding="utf-8"),
                "# Test sources\n",
            )
            generated_files = (
                "ATTRIBUTIONS.txt",
                "DATA_LICENSE.md",
                "SOURCES.md",
                "nutrition.csv",
                *(f"ingredients_{language}.csv" for language in SUPPORTED_LANGUAGES),
            )
            first_build = {name: (root / "dist" / name).read_bytes() for name in generated_files}
            (root / "dist" / "ingredients_it.csv").write_text("ingredient_id,name\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "missing it names"):
                validate_catalog(root / "dist")
            build_catalog(sources, root / "raw", root / "dist", mappings)
            self.assertEqual(
                {name: (root / "dist" / name).read_bytes() for name in generated_files},
                first_build,
            )

    def test_draft_reports_unresolved_nutrition_without_fake_ingredient(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            inputs = root / "inputs"
            inputs.mkdir()
            create_ciqual_files(inputs)
            (inputs / "ingredients.txt").write_text(
                "en:apple\npl:jabłko\nciqual_food_code:en:missing\n",
                encoding="utf-8",
            )
            sources = (test_sources()[0], test_sources()[2])
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

            manifest = build_catalog_draft(sources, root / "raw", root / "dist", create_nutrient_mappings(root))

            self.assertEqual(manifest["ingredientCount"], 0)
            self.assertEqual(manifest["unresolvedCount"], 1)
            unresolved = read_output_csv(root / "dist" / "unresolved.draft.csv")[0]
            self.assertEqual(unresolved["taxonomy_key"], "apple")
            self.assertEqual(unresolved["references"], "ciqual:missing")

    def test_validator_rejects_changed_header(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "nutrition.csv"
            path.write_text("wrong\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "invalid CSV header"):
                validate_catalog(path.parent)


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
                Asset("ciqual", "foods", "https://entrepot.recherche.data.gouv.fr/a", "foods.xml", "xml", "", 100_000, False),
                Asset("ciqual", "nutrients", "https://entrepot.recherche.data.gouv.fr/b", "nutrients.xml", "xml", "", 100_000, False),
                Asset("ciqual", "values", "https://entrepot.recherche.data.gouv.fr/c", "values.xml", "xml", "", 100_000, False),
            ),
        ),
        Source(
            source_id="usda-sr-legacy",
            role="nutrition",
            name="USDA",
            version="1",
            homepage="https://fdc.nal.usda.gov/download-datasets/",
            attribution="USDA",
            license=license_data,
            assets=(Asset("usda-sr-legacy", "archive", "https://fdc.nal.usda.gov/a", "usda.zip", "zip", "", 1_000_000, False),),
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
        "usda_ndb_code:en:11352\n\n"
        "< en:potato\n"
        "en:cooked potato\n"
        "pl:ziemniak gotowany\n"
        "usda_ndb_code:en:11367\n"
    )
    return base + types + vegetables


def create_usda_archive(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        write_csv_entry(
            archive,
            "food.csv",
            [["fdc_id", "data_type", "description"], ["200", "sr_legacy_food", "Potatoes, flesh and skin, raw"]],
        )
        write_csv_entry(archive, "sr_legacy_food.csv", [["fdc_id", "NDB_number"], ["200", "11352"]])
        write_csv_entry(archive, "nutrient.csv", [["id", "name", "unit_name"], ["1008", "Energy", "KCAL"]])
        write_csv_entry(
            archive,
            "food_nutrient.csv",
            [["id", "fdc_id", "nutrient_id", "amount", "min", "max", "median"], ["1", "200", "1008", "77", "", "", ""]],
        )


def create_ciqual_files(directory: Path) -> None:
    (directory / "foods.xml").write_text(
        "<TABLE><ALIM><alim_code>13050</alim_code><alim_nom_fr>Pomme, crue</alim_nom_fr>"
        "<alim_nom_eng>Apple, raw</alim_nom_eng></ALIM></TABLE>",
        encoding="utf-8",
    )
    (directory / "nutrients.xml").write_text(
        "<TABLE><CONST><const_code>328</const_code><const_nom_eng>Energy (kcal/100g)</const_nom_eng>"
        "</CONST></TABLE>",
        encoding="utf-8",
    )
    (directory / "values.xml").write_text(
        "<TABLE><COMPO><alim_code>13050</alim_code><const_code>328</const_code><teneur>52</teneur>"
        "<code_confiance>A</code_confiance></COMPO></TABLE>",
        encoding="utf-8",
    )


def create_nutrient_mappings(root: Path) -> Path:
    (root / "DATA_LICENSE.md").write_text("# Test data license\n", encoding="utf-8")
    (root / "SOURCES.md").write_text("# Test sources\n", encoding="utf-8")
    path = root / "nutrients.json"
    path.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "mappings": [
                    {"source": "ciqual", "sourceId": "328", "key": "energy_kcal"},
                    {"source": "usda-sr-legacy", "sourceId": "1008", "key": "energy_kcal"},
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def write_csv_entry(archive: zipfile.ZipFile, name: str, rows: list[list[str]]) -> None:
    output = io.StringIO(newline="")
    csv.writer(output).writerows(rows)
    archive.writestr(name, output.getvalue())


def read_output_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file))


if __name__ == "__main__":
    unittest.main()
