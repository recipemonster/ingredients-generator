from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .config import Source


@dataclass(frozen=True)
class NutrientValue:
    key: str
    amount: str
    unit: str
    qualifier: str = ""


@dataclass(frozen=True)
class NutritionRecord:
    dataset: str
    version: str
    record_id: str
    label: str
    values: tuple[NutrientValue, ...]


class SourceAdapter(Protocol):
    def read(
        self,
        source: Source,
        raw_directory: Path,
        nutrient_mappings: dict[tuple[str, str], str],
    ) -> list[NutritionRecord]: ...


def nutrient_key(
    mappings: dict[tuple[str, str], str],
    source_id: str,
    nutrient_id: str,
    fallback: str = "",
) -> str:
    return mappings.get((source_id, nutrient_id), fallback or f"{source_id}_{nutrient_id}")
