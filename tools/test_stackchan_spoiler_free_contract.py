#!/usr/bin/env python3
"""Cross-file release contract for spoiler protection."""

from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class SpoilerFreeReleaseContractTests(unittest.TestCase):
    def test_default_is_opt_in_and_mod_version_is_documented(self):
        example = json.loads(
            (ROOT / "config" / "kalshi_watchlist.example.json").read_text(
                encoding="utf-8"
            )
        )
        mod_state = (ROOT / "mod" / "state.js").read_text(encoding="utf-8")
        readmes = "\n".join(
            (ROOT / name).read_text(encoding="utf-8")
            for name in ("README.md", "README.zh-CN.md")
        )

        self.assertIs(example["spoiler_free_mode"], False)
        self.assertIn("MOD_VERSION = '1.6.0'", mod_state)
        self.assertIn("docs/releases/1.6.0.md", readmes)

    def test_dedicated_endpoints_exist_in_code_and_api_docs(self):
        setup = (ROOT / "tools" / "stackchan_match_setup.py").read_text(
            encoding="utf-8"
        )
        mod_web = (ROOT / "mod" / "web.js").read_text(encoding="utf-8")
        documents = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (
                ROOT / "docs" / "device-api.md",
                ROOT / "docs" / "device-api.zh-CN.md",
                ROOT / "docs" / "releases" / "1.6.0.md",
            )
        )

        self.assertIn('path == "/api/setup/spoiler"', setup)
        self.assertIn("server.post('/api/match-setup/spoiler'", mod_web)
        self.assertIn("POST /api/setup/spoiler", documents)
        self.assertIn("POST /api/match-setup/spoiler", documents)

    def test_docs_keep_confirmed_espn_and_passive_market_display(self):
        documents = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (
                ROOT / "docs" / "configuration.md",
                ROOT / "docs" / "configuration.zh-CN.md",
                ROOT / "docs" / "releases" / "1.6.0.md",
            )
        ).casefold()

        self.assertIn("confirmed espn", documents)
        self.assertIn("概率条", documents)
        self.assertIn("ticker", documents)


if __name__ == "__main__":
    unittest.main()
