from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

VERSION_PATTERN = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-rc(\d+))?$")


@dataclass(frozen=True)
class Version:
    major: int
    minor: int
    patch: int
    candidate: int | None = None

    def order(self) -> tuple[int, int, int, int, int]:
        return self.major, self.minor, self.patch, int(self.candidate is None), self.candidate or 0

    def __str__(self) -> str:
        suffix = f"-rc{self.candidate}" if self.candidate is not None else ""
        return f"{self.major}.{self.minor}.{self.patch}{suffix}"


def parse_version(value: str) -> Version:
    normalized = value.strip().removeprefix("v")
    match = VERSION_PATTERN.fullmatch(normalized)
    if match is None:
        raise ValueError(f"invalid semantic version: {value.strip()}")
    major, minor, patch, candidate = match.groups()
    values = (int(major), int(minor), int(patch))
    if any(number < 0 for number in values) or candidate == "0":
        raise ValueError(f"invalid semantic version: {value.strip()}")
    return Version(*values, candidate=int(candidate) if candidate is not None else None)


def read_versions(path: Path) -> tuple[Version, ...]:
    if not path.is_file():
        return ()
    versions = tuple(parse_version(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
    if len(set(versions)) != len(versions):
        raise ValueError("published versions contain duplicates")
    return versions


def latest_version(versions: tuple[Version, ...]) -> Version | None:
    return max(versions, key=Version.order, default=None)


def next_patch_version(versions: tuple[Version, ...]) -> Version:
    latest = latest_version(versions)
    if latest is None:
        return Version(major=0, minor=1, patch=0)
    if latest.candidate is not None:
        return Version(major=latest.major, minor=latest.minor, patch=latest.patch)
    return Version(major=latest.major, minor=latest.minor, patch=latest.patch + 1)


def validate_new_version(candidate: Version, published: tuple[Version, ...]) -> None:
    if candidate in published:
        raise ValueError(f"version {candidate} is already published")
    latest = latest_version(published)
    if latest is not None and candidate.order() <= latest.order():
        raise ValueError(f"version {candidate} must be newer than {latest}")


def read_version_file(path: Path) -> Version:
    lines = path.read_text(encoding="utf-8").splitlines()
    if len(lines) != 1 or not lines[0].strip():
        raise ValueError("VERSION must contain exactly one semantic version")
    return parse_version(lines[0])
