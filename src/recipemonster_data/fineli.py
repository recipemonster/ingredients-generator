from __future__ import annotations

import csv
import io
import zipfile
from collections import defaultdict
from pathlib import Path

from .adapters import NutritionRecord, NutrientValue, nutrient_key
from .config import Source
from .normalize import decimal_value


class FineliAdapter:
    def read(
        self,
        source: Source,
        raw_directory: Path,
        nutrient_mappings: dict[tuple[str, str], str],
    ) -> list[NutritionRecord]:
        with zipfile.ZipFile(raw_directory / source.assets[0].file) as archive:
            members = {Path(name).name.casefold(): name for name in archive.namelist() if not name.endswith("/")}
            foods = {field(row, "foodid"): row for row in csv_rows(archive, members, "food.csv") if field(row, "foodid")}
            names = read_names(archive, members, foods)
            components = read_components(archive, members)
            values: dict[str, list[dict[str, str]]] = defaultdict(list)
            for row in csv_rows(archive, members, "component_value.csv"):
                food_id = field(row, "foodid")
                component_id = field(row, "eufdname", "componentid", "component")
                amount_text = field(row, "bestloc", "value", "amount")
                component = components.get(component_id, {})
                if food_id not in foods or not component_id or not amount_text:
                    continue
                try:
                    amount, qualifier = decimal_value(amount_text)
                except ValueError:
                    continue
                value = NutrientValue(
                    key=nutrient_key(
                        nutrient_mappings,
                        source.source_id,
                        component_id,
                        component_id.casefold(),
                    ),
                    amount=amount,
                    unit=component.get("unit", field(row, "unit")).casefold(),
                    qualifier=qualifier,
                )
                values[food_id].append(value)

        records: list[NutritionRecord] = []
        for food_id in foods:
            food_names = names.get(food_id, {})
            canonical_name = food_names.get("en") or food_names.get("fi") or food_names.get("sv")
            if not canonical_name or not values.get(food_id):
                continue
            records.append(
                NutritionRecord(
                    dataset=source.source_id,
                    version=source.version,
                    record_id=food_id,
                    label=canonical_name,
                    values=tuple(sorted(values[food_id], key=lambda item: item.key)),
                )
            )
        return records


def read_names(archive: zipfile.ZipFile, members: dict[str, str], foods: dict[str, dict[str, str]]):
    names: dict[str, dict[str, str]] = defaultdict(dict)
    for food_id, row in foods.items():
        for language in ("fi", "sv", "en"):
            value = field(row, f"foodname{language}", f"name{language}")
            if value:
                names[food_id][language] = value
    for language in ("fi", "sv", "en"):
        filename = f"foodname_{language}.csv"
        if filename not in members:
            continue
        for row in csv_rows(archive, members, filename):
            food_id = field(row, "foodid")
            name = field(row, "foodname", "name")
            if food_id in foods and name:
                names[food_id][language] = name
    return names


def read_components(archive: zipfile.ZipFile, members: dict[str, str]) -> dict[str, dict[str, str]]:
    components: dict[str, dict[str, str]] = {}
    for row in csv_rows(archive, members, "component.csv"):
        component_id = field(row, "eufdname", "componentid", "component")
        if component_id:
            components[component_id] = {
                "name": field(row, "componentnameen", "componentname", "name") or component_id,
                "unit": field(row, "compunit", "unit"),
            }
    for language in ("en", "fi", "sv"):
        filename = f"componentname_{language}.csv"
        if filename not in members:
            continue
        for row in csv_rows(archive, members, filename):
            component_id = field(row, "eufdname", "componentid", "component")
            if component_id in components and language == "en":
                components[component_id]["name"] = field(row, "componentname", "name") or component_id
    return components


def csv_rows(archive: zipfile.ZipFile, members: dict[str, str], filename: str):
    member = members.get(filename.casefold())
    if member is None:
        raise ValueError(f"Fineli archive is missing {filename}")
    info = archive.getinfo(member)
    if info.file_size > 256 * 1024 * 1024:
        raise ValueError(f"Fineli archive entry is too large: {filename}")
    with archive.open(member) as binary:
        content = binary.read().decode("utf-8-sig")
        try:
            dialect = csv.Sniffer().sniff(content[:8192], delimiters=",;\t^")
        except csv.Error:
            dialect = csv.excel
        text = io.StringIO(content, newline="")
        for row in csv.DictReader(text, dialect=dialect):
            yield {normalize_header(key): (value or "").strip() for key, value in row.items() if key}


def normalize_header(value: str) -> str:
    return "".join(character for character in value.casefold().lstrip("\ufeff") if character.isalnum())


def field(row: dict[str, str], *names: str) -> str:
    return next((row.get(normalize_header(name), "") for name in names if row.get(normalize_header(name), "")), "")
