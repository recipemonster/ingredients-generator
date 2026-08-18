from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path

from recipemonster_data.config import UpdateProbe
from recipemonster_data.download import (
    extract_probe_value,
    merge_changed_sources,
    update_download_lock,
    update_source_checksums,
    update_source_probes,
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

    def test_updates_manual_source_probe_without_claiming_new_dataset_version(self) -> None:
        payload = {
            "schemaVersion": 1,
            "sources": [
                {
                    "id": "fineli",
                    "version": "rolling-snapshot",
                    "updateProbe": {"value": "1/1/2025"},
                }
            ],
        }

        changed = update_source_probes(payload, {"fineli": "13/5/2026"})

        self.assertEqual(changed, ("fineli",))
        self.assertEqual(payload["sources"][0]["updateProbe"]["value"], "13/5/2026")
        self.assertEqual(payload["sources"][0]["version"], "rolling-snapshot")

    def test_combines_source_changes_once_in_detection_order(self) -> None:
        self.assertEqual(
            merge_changed_sources(("ciqual", "fineli"), ("fineli", "openfoodfacts")),
            ("ciqual", "fineli", "openfoodfacts"),
        )

    def test_extracts_update_value_from_visible_html_text(self) -> None:
        probe = UpdateProbe(
            url="https://www.suomi.fi/example",
            pattern=r"Updated:\s*(\d{1,2}/\d{1,2}/\d{4})",
            value="1/1/2025",
            maximum_bytes=1024,
        )

        value = extract_probe_value(
            "fineli",
            probe,
            b"<span>Updated: <!-- marker --></span>13/5/2026",
        )

        self.assertEqual(value, "13/5/2026")


def checksum_result(source_id: str, asset_id: str, checksum: str) -> dict[str, object]:
    return {
        "sourceId": source_id,
        "assetId": asset_id,
        "file": f"{source_id}.data",
        "sha256": checksum,
        "bytes": 1,
    }


if __name__ == "__main__":
    unittest.main()
