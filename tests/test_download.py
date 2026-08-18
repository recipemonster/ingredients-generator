from __future__ import annotations

import tempfile
import unittest
from datetime import date
from email.message import Message
from pathlib import Path

from recipemonster_data.config import Asset, Source, VersionCheck
from recipemonster_data.download import (
    extract_asset_version,
    merge_changed_sources,
    update_asset_versions,
    update_download_lock,
    update_source_checksums,
)


class DownloadTest(unittest.TestCase):
    def test_updates_only_sources_with_changed_content(self) -> None:
        payload = {
            "schemaVersion": 1,
            "sources": [
                {
                    "id": "changed",
                    "version": "2025-01-01",
                    "assets": [{"id": "data", "sha256": "old"}],
                },
                {
                    "id": "unchanged",
                    "version": "2025-01-01",
                    "assets": [{"id": "data", "sha256": "same"}],
                },
                {
                    "id": "manual",
                    "version": "2025-01-01",
                    "assets": [{"id": "data", "sha256": "manual"}],
                },
            ],
        }
        observed = {
            ("changed", "data"): checksum_result("changed", "data", "new"),
            ("unchanged", "data"): checksum_result("unchanged", "data", "same"),
        }

        changed = update_source_checksums(payload, observed, date(2026, 8, 18))

        self.assertEqual(changed, ("changed",))
        self.assertEqual(payload["sources"][0]["version"], "2026-08-18")
        self.assertEqual(payload["sources"][0]["assets"][0]["sha256"], "new")
        self.assertEqual(payload["sources"][1]["version"], "2025-01-01")
        self.assertEqual(payload["sources"][2]["version"], "2025-01-01")

    def test_download_lock_keeps_unobserved_manual_assets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "download-lock.json"
            path.write_text(
                '{"schemaVersion":1,"assets":[{"sourceId":"manual","assetId":"data","sha256":"old"}]}',
                encoding="utf-8",
            )

            update_download_lock(path, {("automatic", "data"): checksum_result("automatic", "data", "new")})

            contents = path.read_text(encoding="utf-8")
            self.assertIn('"sourceId": "manual"', contents)
            self.assertIn('"sourceId": "automatic"', contents)

    def test_updates_asset_version_without_claiming_new_dataset_content(self) -> None:
        payload = {
            "schemaVersion": 1,
            "sources": [
                {
                    "id": "fineli",
                    "version": "rolling-snapshot",
                    "assets": [
                        {
                            "id": "archive",
                            "versionCheck": {"field": "filename", "value": "Fineli_Rel20.zip"},
                        }
                    ],
                }
            ],
        }

        changed = update_asset_versions(payload, {("fineli", "archive"): "Fineli_Rel21.zip"})

        self.assertEqual(changed, ("fineli",))
        self.assertEqual(
            payload["sources"][0]["assets"][0]["versionCheck"]["value"],
            "Fineli_Rel21.zip",
        )
        self.assertEqual(payload["sources"][0]["version"], "rolling-snapshot")

    def test_combines_source_changes_once_in_detection_order(self) -> None:
        self.assertEqual(
            merge_changed_sources(("ciqual", "fineli"), ("fineli", "openfoodfacts")),
            ("ciqual", "fineli", "openfoodfacts"),
        )

    def test_extracts_dataset_release_from_redirect_filename(self) -> None:
        source, asset = test_source("filename", "Fineli_Rel20.zip")

        value = extract_asset_version(
            source,
            asset,
            Message(),
            "https://fineli.fi/fineli/content/file/49/Fineli_Rel21.zip",
        )

        self.assertEqual(value, "Fineli_Rel21.zip")

    def test_extracts_dataset_release_from_etag_without_reading_body(self) -> None:
        source, asset = test_source("etag", '"old"')
        headers = Message()
        headers["ETag"] = '"new"'

        value = extract_asset_version(source, asset, headers, asset.url)

        self.assertEqual(value, '"new"')


def checksum_result(source_id: str, asset_id: str, checksum: str) -> dict[str, object]:
    return {
        "sourceId": source_id,
        "assetId": asset_id,
        "file": f"{source_id}.data",
        "sha256": checksum,
        "bytes": 1,
    }


def test_source(field: str, value: str) -> tuple[Source, Asset]:
    asset = Asset(
        source_id="fineli",
        asset_id="archive",
        url="https://fineli.fi/fineli/content/file/49",
        file="fineli.zip",
        format="zip",
        sha256="",
        maximum_bytes=10_000_000,
        manual_download=True,
        version_check=VersionCheck(field=field, value=value),
    )
    source = Source(
        source_id="fineli",
        role="nutrition",
        name="Fineli",
        version="rolling-snapshot",
        homepage="https://fineli.fi",
        attribution="THL",
        license={"name": "test", "spdx": "test", "url": "https://example.test"},
        assets=(asset,),
    )
    return source, asset


if __name__ == "__main__":
    unittest.main()
