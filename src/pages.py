from __future__ import annotations

import csv
import html
from dataclasses import dataclass
from pathlib import Path

from .build import NAME_COLUMNS, NUTRITION_COLUMNS, SUPPORTED_LANGUAGES
from .changelog import TAG_PATTERN

LEGACY_NAME_COLUMNS = ("ingredient_id", "name")


@dataclass(frozen=True)
class ReleaseCatalog:
    tag: str
    identities: dict[str, str]
    nutrition: dict[str, dict[str, str]]
    names: dict[str, dict[str, tuple[str, ...]]]


@dataclass(frozen=True)
class ReleaseDiff:
    has_previous: bool
    added: tuple[str, ...]
    removed: tuple[str, ...]
    identity_changes: tuple[tuple[str, str, str], ...]
    name_changes: tuple[tuple[str, str, str, str], ...]
    nutrition_changes: tuple[tuple[str, str, str, str], ...]


def generate_pages(releases_directory: Path, output_directory: Path) -> None:
    catalogs = sorted(
        (load_release(path) for path in releases_directory.iterdir() if path.is_dir()),
        key=lambda catalog: release_order(catalog.tag),
    )
    if not catalogs:
        raise ValueError("release history is empty")
    (output_directory / "releases").mkdir(parents=True, exist_ok=True)
    summaries: list[tuple[ReleaseCatalog, ReleaseDiff]] = []
    previous = None
    for catalog in catalogs:
        difference = compare_releases(previous, catalog)
        summaries.append((catalog, difference))
        (output_directory / "releases" / f"{catalog.tag}.html").write_text(
            release_page(catalog, previous, difference),
            encoding="utf-8",
        )
        previous = catalog
    (output_directory / "index.html").write_text(index_page(tuple(reversed(summaries))), encoding="utf-8")


def generate_preview(
    releases_directory: Path,
    candidate_directory: Path,
    output_directory: Path,
    candidate_tag: str,
    pull_request_number: int,
) -> None:
    catalogs = sorted(
        (load_release(path) for path in releases_directory.iterdir() if path.is_dir()),
        key=lambda catalog: release_order(catalog.tag),
    )
    if not catalogs:
        raise ValueError("release history is empty")
    if TAG_PATTERN.fullmatch(candidate_tag) is None:
        raise ValueError(f"invalid candidate tag: {candidate_tag}")
    candidate = load_catalog(candidate_directory, candidate_tag)
    previous = catalogs[-1]
    if release_order(candidate.tag) <= release_order(previous.tag):
        raise ValueError(f"candidate {candidate.tag} must be newer than {previous.tag}")
    destination = output_directory / "previews" / f"pr-{pull_request_number}"
    destination.mkdir(parents=True, exist_ok=True)
    difference = compare_releases(previous, candidate)
    destination.joinpath("index.html").write_text(
        release_page(candidate, previous, difference, "../../index.html", "Proposed release"),
        encoding="utf-8",
    )


def load_release(directory: Path) -> ReleaseCatalog:
    tag = directory.name
    if TAG_PATTERN.fullmatch(tag) is None:
        raise ValueError(f"invalid release directory: {tag}")
    return load_catalog(directory, tag)


def load_catalog(directory: Path, tag: str) -> ReleaseCatalog:
    nutrition_rows = read_csv(directory / "nutrition.csv")
    if not nutrition_rows or tuple(nutrition_rows[0].keys()) != NUTRITION_COLUMNS:
        raise ValueError(f"{tag} has an invalid nutrition catalog")
    nutrition = {row["ingredient_id"]: row for row in nutrition_rows}
    if len(nutrition) != len(nutrition_rows):
        raise ValueError(f"{tag} has duplicate ingredient IDs")
    names: dict[str, dict[str, tuple[str, ...]]] = {}
    identities = {ingredient_id: row["taxonomy_key"] for ingredient_id, row in nutrition.items()}
    for language in SUPPORTED_LANGUAGES:
        grouped: dict[str, list[str]] = {}
        rows = read_csv(directory / f"ingredients_{language}.csv")
        columns = tuple(rows[0].keys()) if rows else ()
        if columns not in {NAME_COLUMNS, LEGACY_NAME_COLUMNS}:
            raise ValueError(f"{tag} has an invalid {language} ingredient catalog")
        for row in rows:
            ingredient_id = row["ingredient_id"]
            if columns == NAME_COLUMNS:
                taxonomy_key = row["taxonomy_key"]
                existing = identities.get(ingredient_id)
                if existing is not None and existing != taxonomy_key:
                    raise ValueError(f"{tag} has conflicting ingredient identities")
                identities[ingredient_id] = taxonomy_key
            elif ingredient_id not in identities:
                raise ValueError(f"{tag} has a legacy name without nutrition identity")
            grouped.setdefault(row["ingredient_id"], []).append(row["name"])
        names[language] = {ingredient_id: tuple(values) for ingredient_id, values in grouped.items()}
    return ReleaseCatalog(tag=tag, identities=identities, nutrition=nutrition, names=names)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file))


def release_order(tag: str) -> tuple[int, int, int, int, int]:
    match = TAG_PATTERN.fullmatch(tag)
    if match is None:
        raise ValueError(f"invalid release tag: {tag}")
    major, minor, patch, candidate = match.groups()
    return int(major), int(minor), int(patch), int(candidate is None), int(candidate or 0)


def compare_releases(previous: ReleaseCatalog | None, current: ReleaseCatalog) -> ReleaseDiff:
    if previous is None:
        return ReleaseDiff(
            has_previous=False,
            added=(),
            removed=(),
            identity_changes=(),
            name_changes=(),
            nutrition_changes=(),
        )
    previous_ids = set(previous.identities)
    current_ids = set(current.identities)
    previous_by_key = {taxonomy_key: ingredient_id for ingredient_id, taxonomy_key in previous.identities.items()}
    current_by_key = {taxonomy_key: ingredient_id for ingredient_id, taxonomy_key in current.identities.items()}
    identity_changes = tuple(
        (key, previous_by_key[key], current_by_key[key])
        for key in sorted(set(previous_by_key) & set(current_by_key))
        if previous_by_key[key] != current_by_key[key]
    )
    name_changes: list[tuple[str, str, str, str]] = []
    for ingredient_id in sorted(previous_ids & current_ids):
        for language in SUPPORTED_LANGUAGES:
            before = previous.names[language].get(ingredient_id, ())
            after = current.names[language].get(ingredient_id, ())
            if before != after:
                name_changes.append((ingredient_id, language, ", ".join(before), ", ".join(after)))
    nutrition_changes: list[tuple[str, str, str, str]] = []
    ignored = {"ingredient_id", "taxonomy_key"}
    for ingredient_id in sorted(previous_ids & current_ids):
        before = previous.nutrition.get(ingredient_id, {})
        after = current.nutrition.get(ingredient_id, {})
        for field in NUTRITION_COLUMNS:
            if field not in ignored and before.get(field, "") != after.get(field, ""):
                nutrition_changes.append((ingredient_id, field, before.get(field, ""), after.get(field, "")))
    return ReleaseDiff(
        has_previous=True,
        added=tuple(sorted(current_ids - previous_ids)),
        removed=tuple(sorted(previous_ids - current_ids)),
        identity_changes=identity_changes,
        name_changes=tuple(name_changes),
        nutrition_changes=tuple(nutrition_changes),
    )


def index_page(summaries: tuple[tuple[ReleaseCatalog, ReleaseDiff], ...]) -> str:
    rows = "".join(index_row(catalog, diff) for catalog, diff in summaries)
    body = (
        "<header><p class=\"eyebrow\">RecipeMonster data</p><h1>Ingredient catalog releases</h1>"
        "<p>Version history generated from published release artifacts.</p></header>"
        "<main><section class=\"panel\"><h2>Versions</h2><div class=\"table-wrap\"><table>"
        "<thead><tr><th>Version</th><th>Ingredients</th><th>Added</th><th>Removed</th>"
        f"<th>Name changes</th><th>Nutrition changes</th></tr></thead><tbody>{rows}</tbody></table></div></section></main>"
    )
    return document("Ingredient catalog releases", body)


def index_row(catalog: ReleaseCatalog, diff: ReleaseDiff) -> str:
    prefix = (
        f"<tr><td><a href=\"releases/{escape(catalog.tag)}.html\">{escape(catalog.tag)}</a></td>"
        f"<td>{len(catalog.identities)}</td>"
    )
    if not diff.has_previous:
        return prefix + '<td colspan="4" class="empty">Initial release, no diff</td></tr>'
    return (
        prefix
        + f"<td class=\"positive\">+{len(diff.added)}</td>"
        + f"<td class=\"negative\">-{len(diff.removed)}</td><td>{len(diff.name_changes)}</td>"
        + f"<td>{len(diff.nutrition_changes)}</td></tr>"
    )


def release_page(
    current: ReleaseCatalog,
    previous: ReleaseCatalog | None,
    diff: ReleaseDiff,
    index_href: str = "../index.html",
    eyebrow: str = "Release diff",
) -> str:
    if previous is None:
        body = (
            f"<nav><a href=\"{escape(index_href)}\">All releases</a></nav>"
            f"<header><p class=\"eyebrow\">Initial release</p><h1>{escape(current.tag)}</h1>"
            "<p>No previous version to compare.</p></header>"
            f"<main><section class=\"summary initial\">{summary_card('Ingredients', len(current.identities), 'Total in this version')}</section></main>"
        )
        return document(f"{current.tag} release", body)
    previous_label = previous.tag
    cards = (
        summary_card("Ingredients", len(current.identities), "Total in this version")
        + summary_card("Added", len(diff.added), f"Compared with {previous_label}")
        + summary_card("Removed", len(diff.removed), f"Compared with {previous_label}")
        + summary_card("Changed fields", len(diff.name_changes) + len(diff.nutrition_changes), "Names and nutrition")
    )
    body = (
        f"<nav><a href=\"{escape(index_href)}\">All releases</a></nav><header><p class=\"eyebrow\">{escape(eyebrow)}</p>"
        f"<h1>{escape(current.tag)}</h1><p>Compared with {escape(previous_label)}.</p></header>"
        f"<main><section class=\"summary\">{cards}</section>"
        + diff_table("Added ingredients", ("Ingredient", "Taxonomy key"), ingredient_rows(current, diff.added))
        + diff_table("Removed ingredients", ("Ingredient", "Taxonomy key"), ingredient_rows(previous, diff.removed))
        + diff_table("ID changes", ("Taxonomy key", "Before", "After"), diff.identity_changes)
        + diff_table("Name changes", ("Ingredient", "Language", "Before", "After"), diff.name_changes)
        + diff_table("Nutrition changes", ("Ingredient", "Field", "Before", "After"), diff.nutrition_changes)
        + "</main>"
    )
    return document(f"{current.tag} release diff", body)


def ingredient_rows(catalog: ReleaseCatalog | None, ingredient_ids: tuple[str, ...]):
    if catalog is None:
        return ()
    return tuple((ingredient_id, catalog.identities[ingredient_id]) for ingredient_id in ingredient_ids)


def summary_card(label: str, value: int, detail: str) -> str:
    return f"<article><span>{escape(label)}</span><strong>{value}</strong><small>{escape(detail)}</small></article>"


def diff_table(title: str, columns: tuple[str, ...], rows) -> str:
    rows = tuple(rows)
    heading = "".join(f"<th>{escape(column)}</th>" for column in columns)
    content = "".join("<tr>" + "".join(f"<td>{escape(value) or '<span class=\"empty\">empty</span>'}</td>" for value in row) + "</tr>" for row in rows)
    if not content:
        content = f"<tr><td colspan=\"{len(columns)}\" class=\"empty-state\">No changes</td></tr>"
    return f"<section class=\"panel\"><h2>{escape(title)} <span class=\"count\">{len(rows)}</span></h2><div class=\"table-wrap\"><table><thead><tr>{heading}</tr></thead><tbody>{content}</tbody></table></div></section>"


def escape(value) -> str:
    return html.escape(str(value), quote=True)


def document(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(title)}</title><style>
:root{{--bg:#ecfeff;--surface:#fff;--text:#164e63;--muted:#475569;--line:#bae6fd;--primary:#0e7490;--good:#166534;--bad:#991b1b}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--text);font:16px/1.5 system-ui,sans-serif}}header,main,nav{{width:min(1180px,calc(100% - 32px));margin:auto}}nav{{padding-top:24px}}a{{color:var(--primary);font-weight:700}}a:focus-visible{{outline:3px solid #22c55e;outline-offset:3px}}header{{padding:52px 0 28px}}h1{{font-size:clamp(2rem,6vw,4.5rem);line-height:1;margin:.2em 0}}h2{{font-size:1.15rem;margin:0 0 18px}}.eyebrow{{font:700 .78rem ui-monospace,monospace;letter-spacing:.12em;text-transform:uppercase}}main{{padding-bottom:64px}}.panel,.summary article{{background:var(--surface);border:1px solid var(--line);box-shadow:0 8px 24px rgba(14,116,144,.08)}}.panel{{margin:16px 0;padding:20px;border-radius:12px}}.summary{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:24px}}.summary.initial{{grid-template-columns:minmax(0,280px)}}.summary article{{padding:18px;border-radius:10px}}.summary span,.summary small{{display:block;color:var(--muted)}}.summary strong{{display:block;font:700 2rem ui-monospace,monospace;margin:4px 0}}.count{{display:inline-block;padding:2px 8px;border-radius:999px;background:#cffafe;font:600 .8rem ui-monospace,monospace}}.table-wrap{{overflow:auto}}table{{width:100%;border-collapse:collapse;min-width:680px}}th,td{{padding:11px 12px;text-align:left;border-bottom:1px solid #e2e8f0;vertical-align:top}}th{{font-size:.75rem;text-transform:uppercase;letter-spacing:.06em;color:var(--muted)}}td{{font-family:ui-monospace,monospace;font-size:.86rem}}tbody tr:hover{{background:#f0fdfa}}.positive{{color:var(--good)}}.negative{{color:var(--bad)}}.empty,.empty-state{{color:var(--muted);font-style:italic}}.empty-state{{text-align:center;padding:28px}}@media(max-width:760px){{header{{padding-top:32px}}.summary{{grid-template-columns:1fr 1fr}}.summary.initial{{grid-template-columns:minmax(0,280px)}}.panel{{padding:14px}}}}@media(max-width:420px){{.summary,.summary.initial{{grid-template-columns:1fr}}}}@media(prefers-reduced-motion:reduce){{*{{scroll-behavior:auto!important}}}}
</style></head><body>{body}</body></html>"""
