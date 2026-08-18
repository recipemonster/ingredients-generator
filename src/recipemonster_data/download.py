from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import zipfile
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .config import ALLOWED_HOSTS, Asset, Source, UpdateProbe


class TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def text(self) -> str:
        return " ".join(" ".join(self.parts).split())


class SafeRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        parsed = urlparse(new_url)
        if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS or parsed.username or parsed.port:
            raise ValueError("download redirect points to an unapproved URL")
        return super().redirect_request(request, file_pointer, code, message, headers, new_url)


def download_sources(
    sources: tuple[Source, ...],
    raw_directory: Path,
    local_files: dict[str, Path],
) -> dict[str, object]:
    raw_directory.mkdir(parents=True, exist_ok=True)
    lock_path = raw_directory / "download-lock.json"
    observed: dict[tuple[str, str], dict[str, object]] = {}
    for source in sources:
        for asset in source.assets:
            local_file = local_files.get(f"{source.source_id}/{asset.asset_id}") or local_files.get(source.source_id)
            try:
                observed[(source.source_id, asset.asset_id)] = acquire_asset(source, asset, raw_directory, local_file)
            except (HTTPError, URLError) as error:
                if asset.manual_download:
                    raise RuntimeError(
                        f"download of {source.source_id}/{asset.asset_id} was blocked; download it from "
                        f"{source.homepage} and pass --file {source.source_id}=/path/to/{asset.file}"
                    ) from error
                raise
    update_download_lock(lock_path, observed)
    return json.loads(lock_path.read_text(encoding="utf-8"))


def acquire_asset(
    source: Source,
    asset: Asset,
    raw_directory: Path,
    local_file: Path | None,
    verify_pinned_checksum: bool = True,
) -> dict[str, object]:
    destination = raw_directory / asset.file
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{asset.file}.", dir=raw_directory)
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        if local_file:
            with local_file.open("rb") as input_file, temporary.open("wb") as output_file:
                digest, size = copy_limited(input_file, output_file, asset.maximum_bytes)
        else:
            request = Request(
                asset.url,
                headers={
                    "Accept": "application/zip, application/xml, text/plain, text/xml, application/octet-stream",
                    "User-Agent": "RecipeMonster data builder",
                },
            )
            with build_opener(SafeRedirectHandler()).open(request, timeout=600) as response, temporary.open("wb") as output_file:
                content_length = int(response.headers.get("Content-Length", "0") or 0)
                if content_length > asset.maximum_bytes:
                    raise ValueError(f"asset {source.source_id}/{asset.asset_id} exceeds its size limit")
                digest, size = copy_limited(response, output_file, asset.maximum_bytes)
        if verify_pinned_checksum and asset.sha256 and digest != asset.sha256:
            raise ValueError(f"asset {source.source_id}/{asset.asset_id} does not match its pinned SHA-256")
        validate_asset_format(temporary, asset)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return {
        "sourceId": source.source_id,
        "assetId": asset.asset_id,
        "file": asset.file,
        "sha256": digest,
        "bytes": size,
    }


def refresh_sources_manifest(
    manifest_path: Path,
    sources: tuple[Source, ...],
    raw_directory: Path,
    snapshot_date: date | None = None,
) -> tuple[str, ...]:
    raw_directory.mkdir(parents=True, exist_ok=True)
    observed: dict[tuple[str, str], dict[str, object]] = {}
    observed_probes: dict[str, str] = {}
    for source in sources:
        if source.update_probe is not None:
            observed_probes[source.source_id] = acquire_update_probe(source.source_id, source.update_probe)
        for asset in source.assets:
            if asset.manual_download:
                continue
            observed[(source.source_id, asset.asset_id)] = acquire_asset(
                source,
                asset,
                raw_directory,
                None,
                verify_pinned_checksum=False,
            )
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    changed_sources = update_source_checksums(payload, observed, snapshot_date or date.today())
    changed_sources = merge_changed_sources(changed_sources, update_source_probes(payload, observed_probes))
    if changed_sources:
        manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    update_download_lock(raw_directory / "download-lock.json", observed)
    return changed_sources


def acquire_update_probe(source_id: str, probe: UpdateProbe) -> str:
    request = Request(
        probe.url,
        headers={
            "Accept": "text/html,application/xhtml+xml",
            "User-Agent": "RecipeMonster data builder",
        },
    )
    with build_opener(SafeRedirectHandler()).open(request, timeout=60) as response:
        content_length = int(response.headers.get("Content-Length", "0") or 0)
        if content_length > probe.maximum_bytes:
            raise ValueError(f"update probe for {source_id} exceeds its size limit")
        body = response.read(probe.maximum_bytes + 1)
    if len(body) > probe.maximum_bytes:
        raise ValueError(f"update probe for {source_id} exceeds its size limit")
    return extract_probe_value(source_id, probe, body)


def extract_probe_value(source_id: str, probe: UpdateProbe, body: bytes) -> str:
    extractor = TextExtractor()
    extractor.feed(body.decode("utf-8", errors="replace"))
    match = re.search(probe.pattern, extractor.text())
    if match is None or match.lastindex != 1:
        raise ValueError(f"update probe for {source_id} did not return one version value")
    return match.group(1)


def update_source_probes(payload: dict[str, object], observed: dict[str, str]) -> tuple[str, ...]:
    changed_sources: list[str] = []
    for source in payload.get("sources", []):
        source_id = str(source.get("id", ""))
        value = observed.get(source_id)
        probe = source.get("updateProbe")
        if value is None or not isinstance(probe, dict) or probe.get("value") == value:
            continue
        probe["value"] = value
        changed_sources.append(source_id)
    return tuple(changed_sources)


def merge_changed_sources(*groups: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(source_id for group in groups for source_id in group))


def update_source_checksums(
    payload: dict[str, object],
    observed: dict[tuple[str, str], dict[str, object]],
    snapshot_date: date,
) -> tuple[str, ...]:
    changed_sources: list[str] = []
    for source in payload.get("sources", []):
        source_id = str(source.get("id", ""))
        source_changed = False
        for asset in source.get("assets", []):
            identity = source_id, str(asset.get("id", ""))
            result = observed.get(identity)
            if result is None:
                continue
            checksum = str(result["sha256"])
            if asset.get("sha256") != checksum:
                asset["sha256"] = checksum
                source_changed = True
        if source_changed:
            source["version"] = snapshot_date.isoformat()
            changed_sources.append(source_id)
    return tuple(changed_sources)


def update_download_lock(lock_path: Path, observed: dict[tuple[str, str], dict[str, object]]) -> None:
    existing = json.loads(lock_path.read_text(encoding="utf-8")) if lock_path.exists() else {"assets": []}
    locked_assets = {
        (str(item["sourceId"]), str(item["assetId"])): item
        for item in existing.get("assets", [])
    }
    locked_assets.update(observed)
    write_json(
        lock_path,
        {
            "schemaVersion": 1,
            "assets": sorted(locked_assets.values(), key=lambda item: (str(item["sourceId"]), str(item["assetId"]))),
        },
    )


def copy_limited(input_file, output_file, maximum_bytes: int) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    while chunk := input_file.read(1024 * 1024):
        size += len(chunk)
        if size > maximum_bytes:
            raise ValueError("asset exceeds its size limit")
        digest.update(chunk)
        output_file.write(chunk)
    if size == 0:
        raise ValueError("asset is empty")
    return digest.hexdigest(), size


def validate_asset_format(path: Path, asset: Asset) -> None:
    if asset.format == "zip":
        if not zipfile.is_zipfile(path):
            raise ValueError(f"asset {asset.source_id}/{asset.asset_id} is not a ZIP archive")
        with zipfile.ZipFile(path) as archive:
            total_size = 0
            for entry in archive.infolist():
                if Path(entry.filename).is_absolute() or ".." in Path(entry.filename).parts:
                    raise ValueError(f"asset {asset.source_id}/{asset.asset_id} contains an unsafe ZIP path")
                total_size += entry.file_size
                if total_size > asset.maximum_bytes * 16:
                    raise ValueError(f"asset {asset.source_id}/{asset.asset_id} expands beyond its limit")
        return
    with path.open("rb") as file:
        prefix = file.read(256).lstrip(b"\xef\xbb\xbf\x00\t\r\n ")
    if asset.format == "xml" and not prefix.startswith(b"<?xml") and not prefix.startswith(b"<"):
        raise ValueError(f"asset {asset.source_id}/{asset.asset_id} is not XML")
    if asset.format == "text" and b"\x00" in prefix:
        raise ValueError(f"asset {asset.source_id}/{asset.asset_id} is not text")


def verify_downloads(sources: tuple[Source, ...], raw_directory: Path) -> dict[str, object]:
    lock_path = raw_directory / "download-lock.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    if lock.get("schemaVersion") != 1:
        raise ValueError("download lock must use schema version 1")
    entries = {(entry["sourceId"], entry["assetId"]): entry for entry in lock.get("assets", [])}
    for source in sources:
        for asset in source.assets:
            entry = entries.get((source.source_id, asset.asset_id))
            if not entry or entry.get("file") != asset.file:
                raise ValueError(f"download lock is missing {source.source_id}/{asset.asset_id}")
            path = raw_directory / asset.file
            if not path.is_file() or path.stat().st_size > asset.maximum_bytes:
                raise ValueError(f"downloaded asset is missing or too large: {asset.file}")
            digest = hash_file(path)
            if digest != entry.get("sha256") or (asset.sha256 and digest != asset.sha256):
                raise ValueError(f"downloaded asset checksum changed: {asset.file}")
    return lock


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
