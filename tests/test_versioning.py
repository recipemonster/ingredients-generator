from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from recipemonster_data.versioning import (
    Version,
    latest_version,
    next_patch_version,
    parse_version,
    read_version_file,
    read_versions,
    validate_new_version,
    validate_release_tag,
)


class VersioningTest(unittest.TestCase):
    def test_release_candidate_is_followed_by_stable_version(self) -> None:
        versions = (parse_version("v0.1.0-rc1"),)

        self.assertEqual(next_patch_version(versions), Version(major=0, minor=1, patch=0))

    def test_stable_version_is_followed_by_patch_version(self) -> None:
        versions = (parse_version("v0.1.0"), parse_version("v0.1.1-rc2"))

        self.assertEqual(latest_version(versions), parse_version("v0.1.1-rc2"))
        self.assertEqual(next_patch_version(versions), parse_version("v0.1.1"))

    def test_rejects_existing_or_older_version(self) -> None:
        published = (parse_version("v0.1.0"), parse_version("v0.2.0-rc1"))

        with self.assertRaisesRegex(ValueError, "already published"):
            validate_new_version(parse_version("0.1.0"), published)
        with self.assertRaisesRegex(ValueError, "must be newer"):
            validate_new_version(parse_version("0.1.1"), published)

    def test_accepts_newer_release_candidate(self) -> None:
        validate_new_version(parse_version("0.2.0-rc2"), (parse_version("v0.2.0-rc1"),))

    def test_reads_tags_and_single_line_version_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "tags.txt").write_text("v0.1.0-rc1\nv0.1.0\n", encoding="utf-8")
            (root / "VERSION").write_text("0.1.1\n", encoding="utf-8")

            self.assertEqual(read_versions(root / "tags.txt"), (parse_version("0.1.0-rc1"), parse_version("0.1.0")))
            self.assertEqual(read_version_file(root / "VERSION"), parse_version("0.1.1"))

    def test_stable_version_accepts_stable_and_release_candidate_tags(self) -> None:
        version = parse_version("0.1.0")

        self.assertEqual(validate_release_tag(version, "v0.1.0"), parse_version("0.1.0"))
        self.assertEqual(validate_release_tag(version, "v0.1.0-rc1"), parse_version("0.1.0-rc1"))
        self.assertEqual(validate_release_tag(version, "v0.1.0-rc27"), parse_version("0.1.0-rc27"))

    def test_rejects_tag_for_another_base_version(self) -> None:
        with self.assertRaisesRegex(ValueError, "does not match VERSION"):
            validate_release_tag(parse_version("0.1.0"), "v0.1.1-rc1")

    def test_version_file_rejects_release_candidate_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "VERSION"
            path.write_text("0.1.0-rc1\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "stable base version"):
                read_version_file(path)

    def test_rejects_invalid_versions(self) -> None:
        for value in ("1", "1.2", "1.2.3-rc.1", "1.2.3-rc0", "v1.2.3-beta"):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "invalid semantic version"):
                parse_version(value)


if __name__ == "__main__":
    unittest.main()
