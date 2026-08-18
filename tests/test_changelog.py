from __future__ import annotations

import unittest

from recipemonster_data.changelog import parse_changelog, release_notes


class ChangelogTest(unittest.TestCase):
    def test_release_candidate_uses_version_notes(self) -> None:
        changelog = "# Changelog\n\n## Unreleased\n\n- Next change\n\n## v0.1.0\n\n- First release\n"

        self.assertEqual(release_notes(changelog, "v0.1.0-rc1"), "- First release\n")

    def test_release_candidate_falls_back_to_unreleased(self) -> None:
        changelog = "# Changelog\n\n## Unreleased\n\n- Next release\n\n## v0.1.0\n\n- First release\n"

        self.assertEqual(release_notes(changelog, "v0.2.0-rc3"), "- Next release\n")

    def test_stable_release_falls_back_to_unreleased(self) -> None:
        changelog = "# Changelog\n\n## Unreleased\n\n- Next release\n"

        self.assertEqual(release_notes(changelog, "v0.2.0"), "- Next release\n")

    def test_rejects_release_candidate_heading(self) -> None:
        changelog = "# Changelog\n\n## Unreleased\n\n- Next release\n\n## v0.1.0-rc1\n\n- Candidate\n"

        with self.assertRaisesRegex(ValueError, "invalid changelog release heading"):
            parse_changelog(changelog)

    def test_rejects_invalid_tag(self) -> None:
        with self.assertRaisesRegex(ValueError, "invalid release tag"):
            release_notes("# Changelog\n\n## Unreleased\n\n- Next release\n", "v0.1-rc1")


if __name__ == "__main__":
    unittest.main()
