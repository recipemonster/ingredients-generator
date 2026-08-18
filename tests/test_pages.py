from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from src.build import NAME_COLUMNS, NUTRITION_COLUMNS, SUPPORTED_LANGUAGES
from src.pages import generate_pages, generate_preview


class PagesTest(unittest.TestCase):
    def test_generates_version_index_and_diff_tables(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            releases = root / "releases"
            write_release(
                releases / "v0.1.0-rc1",
                {"ingredient_a": ("apple", "Apple", "52")},
            )
            write_release(
                releases / "v0.1.0",
                {
                    "ingredient_a": ("apple", "Apple & pear", "53"),
                    "ingredient_b": ("potato", "Potato", "77"),
                    "ingredient_c": ("salt", "Salt", ""),
                },
            )

            generate_pages(releases, root / "site")

            index = (root / "site" / "index.html").read_text(encoding="utf-8")
            release = (root / "site" / "releases" / "v0.1.0.html").read_text(encoding="utf-8")
            self.assertIn("v0.1.0-rc1", index)
            self.assertIn("v0.1.0", index)
            self.assertIn("ingredient_b", release)
            self.assertIn("ingredient_c", release)
            self.assertIn("energy_kcal_kcal", release)
            self.assertIn("Apple &amp; pear", release)
            self.assertNotIn("Apple & pear", release)

    def test_initial_release_does_not_generate_a_full_catalog_diff(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            releases = root / "releases"
            write_release(
                releases / "v0.1.0-rc1",
                {
                    "ingredient_a": ("apple", "Apple", "52"),
                    "ingredient_b": ("potato", "Potato", "77"),
                },
            )

            generate_pages(releases, root / "site")

            index = (root / "site" / "index.html").read_text(encoding="utf-8")
            release = (root / "site" / "releases" / "v0.1.0-rc1.html").read_text(encoding="utf-8")
            self.assertIn("Initial release, no diff", index)
            self.assertIn("No previous version to compare", release)
            self.assertNotIn("ingredient_a", release)
            self.assertNotIn("Added ingredients", release)
            self.assertNotIn("Nutrition changes", release)

    def test_generates_pull_request_preview_against_latest_release(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            releases = root / "releases"
            write_release(releases / "v0.1.0", {"ingredient_a": ("apple", "Apple", "52")})
            candidate = root / "candidate"
            write_release(
                candidate,
                {
                    "ingredient_a": ("apple", "Apple", "53"),
                    "ingredient_b": ("potato", "Potato", "77"),
                },
            )

            generate_pages(releases, root / "site")
            generate_preview(releases, candidate, root / "site", "v0.1.1", 42)

            preview = (root / "site" / "previews" / "pr-42" / "index.html").read_text(encoding="utf-8")
            self.assertIn("Proposed release", preview)
            self.assertIn("v0.1.1", preview)
            self.assertIn("ingredient_b", preview)
            self.assertIn("../../index.html", preview)

    def test_rejects_preview_older_than_latest_release(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            releases = root / "releases"
            write_release(releases / "v0.2.0", {"ingredient_a": ("apple", "Apple", "52")})
            candidate = root / "candidate"
            write_release(candidate, {"ingredient_a": ("apple", "Apple", "52")})

            with self.assertRaisesRegex(ValueError, "must be newer"):
                generate_preview(releases, candidate, root / "site", "v0.1.1", 42)

    def test_reads_legacy_language_files_using_nutrition_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            release = root / "releases" / "v0.1.0"
            write_release(release, {"ingredient_a": ("apple", "Apple", "52")})
            for language in SUPPORTED_LANGUAGES:
                path = release / f"ingredients_{language}.csv"
                with path.open("w", encoding="utf-8", newline="") as file:
                    writer = csv.writer(file, lineterminator="\n")
                    writer.writerow(("ingredient_id", "name"))
                    writer.writerow(("ingredient_a", "Apple"))

            generate_pages(root / "releases", root / "site")

            self.assertIn("1", (root / "site" / "index.html").read_text(encoding="utf-8"))


def write_release(directory: Path, ingredients: dict[str, tuple[str, str, str]]) -> None:
    directory.mkdir(parents=True)
    with (directory / "nutrition.csv").open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=NUTRITION_COLUMNS, lineterminator="\n")
        writer.writeheader()
        for ingredient_id, (taxonomy_key, _, energy) in ingredients.items():
            if not energy:
                continue
            row = {column: "" for column in NUTRITION_COLUMNS}
            row.update(
                {
                    "ingredient_id": ingredient_id,
                    "taxonomy_key": taxonomy_key,
                    "nutrition_source": "test",
                    "nutrition_source_version": "1",
                    "nutrition_source_record_id": taxonomy_key,
                    "nutrition_source_label": taxonomy_key,
                    "basis_g": "100",
                    "energy_kcal_kcal": energy,
                }
            )
            writer.writerow(row)
    for language in SUPPORTED_LANGUAGES:
        with (directory / f"ingredients_{language}.csv").open("w", encoding="utf-8", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=NAME_COLUMNS, lineterminator="\n")
            writer.writeheader()
            for ingredient_id, (taxonomy_key, name, _) in ingredients.items():
                writer.writerow(
                    {
                        "ingredient_id": ingredient_id,
                        "taxonomy_key": taxonomy_key,
                        "name": name if language == "en" else f"{name} {language}",
                    }
                )


if __name__ == "__main__":
    unittest.main()
