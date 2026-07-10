from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import stackchan_match_setup as setup


SCOREBOARD = {
    "events": [
        {
            "id": "760511",
            "date": "2026-07-10T19:00Z",
            "competitions": [
                {
                    "id": "760511",
                    "date": "2026-07-10T19:00Z",
                    "status": {"type": {"state": "pre", "description": "Scheduled"}},
                    "competitors": [
                        {
                            "homeAway": "home",
                            "team": {"displayName": "Spain", "abbreviation": "ESP"},
                        },
                        {
                            "homeAway": "away",
                            "team": {"displayName": "Belgium", "abbreviation": "BEL"},
                        },
                    ],
                    "venue": {"fullName": "SoFi Stadium"},
                }
            ],
        }
    ]
}


KALSHI_EVENT = {
    "event": {
        "event_ticker": "KXWCADVANCE-26JUL10ESPBEL",
        "title": "Spain vs Belgium",
        "markets": [
            {
                "ticker": "KXWCADVANCE-26JUL10ESPBEL-ESP",
                "yes_sub_title": "Spain advances",
                "status": "active",
            },
            {
                "ticker": "KXWCADVANCE-26JUL10ESPBEL-BEL",
                "yes_sub_title": "Belgium advances",
                "status": "active",
            },
        ],
    }
}


def service(path: Path, language: str = "zh") -> setup.MatchSetupService:
    return setup.MatchSetupService(
        path,
        "https://kalshi.example/trade-api/v2",
        "https://espn.example/soccer",
        "fifa.world",
        language=language,
    )


class MatchSetupTests(unittest.TestCase):
    def test_extracts_event_ticker_from_kalshi_url(self):
        value = setup.extract_kalshi_event_ticker(
            "https://kalshi.com/markets/kxwcadvance/world-cup-advance/"
            "kxwcadvance-26jul10espbel"
        )

        self.assertEqual(value, "KXWCADVANCE-26JUL10ESPBEL")

    def test_scoreboard_localizes_teams_and_preserves_utc_start(self):
        matches = setup.parse_scoreboard(
            SCOREBOARD,
            now=datetime(2026, 7, 9, 20, tzinfo=timezone.utc),
        )

        self.assertEqual(matches[0]["label"], "西班牙 vs 比利时")
        self.assertEqual(matches[0]["home"]["flag"], "es")
        self.assertEqual(matches[0]["away"]["color"], "#EF3340")
        self.assertEqual(matches[0]["starts_at"], "2026-07-10T19:00:00+00:00")

    def test_resolver_recommends_exact_espn_team_match(self):
        instance = service(Path("unused.json"))
        with patch.object(instance, "_kalshi_event", return_value=setup.event_markets(KALSHI_EVENT)):
            with patch.object(
                instance,
                "upcoming_matches",
                return_value=setup.parse_scoreboard(
                    SCOREBOARD,
                    now=datetime(2026, 7, 9, 20, tzinfo=timezone.utc),
                ),
            ):
                result = instance.resolve_kalshi("KXWCADVANCE-26JUL10ESPBEL")

        self.assertEqual(result["recommended_event_id"], "760511")
        self.assertEqual(
            [team["market_ticker"] for team in result["teams"]],
            ["KXWCADVANCE-26JUL10ESPBEL-ESP", "KXWCADVANCE-26JUL10ESPBEL-BEL"],
        )

    def test_apply_updates_markets_bar_and_fan_position_atomically(self):
        initial = {
            "probability_bar": {},
            "espn": {"team_names": {}, "team_colors": {}},
            "setup_server": {},
            "markets": [
                {
                    "ticker": "OLD",
                    "alert_move_cents": 5,
                    "goal_signal_cooldown_seconds": 90,
                }
            ],
        }
        match = setup.parse_scoreboard(
            SCOREBOARD,
            now=datetime(2026, 7, 9, 20, tzinfo=timezone.utc),
        )[0]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "watch.json"
            path.write_text(json.dumps(initial), encoding="utf-8")
            instance = service(path)
            with patch.object(
                instance,
                "_kalshi_event",
                return_value=setup.event_markets(KALSHI_EVENT),
            ):
                with patch.object(instance, "_espn_match", return_value=match):
                    result = instance.apply_selection(
                        {
                            "event_ticker": "KXWCADVANCE-26JUL10ESPBEL",
                            "espn_event_id": "760511",
                            "favorite_team": "Spain",
                            "position_team": "",
                            "language": "en",
                        }
                    )
            updated = json.loads(path.read_text(encoding="utf-8"))
            english_status = instance.current_status()

        self.assertTrue(result["ok"])
        self.assertEqual(result["language"], "en")
        self.assertEqual(result["label"], "Spain vs Belgium")
        self.assertEqual(updated["language"], "en")
        self.assertEqual(updated["espn"]["event_id"], "760511")
        self.assertEqual(updated["espn"]["starts_at"], "2026-07-10T19:00:00+00:00")
        self.assertEqual(updated["espn"]["favorite_team"], "Spain")
        self.assertEqual(updated["espn"]["position_team"], "")
        self.assertEqual(
            updated["espn"]["label"],
            {"zh": "西班牙 vs 比利时", "en": "Spain vs Belgium"},
        )
        self.assertEqual(
            updated["espn"]["team_names"]["Spain"],
            {"zh": "西班牙", "en": "Spain"},
        )
        self.assertEqual(updated["probability_bar"]["left_flag"], "es")
        self.assertEqual(updated["probability_bar"]["right_flag"], "be")
        self.assertEqual(len(updated["markets"]), 2)
        self.assertEqual(
            updated["markets"][0]["label"],
            {"zh": "西班牙晋级", "en": "Spain to advance"},
        )
        self.assertIn("西班牙", updated["markets"][0]["goal_signal_up_speech"]["zh"])
        self.assertIn("Spain", updated["markets"][0]["goal_signal_up_speech"]["en"])
        self.assertIn("Belgium", updated["markets"][0]["goal_signal_down_speech"]["en"])
        self.assertTrue(updated["markets"][0]["alerts_enabled"])
        self.assertFalse(updated["markets"][1]["alerts_enabled"])
        self.assertEqual(english_status["label"], "Spain vs Belgium")
        self.assertEqual(english_status["favorite_team"], "Spain")
        self.assertEqual(english_status["language"], "en")
        self.assertTrue(instance.take_reload_requested())

    def test_apply_rejects_unsupported_language_before_fetching(self):
        instance = service(Path("unused.json"))

        with self.assertRaisesRegex(ValueError, "language must be one of: zh, en"):
            instance.apply_selection({"language": "fr"})

    def test_setup_page_contains_mobile_form_controls(self):
        page = setup.setup_page_html()

        self.assertIn('name="viewport"', page)
        self.assertIn('id="favorite"', page)
        self.assertIn('id="position"', page)
        self.assertIn('name="language"', page)
        self.assertIn('value="zh"', page)
        self.assertIn('value="en"', page)
        self.assertIn('id="apply"', page)


if __name__ == "__main__":
    unittest.main()
