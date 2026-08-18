from __future__ import annotations

import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

from .adapters import NutritionRecord, NutrientValue, nutrient_key
from .config import Source
from .normalize import decimal_value, nutrient_unit


class CIQUALAdapter:
    def read(
        self,
        source: Source,
        raw_directory: Path,
        nutrient_mappings: dict[tuple[str, str], str],
    ) -> list[NutritionRecord]:
        assets = {asset.asset_id: raw_directory / asset.file for asset in source.assets}
        foods = read_foods(assets["foods"])
        nutrients = read_nutrients(assets["nutrients"])
        values: dict[str, list[dict[str, str]]] = defaultdict(list)
        for element in iter_records(assets["values"], "COMPO"):
            food_id = child_text(element, "alim_code")
            nutrient_id = child_text(element, "const_code")
            amount_text = child_text(element, "teneur")
            nutrient = nutrients.get(nutrient_id)
            if food_id not in foods or nutrient is None or not amount_text:
                continue
            try:
                amount, qualifier = decimal_value(amount_text)
            except ValueError:
                continue
            value = NutrientValue(
                key=nutrient_key(
                    nutrient_mappings,
                    source.source_id,
                    nutrient_id,
                    f"ciqual_{nutrient_id}",
                ),
                amount=amount,
                unit=nutrient["unit"],
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
                    label=food["en"] or food["fr"],
                    values=tuple(sorted(values[food_id], key=lambda item: item.key)),
                )
            )
        return records


def read_foods(path: Path) -> dict[str, dict[str, str]]:
    foods: dict[str, dict[str, str]] = {}
    for element in iter_records(path, "ALIM"):
        food_id = child_text(element, "alim_code")
        french = child_text(element, "alim_nom_fr")
        english = child_text(element, "alim_nom_eng")
        if food_id and (french or english):
            foods[food_id] = {"fr": french, "en": english}
    return foods


def read_nutrients(path: Path) -> dict[str, dict[str, str]]:
    nutrients: dict[str, dict[str, str]] = {}
    for element in iter_records(path, "CONST"):
        nutrient_id = child_text(element, "const_code")
        english = child_text(element, "const_nom_eng")
        french = child_text(element, "const_nom_fr")
        name = english or french
        if nutrient_id and name:
            nutrients[nutrient_id] = {
                "name": name,
                "unit": nutrient_unit(name),
                "infoods": child_text(element, "code_INFOODS"),
            }
    return nutrients


def iter_records(path: Path, tag: str):
    for _, element in ET.iterparse(path, events=("end",)):
        if element.tag == tag:
            yield element
            element.clear()


def child_text(element: ET.Element, tag: str) -> str:
    child = element.find(tag)
    return "" if child is None or child.text is None else child.text.strip()
