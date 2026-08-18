from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .build import build_catalog, build_catalog_draft
from .changelog import release_notes
from .config import Source, load_sources
from .download import download_sources, refresh_sources_manifest
from .pages import generate_pages, generate_preview
from .validate import validate_catalog
from .versioning import latest_version, next_patch_version, read_version_file, read_versions, validate_new_version


def parser() -> argparse.ArgumentParser:
    command_parser = argparse.ArgumentParser(prog="recipemonster-data")
    command_parser.add_argument("--root", type=Path, default=Path.cwd(), help="recipemonster-data directory")
    subcommands = command_parser.add_subparsers(dest="command", required=True)

    download = subcommands.add_parser("download", help="download and lock source datasets")
    download.add_argument("--source", action="append")
    download.add_argument(
        "--file",
        action="append",
        default=[],
        metavar="SOURCE[/ASSET]=PATH",
        help="use a manually downloaded source asset",
    )
    subcommands.add_parser("refresh-sources", help="check upstream datasets and update changed checksums")

    build = subcommands.add_parser("build", help="build a deterministic RecipeMonster catalog")
    build.add_argument("--source", action="append")
    build.add_argument("--previous-catalog", type=Path)
    draft = subcommands.add_parser("draft", help="build an inspectable catalog from downloaded sources")
    draft.add_argument("--source", action="append")
    draft.add_argument("--previous-catalog", type=Path)
    subcommands.add_parser("validate", help="validate the generated catalog and manifest")
    notes = subcommands.add_parser("release-notes", help="extract release notes from CHANGELOG.md")
    notes.add_argument("--tag", required=True)
    notes.add_argument("--output", type=Path, required=True)
    pages = subcommands.add_parser("pages", help="generate release history pages")
    pages.add_argument("--releases", type=Path, required=True)
    pages.add_argument("--output", type=Path, required=True)
    pages.add_argument("--candidate", type=Path)
    pages.add_argument("--candidate-tag")
    pages.add_argument("--pull-request", type=int)
    next_version = subcommands.add_parser("next-version", help="write the next default catalog version")
    next_version.add_argument("--published-tags", type=Path, required=True)
    next_version.add_argument("--output", type=Path, required=True)
    latest = subcommands.add_parser("latest-version", help="print the latest published catalog tag")
    latest.add_argument("--published-tags", type=Path, required=True)
    validate_version = subcommands.add_parser("validate-version", help="validate VERSION against published tags")
    validate_version.add_argument("--published-tags", type=Path, required=True)
    all_command = subcommands.add_parser("all", help="download, build and validate")
    all_command.add_argument("--previous-catalog", type=Path)
    return command_parser


def main(arguments: list[str] | None = None) -> int:
    args = parser().parse_args(arguments)
    root = args.root.resolve()
    try:
        if args.command == "release-notes":
            notes = release_notes((root / "CHANGELOG.md").read_text(encoding="utf-8"), args.tag)
            output = args.output.resolve()
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(notes, encoding="utf-8")
            return 0
        if args.command == "pages":
            generate_pages(args.releases.resolve(), args.output.resolve())
            preview_values = (args.candidate, args.candidate_tag, args.pull_request)
            if any(value is not None for value in preview_values):
                if not all(value is not None for value in preview_values):
                    raise ValueError("candidate, candidate tag and pull request must be provided together")
                generate_preview(
                    args.releases.resolve(),
                    args.candidate.resolve(),
                    args.output.resolve(),
                    args.candidate_tag,
                    args.pull_request,
                )
            return 0
        if args.command == "next-version":
            version = next_patch_version(read_versions(args.published_tags.resolve()))
            args.output.resolve().write_text(f"{version}\n", encoding="utf-8")
            print(version)
            return 0
        if args.command == "latest-version":
            version = latest_version(read_versions(args.published_tags.resolve()))
            if version is None:
                raise ValueError("no published catalog version exists")
            print(f"v{version}")
            return 0
        if args.command == "validate-version":
            version = read_version_file(root / "VERSION")
            validate_new_version(version, read_versions(args.published_tags.resolve()))
            print(f"v{version}")
            return 0
        sources = load_sources(root / "sources.json")
        if args.command == "refresh-sources":
            changed = refresh_sources_manifest(root / "sources.json", sources, root / "raw")
            print(json.dumps({"changedSources": changed}, ensure_ascii=False, sort_keys=True))
            return 0
        if args.command in ("download", "all"):
            selected = set(args.source or ()) if args.command == "download" else set()
            download_set = tuple(source for source in sources if not selected or source.source_id in selected)
            lock = download_sources(download_set, root / "raw", parse_local_files(args.file if args.command == "download" else []))
            print(json.dumps(lock, ensure_ascii=False, sort_keys=True))
            if args.command == "download":
                return 0
        if args.command in ("build", "all"):
            build_sources = sources if args.command == "all" else selected_downloaded_sources(
                sources,
                root / "raw",
                args.source,
            )
            manifest = build_catalog(
                build_sources,
                root / "raw",
                root / "dist",
                root / "mappings" / "nutrients.json",
                args.previous_catalog,
            )
            print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
            if args.command == "build":
                return 0
        if args.command == "draft":
            draft_sources = selected_downloaded_sources(sources, root / "raw", args.source)
            manifest = build_catalog_draft(
                draft_sources,
                root / "raw",
                root / "dist",
                root / "mappings" / "nutrients.json",
                args.previous_catalog,
            )
            print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
            return 0
        result = validate_catalog(root / "dist")
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except (OSError, ValueError, RuntimeError) as error:
        print(f"recipemonster-data: {error}", file=sys.stderr)
        return 1


def parse_local_files(values: list[str]) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for value in values:
        identity, separator, path = value.partition("=")
        if not separator or not identity or not path or identity in result:
            raise ValueError(f"invalid --file value: {value}")
        result[identity] = Path(path).expanduser().resolve(strict=True)
    return result


def source_is_downloaded(source: Source, raw_directory: Path) -> bool:
    lock_path = raw_directory / "download-lock.json"
    if not lock_path.is_file():
        return False
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    entries = {(entry.get("sourceId"), entry.get("assetId")) for entry in lock.get("assets", [])}
    return all(
        (source.source_id, asset.asset_id) in entries and (raw_directory / asset.file).is_file()
        for asset in source.assets
    )


def selected_downloaded_sources(
    sources: tuple[Source, ...],
    raw_directory: Path,
    selected_values: list[str] | None,
) -> tuple[Source, ...]:
    selected = set(selected_values or ())
    known = {source.source_id for source in sources}
    unknown = selected - known
    if unknown:
        raise ValueError(f"unknown source: {', '.join(sorted(unknown))}")
    selected.update(source.source_id for source in sources if source.role == "identity")
    available = tuple(
        source
        for source in sources
        if source_is_downloaded(source, raw_directory) and (not selected_values or source.source_id in selected)
    )
    missing = selected - {source.source_id for source in available}
    if missing:
        raise ValueError(f"selected source is not fully downloaded: {', '.join(sorted(missing))}")
    if not any(source.role == "identity" for source in available):
        raise ValueError("no fully downloaded identity taxonomy is available")
    if not any(source.role == "nutrition" for source in available):
        raise ValueError("no fully downloaded nutrition source is available")
    return available
