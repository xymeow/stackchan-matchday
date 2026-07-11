#!/usr/bin/env python3
"""Cross-file release contract for the three commentary styles."""

from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import stackchan_kalshi_watch as watcher  # noqa: E402
import stackchan_match_setup as setup  # noqa: E402


EXPECTED_STYLES = {"casual", "balanced", "professional"}


class CommentaryStyleReleaseContractTests(unittest.TestCase):
    def test_style_vocabulary_and_example_default_stay_in_sync(self):
        example = json.loads(
            (ROOT / "config" / "kalshi_watchlist.example.json").read_text(
                encoding="utf-8"
            )
        )
        mod_state = (ROOT / "mod" / "state.js").read_text(encoding="utf-8")

        self.assertEqual(set(watcher.COMMENTARY_STYLES), EXPECTED_STYLES)
        self.assertEqual(set(setup.COMMENTARY_STYLES), EXPECTED_STYLES)
        self.assertEqual(example["espn"]["commentary_style"], "balanced")
        for style in EXPECTED_STYLES:
            self.assertIn(f"'{style}'", mod_state)

    def test_documented_style_endpoints_exist_in_both_services(self):
        watcher_setup = (ROOT / "tools" / "stackchan_match_setup.py").read_text(
            encoding="utf-8"
        )
        mod_web = (ROOT / "mod" / "web.js").read_text(encoding="utf-8")
        documents = [
            (ROOT / "docs" / "device-api.md").read_text(encoding="utf-8"),
            (ROOT / "docs" / "device-api.zh-CN.md").read_text(encoding="utf-8"),
            (ROOT / "docs" / "commentary-styles-prd.md").read_text(encoding="utf-8"),
            (ROOT / "docs" / "releases" / "1.4.0.md").read_text(encoding="utf-8"),
        ]

        self.assertIn('path == "/api/setup/style"', watcher_setup)
        self.assertIn("server.post('/api/match-setup/style'", mod_web)
        for document in documents:
            self.assertIn("POST /api/setup/style", document)
            self.assertIn("POST /api/match-setup/style", document)

    def test_mod_version_and_release_notes_stay_aligned(self):
        mod_state = (ROOT / "mod" / "state.js").read_text(encoding="utf-8")
        release_notes = (ROOT / "docs" / "releases" / "1.5.0.md").read_text(
            encoding="utf-8"
        )
        readmes = "\n".join(
            (ROOT / name).read_text(encoding="utf-8")
            for name in ("README.md", "README.zh-CN.md")
        )

        match = re.search(r"MOD_VERSION\s*=\s*'([^']+)'", mod_state)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), "1.5.0")
        self.assertIn("# Matchday MOD 1.5.0", release_notes)
        self.assertIn("docs/releases/1.5.0.md", readmes)


if __name__ == "__main__":
    unittest.main()
