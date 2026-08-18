from __future__ import annotations

import re
from dataclasses import dataclass

TAG_PATTERN = re.compile(r"^v(\d+)\.(\d+)\.(\d+)(?:-rc\.(\d+))?$")


@dataclass(frozen=True)
class ReleaseSection:
    tag: str
    notes: str


def parse_changelog(text: str) -> tuple[ReleaseSection, ...]:
    sections: list[ReleaseSection] = []
    current_tag = ""
    current_lines: list[str] = []
    unreleased_seen = False
    for line in text.splitlines():
        if not line.startswith("## "):
            if current_tag:
                current_lines.append(line)
            continue
        if current_tag:
            sections.append(release_section(current_tag, current_lines))
        current_tag = line.removeprefix("## ").strip()
        current_lines = []
        if current_tag == "Unreleased":
            if unreleased_seen or sections:
                raise ValueError("Unreleased must occur once before all releases")
            unreleased_seen = True
        elif TAG_PATTERN.fullmatch(current_tag) is None:
            raise ValueError(f"invalid changelog release heading: {current_tag}")
    if current_tag:
        sections.append(release_section(current_tag, current_lines))
    released = tuple(section for section in sections if section.tag != "Unreleased")
    if len({section.tag for section in released}) != len(released):
        raise ValueError("changelog contains duplicate release headings")
    orders = [tag_order(section.tag) for section in released]
    if any(left <= right for left, right in zip(orders, orders[1:])):
        raise ValueError("changelog releases must be ordered newest first")
    return tuple(sections)


def release_notes(text: str, tag: str) -> str:
    if TAG_PATTERN.fullmatch(tag) is None:
        raise ValueError(f"invalid release tag: {tag}")
    for section in parse_changelog(text):
        if section.tag == tag:
            return section.notes + "\n"
    raise ValueError(f"CHANGELOG.md has no section for {tag}")


def release_section(tag: str, lines: list[str]) -> ReleaseSection:
    notes = "\n".join(lines).strip()
    if not notes:
        raise ValueError(f"changelog section {tag} is empty")
    return ReleaseSection(tag=tag, notes=notes)


def tag_order(tag: str) -> tuple[int, int, int, int, int]:
    match = TAG_PATTERN.fullmatch(tag)
    if match is None:
        raise ValueError(f"invalid release tag: {tag}")
    major, minor, patch, candidate = match.groups()
    return int(major), int(minor), int(patch), int(candidate is None), int(candidate or 0)
