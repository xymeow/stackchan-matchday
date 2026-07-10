from __future__ import annotations

import json
import tempfile
import threading
import time
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
                            "commentary_style": "professional",
                        }
                    )
            updated = json.loads(path.read_text(encoding="utf-8"))
            english_status = instance.current_status()

        self.assertTrue(result["ok"])
        self.assertEqual(result["language"], "en")
        self.assertEqual(result["commentary_style"], "professional")
        self.assertEqual(result["label"], "Spain vs Belgium")
        self.assertEqual(updated["language"], "en")
        self.assertEqual(updated["espn"]["event_id"], "760511")
        self.assertEqual(updated["espn"]["starts_at"], "2026-07-10T19:00:00+00:00")
        self.assertEqual(updated["espn"]["favorite_team"], "Spain")
        self.assertEqual(updated["espn"]["position_team"], "")
        self.assertEqual(updated["espn"]["commentary_style"], "professional")
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
        self.assertEqual(
            updated["markets"][0]["goal_signal_up_team"],
            {"zh": "西班牙", "en": "Spain"},
        )
        self.assertEqual(
            updated["markets"][0]["goal_signal_down_team"],
            {"zh": "比利时", "en": "Belgium"},
        )
        self.assertTrue(updated["markets"][0]["alerts_enabled"])
        self.assertFalse(updated["markets"][1]["alerts_enabled"])
        self.assertEqual(english_status["label"], "Spain vs Belgium")
        self.assertEqual(english_status["favorite_team"], "Spain")
        self.assertEqual(english_status["language"], "en")
        self.assertEqual(english_status["commentary_style"], "professional")
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
        self.assertIn('name="commentary_style"', page)
        self.assertIn('value="casual"', page)
        self.assertIn('value="balanced"', page)
        self.assertIn('value="professional"', page)
        self.assertIn('id="style-effective"', page)
        self.assertIn("showEffectiveStyle(style)", page)
        self.assertIn("/api/setup/style", page)
        self.assertIn('id="apply"', page)

    def test_style_only_update_persists_without_requesting_full_reload(self):
        initial = {
            "language": "zh",
            "espn": {"commentary_style": "balanced"},
            "setup_server": {},
            "markets": [{"ticker": "TEST", "label": "测试"}],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "watch.json"
            path.write_text(json.dumps(initial), encoding="utf-8")
            instance = service(path)

            result = instance.apply_commentary_style(
                {"commentary_style": "professional"}
            )
            updated = json.loads(path.read_text(encoding="utf-8"))

            self.assertFalse(instance.take_reload_requested())
            self.assertEqual(instance.take_commentary_style_update(), "professional")
            self.assertIsNone(instance.take_commentary_style_update())

        self.assertEqual(result, {"ok": True, "commentary_style": "professional"})
        self.assertEqual(updated["espn"]["commentary_style"], "professional")

    def test_style_only_update_rejects_missing_or_invalid_value(self):
        initial = {"espn": {}, "markets": [{"ticker": "TEST", "label": "test"}]}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "watch.json"
            path.write_text(json.dumps(initial), encoding="utf-8")
            instance = service(path)

            with self.assertRaisesRegex(ValueError, "commentary_style is required"):
                instance.apply_commentary_style({})
            with self.assertRaisesRegex(ValueError, "espn.commentary_style"):
                instance.apply_commentary_style({"commentary_style": "dramatic"})

    def test_config_mutations_are_serialized_across_setup_threads(self):
        initial = {"espn": {}, "markets": [{"ticker": "TEST", "label": "test"}]}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "watch.json"
            path.write_text(json.dumps(initial), encoding="utf-8")
            instance = service(path)
            real_write = setup.atomic_write_json
            tracker_lock = threading.Lock()
            active = 0
            max_active = 0
            errors = []

            def tracked_write(target, payload):
                nonlocal active, max_active
                with tracker_lock:
                    active += 1
                    max_active = max(max_active, active)
                try:
                    time.sleep(0.02)
                    real_write(target, payload)
                finally:
                    with tracker_lock:
                        active -= 1

            def update(style):
                try:
                    instance.apply_commentary_style({"commentary_style": style})
                except Exception as error:  # pragma: no cover - asserted below.
                    errors.append(error)

            with patch.object(setup, "atomic_write_json", side_effect=tracked_write):
                threads = [
                    threading.Thread(target=update, args=(style,))
                    for style in ("casual", "professional")
                ]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join()

        self.assertEqual(errors, [])
        self.assertEqual(max_active, 1)


KALSHI_GENERAL_EVENT = {
    "event": {
        "event_ticker": "KXPRES-28",
        "title": "Presidential winner",
        "markets": [
            {"ticker": "KXPRES-28-A", "yes_sub_title": "Candidate A", "volume_24h": 500},
            {"ticker": "KXPRES-28-B", "yes_sub_title": "Candidate B", "volume_24h": 900},
            {"ticker": "KXPRES-28-C", "yes_sub_title": "Candidate C", "volume_24h": 100},
            {"ticker": "KXPRES-28-D", "yes_sub_title": "Candidate D", "volume_24h": 50},
            {"ticker": "KXPRES-28-E", "yes_sub_title": "Candidate E", "volume_24h": 300},
        ],
    }
}


class StandaloneMarketTests(unittest.TestCase):
    def test_apply_market_selection_configures_ticker_only_watch(self):
        initial = {
            "ticker_enabled": False,
            "probability_bar": {"enabled": True, "market_ticker": "OLD"},
            "espn": {"enabled": True, "event_id": "760511"},
            "setup_server": {},
            "markets": [{"ticker": "OLD", "label": "old", "alert_move_cents": 7}],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "watch.json"
            path.write_text(json.dumps(initial), encoding="utf-8")
            instance = service(path)
            with patch.object(
                instance,
                "_kalshi_event_any",
                return_value=setup.general_event_markets(KALSHI_GENERAL_EVENT),
            ):
                result = instance.apply_market_selection(
                    {
                        "kalshi_url": "https://kalshi.com/markets/KXPRES-28",
                        "language": "en",
                    }
                )
            updated = json.loads(path.read_text(encoding="utf-8"))

        self.assertTrue(result["ok"])
        self.assertEqual(result["label"], "Presidential winner")
        self.assertEqual(result["language"], "en")
        self.assertEqual(result["event_id"], "")
        # Top four markets by traded volume, most active first.
        self.assertEqual(
            [market["ticker"] for market in updated["markets"]],
            ["KXPRES-28-B", "KXPRES-28-A", "KXPRES-28-E", "KXPRES-28-C"],
        )
        self.assertTrue(all(market["show_in_ticker"] for market in updated["markets"]))
        self.assertTrue(all(market["alerts_enabled"] for market in updated["markets"]))
        self.assertFalse(any(market.get("goal_signal_enabled") for market in updated["markets"]))
        # Defaults inherited from the previous first market entry.
        self.assertEqual(updated["markets"][0]["alert_move_cents"], 7)
        self.assertEqual(updated["markets"][0]["label"], "Candidate B")
        self.assertTrue(updated["ticker_enabled"])
        self.assertFalse(updated["probability_bar"]["enabled"])
        self.assertFalse(updated["espn"]["enabled"])
        self.assertEqual(updated["language"], "en")
        self.assertEqual(updated["setup_server"]["last_event_ticker"], "KXPRES-28")

    def test_apply_market_selection_rejects_unrecognizable_input(self):
        instance = service(Path("unused.json"))

        with self.assertRaises(ValueError):
            instance.apply_market_selection({"kalshi_url": "not a kalshi link"})

    def test_daily_prompt_bookkeeping_roundtrip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "watch.json"
            path.write_text(json.dumps({"setup_server": {}}), encoding="utf-8")
            instance = service(path)

            self.assertEqual(instance.last_daily_prompt(), "")
            instance.record_daily_prompt("2026-07-10")
            self.assertEqual(instance.last_daily_prompt(), "2026-07-10")
            instance.record_daily_prompt("2026-07-10")
            updated = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(updated["setup_server"]["last_daily_prompt"], "2026-07-10")


if __name__ == "__main__":
    unittest.main()
