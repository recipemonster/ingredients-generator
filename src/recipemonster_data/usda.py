from __future__ import annotations

import csv
import io
import zipfile
from collections import defaultdict
from pathlib import Path

from .adapters import NutritionRecord, NutrientValue, nutrient_key
from .config import Source
from .normalize import decimal_value


class USDAAdapter:
    def read(
        self,
        source: Source,
        raw_directory: Path,
        nutrient_mappings: dict[tuple[str, str], str],
    ) -> list[NutritionRecord]:
        archive_path = raw_directory / source.assets[0].file
        with zipfile.ZipFile(archive_path) as archive:
            foods = {
                row["fdc_id"]: row
                for row in read_csv(archive, "food.csv")
                if row.get("data_type") == "sr_legacy_food" and row.get("fdc_id") and row.get("description")
            }
            nutrients = {row["id"]: row for row in read_csv(archive, "nutrient.csv") if row.get("id")}
            values: dict[str, list[dict[str, str]]] = defaultdict(list)
            for row in read_csv(archive, "food_nutrient.csv"):
                food_id = row.get("fdc_id", "")
                nutrient_id = row.get("nutrient_id", "")
                nutrient = nutrients.get(nutrient_id)
                if food_id not in foods or not nutrient or not row.get("amount"):
                    continue
                try:
                    amount, qualifier = decimal_value(row["amount"])
                except ValueError:
                    continue
                value = NutrientValue(
                    key=nutrient_key(nutrient_mappings, source.source_id, nutrient_id),
                    amount=amount,
                    unit=nutrient.get("unit_name", "").lower(),
                    qualifier=qualifier,
                )
                values[food_id].append(value)

        records: list[NutritionRecord] = []
        for food_id, food in foods.items():
            if not values.get(food_id):
                continue
            records.append(
                NutritionRecord(
                    dataset=source.source_id,
                    version=source.version,
                    record_id=food_id,
                    label=food["description"].strip(),
                    values=tuple(sorted(values[food_id], key=lambda item: item.key)),
                )
            )
        return records


def read_ndb_to_fdc(path: Path) -> dict[str, str]:
    with zipfile.ZipFile(path) as archive:
        return {
            row["NDB_number"]: row["fdc_id"]
            for row in read_csv(archive, "sr_legacy_food.csv")
            if row.get("NDB_number") and row.get("fdc_id")
        }


def read_csv(archive: zipfile.ZipFile, filename: str):
    member = next((name for name in archive.namelist() if Path(name).name.lower() == filename.lower()), None)
    if member is None:
        raise ValueError(f"USDA archive is missing {filename}")
    info = archive.getinfo(member)
    if info.file_size > 256 * 1024 * 1024:
        raise ValueError(f"USDA archive entry is too large: {filename}")
    with archive.open(member) as binary:
        text = io.TextIOWrapper(binary, encoding="utf-8-sig", newline="")
        yield from csv.DictReader(text)
