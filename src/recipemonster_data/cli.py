from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .build import build_catalog, build_catalog_draft
from .changelog import release_notes
from .config import Source, load_sources
from .download import download_sources
from .validate import validate_catalog


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

    build = subcommands.add_parser("build", help="build a deterministic RecipeMonster catalog")
    build.add_argument("--source", action="append")
    draft = subcommands.add_parser("draft", help="build an inspectable catalog from downloaded sources")
    draft.add_argument("--source", action="append")
    subcommands.add_parser("validate", help="validate the generated catalog and manifest")
    notes = subcommands.add_parser("release-notes", help="extract release notes from CHANGELOG.md")
    notes.add_argument("--tag", required=True)
    notes.add_argument("--output", type=Path, required=True)
    subcommands.add_parser("all", help="download, build and validate")
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
        sources = load_sources(root / "sources.json")
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
