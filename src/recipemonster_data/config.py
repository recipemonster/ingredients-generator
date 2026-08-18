from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

ALLOWED_HOSTS = {
    "entrepot.recherche.data.gouv.fr",
    "fdc.nal.usda.gov",
    "fineli.fi",
    "prod-espace-generique.opens3r-tls.stockage.inrae.fr",
    "raw.githubusercontent.com",
    "www.suomi.fi",
}


@dataclass(frozen=True)
class Asset:
    source_id: str
    asset_id: str
    url: str
    file: str
    format: str
    sha256: str
    maximum_bytes: int
    manual_download: bool


@dataclass(frozen=True)
class UpdateProbe:
    url: str
    pattern: str
    value: str
    maximum_bytes: int


@dataclass(frozen=True)
class Source:
    source_id: str
    role: str
    name: str
    version: str
    homepage: str
    attribution: str
    license: dict[str, str]
    assets: tuple[Asset, ...]
    update_probe: UpdateProbe | None = None


def load_sources(path: Path) -> tuple[Source, ...]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schemaVersion") != 1 or not payload.get("sources"):
        raise ValueError("sources.json must use schema version 1 and contain sources")

    sources: list[Source] = []
    source_ids: set[str] = set()
    files: set[str] = set()
    for raw_source in payload["sources"]:
        source_id = required_string(raw_source, "id")
        if source_id in source_ids:
            raise ValueError(f"duplicate source: {source_id}")
        source_ids.add(source_id)
        role = required_string(raw_source, "role")
        if role not in {"identity", "nutrition"}:
            raise ValueError(f"source {source_id} has an unsupported role")
        license_data = raw_source.get("license", {})
        for field in ("name", "spdx", "url"):
            required_string(license_data, field)

        assets: list[Asset] = []
        for raw_asset in raw_source.get("assets", []):
            asset_id = required_string(raw_asset, "id")
            filename = required_string(raw_asset, "file")
            if Path(filename).name != filename or filename in files:
                raise ValueError(f"invalid or duplicate asset filename: {filename}")
            files.add(filename)
            url = required_string(raw_asset, "url")
            parsed = urlparse(url)
            if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS or parsed.username or parsed.port:
                raise ValueError(f"asset {source_id}/{asset_id} uses an unapproved URL")
            checksum = str(raw_asset.get("sha256", "")).lower()
            if checksum and not valid_sha256(checksum):
                raise ValueError(f"asset {source_id}/{asset_id} has an invalid SHA-256")
            maximum_bytes = int(raw_asset.get("maximumBytes", 0))
            if maximum_bytes <= 0:
                raise ValueError(f"asset {source_id}/{asset_id} has no size limit")
            asset_format = required_string(raw_asset, "format")
            if asset_format not in {"text", "zip", "xml"}:
                raise ValueError(f"asset {source_id}/{asset_id} has an unsupported format")
            assets.append(
                Asset(
                    source_id=source_id,
                    asset_id=asset_id,
                    url=url,
                    file=filename,
                    format=asset_format,
                    sha256=checksum,
                    maximum_bytes=maximum_bytes,
                    manual_download=bool(raw_asset.get("manualDownload", False)),
                )
            )
        if not assets:
            raise ValueError(f"source {source_id} has no assets")
        update_probe = load_update_probe(source_id, raw_source.get("updateProbe"))
        sources.append(
            Source(
                source_id=source_id,
                role=role,
                name=required_string(raw_source, "name"),
                version=required_string(raw_source, "version"),
                homepage=required_string(raw_source, "homepage"),
                attribution=required_string(raw_source, "attribution"),
                license={key: str(value) for key, value in license_data.items()},
                assets=tuple(assets),
                update_probe=update_probe,
            )
        )
    return tuple(sources)


def load_update_probe(source_id: str, raw_probe: object) -> UpdateProbe | None:
    if raw_probe is None:
        return None
    if not isinstance(raw_probe, dict):
        raise ValueError(f"source {source_id} has an invalid update probe")
    url = required_string(raw_probe, "url")
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS or parsed.username or parsed.port:
        raise ValueError(f"source {source_id} update probe uses an unapproved URL")
    maximum_bytes = int(raw_probe.get("maximumBytes", 0))
    if maximum_bytes <= 0:
        raise ValueError(f"source {source_id} update probe has no size limit")
    return UpdateProbe(
        url=url,
        pattern=required_string(raw_probe, "pattern"),
        value=required_string(raw_probe, "value"),
        maximum_bytes=maximum_bytes,
    )


def load_nutrient_mappings(path: Path) -> dict[tuple[str, str], str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schemaVersion") != 1:
        raise ValueError("nutrient mappings must use schema version 1")
    mappings: dict[tuple[str, str], str] = {}
    for item in payload.get("mappings", []):
        identity = (required_string(item, "source"), required_string(item, "sourceId"))
        if identity in mappings:
            raise ValueError(f"duplicate nutrient mapping: {identity[0]}/{identity[1]}")
        mappings[identity] = required_string(item, "key")
    return mappings


def load_identity_links(path: Path) -> list[dict[str, object]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schemaVersion") != 1:
        raise ValueError("identity links must use schema version 1")
    return list(payload.get("links", []))


def required_string(payload: dict[str, object], key: str) -> str:
    value = str(payload.get(key, "")).strip()
    if not value:
        raise ValueError(f"missing required field: {key}")
    return value


def valid_sha256(value: str) -> bool:
    return len(value) == hashlib.sha256().digest_size * 2 and all(character in "0123456789abcdef" for character in value)
