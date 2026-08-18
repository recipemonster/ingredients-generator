from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from recipemonster_data.build import NUTRITION_COLUMNS, SUPPORTED_LANGUAGES
from recipemonster_data.pages import generate_pages, generate_preview


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
                },
            )

            generate_pages(releases, root / "site")

            index = (root / "site" / "index.html").read_text(encoding="utf-8")
            release = (root / "site" / "releases" / "v0.1.0.html").read_text(encoding="utf-8")
            self.assertIn("v0.1.0-rc1", index)
            self.assertIn("v0.1.0", index)
            self.assertIn("ingredient_b", release)
            self.assertIn("energy_kcal_kcal", release)
            self.assertIn("Apple &amp; pear", release)
            self.assertNotIn("Apple & pear", release)

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


def write_release(directory: Path, ingredients: dict[str, tuple[str, str, str]]) -> None:
    directory.mkdir(parents=True)
    with (directory / "nutrition.csv").open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=NUTRITION_COLUMNS, lineterminator="\n")
        writer.writeheader()
        for ingredient_id, (taxonomy_key, _, energy) in ingredients.items():
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
            writer = csv.writer(file, lineterminator="\n")
            writer.writerow(("ingredient_id", "name"))
            for ingredient_id, (_, name, _) in ingredients.items():
                writer.writerow((ingredient_id, name if language == "en" else f"{name} {language}"))


if __name__ == "__main__":
    unittest.main()
