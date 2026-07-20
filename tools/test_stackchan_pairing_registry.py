from __future__ import annotations

import importlib.util
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

MODULE_PATH = Path(__file__).with_name("stackchan_kalshi_watch.py")
SPEC = importlib.util.spec_from_file_location("stackchan_kalshi_watch", MODULE_PATH)
assert SPEC and SPEC.loader
watcher = importlib.util.module_from_spec(SPEC)
sys.modules.setdefault(SPEC.name, watcher)
SPEC.loader.exec_module(watcher)


def registry_entry(**overrides) -> dict:
    entry = {
        "id": "mlb-2026-07-20-LAD-PHI",
        "category": "mlb",
        "label": {"zh": "道奇 vs 费城人", "en": "Dodgers vs Phillies"},
        "starts_at": "2026-07-20T23:10:00+00:00",
        "outcomes": ["LAD", "PHI"],
        "outcome_labels": {
            "LAD": {"zh": "道奇", "en": "Dodgers"},
            "PHI": {"zh": "费城人", "en": "Phillies"},
        },
        "display": {
            "LAD": {"flag": "us", "color": "#005A9C"},
            "PHI": {"flag": "us", "color": "#E81828"},
        },
        "event_source": {
            "provider": "espn",
            "league": "baseball/mlb",
            "event_id": "401816187",
        },
        "venue_markets": [
            {
                "venue": "kalshi",
                "event_ticker": "KXMLBGAME-26JUL201910LADPHI",
                "outcome_map": {
                    "LAD": "KXMLBGAME-26JUL201910LADPHI-LAD",
                    "PHI": "KXMLBGAME-26JUL201910LADPHI-PHI",
                },
            },
            {
                "venue": "polymarket",
                "event_id": "702860",
                "market_id": "2922141",
                "outcome_map": {
                    "LAD": "Los Angeles Dodgers",
                    "PHI": "Philadelphia Phillies",
                },
            },
        ],
        "pairing": {
            "proposed_by": "agent",
            "confidence": 0.98,
            "evidence": "test",
            "confirmed": True,
        },
    }
    entry.update(overrides)
    return entry


class RegistryConfigTest(unittest.TestCase):
    def load(self, config_dict: dict, entries: list[dict]) -> "watcher.WatchConfig":
        tmpdir = tempfile.mkdtemp()
        self.addCleanup(lambda: None)
        config_path = Path(tmpdir) / "watchlist.json"
        registry_path = Path(tmpdir) / "pairing_registry.json"
        config_path.write_text(json.dumps(config_dict), encoding="utf-8")
        registry_path.write_text(
            json.dumps({"canonical_events": entries}, ensure_ascii=False),
            encoding="utf-8",
        )
        return watcher.load_config(config_path)

    def base_config(self, **overrides) -> dict:
        config = {"language": "zh", "active_canonical_event": "mlb-2026-07-20-LAD-PHI"}
        config.update(overrides)
        return config

    def test_derives_markets_bar_and_polymarket_mapping(self) -> None:
        config = self.load(self.base_config(), [registry_entry()])
        watcher.validate_config(config, dry_run=True)

        self.assertEqual(len(config.markets), 2)
        primary, mirror = config.markets
        self.assertEqual(primary.ticker, "KXMLBGAME-26JUL201910LADPHI-LAD")
        self.assertEqual(primary.label, "道奇获胜")
        self.assertTrue(primary.alerts_enabled)
        self.assertTrue(primary.goal_signal_enabled)
        self.assertEqual(primary.goal_signal_up_team, "道奇")
        self.assertEqual(primary.goal_signal_down_team, "费城人")
        self.assertEqual(mirror.ticker, "KXMLBGAME-26JUL201910LADPHI-PHI")
        self.assertFalse(mirror.alerts_enabled)
        self.assertFalse(mirror.goal_signal_enabled)

        bar = config.probability_bar
        self.assertTrue(bar.enabled)
        self.assertEqual(bar.icon, "baseball")
        self.assertEqual(bar.mode, "normalized_outcomes")
        self.assertEqual(bar.market_ticker, primary.ticker)
        self.assertEqual(bar.right_market_ticker, mirror.ticker)
        self.assertEqual(bar.left_flag, "us")
        self.assertEqual(bar.left_color, "#005A9C")
        self.assertEqual(bar.right_color, "#E81828")
        self.assertEqual(bar.polymarket_market_id, "2922141")
        self.assertEqual(bar.polymarket_left_outcome, "Los Angeles Dodgers")
        self.assertEqual(bar.polymarket_right_outcome, "Philadelphia Phillies")

        self.assertFalse(config.espn.enabled)
        self.assertEqual(config.espn.starts_at, "2026-07-20T23:10:00+00:00")
        self.assertEqual(config.espn.label, "道奇 vs 费城人")

    def test_english_language_labels(self) -> None:
        config = self.load(self.base_config(language="en"), [registry_entry()])
        self.assertEqual(config.markets[0].label, "Dodgers to win")
        self.assertEqual(config.markets[0].goal_signal_down_team, "Phillies")

    def test_soccer_category_keeps_football_icon(self) -> None:
        entry = registry_entry(
            id="epl-2026-08-15-ARS-MCI",
            category="epl",
            event_source={
                "provider": "espn",
                "league": "soccer/eng.1",
                "event_id": "740001",
            },
        )
        config = self.load(
            self.base_config(active_canonical_event="epl-2026-08-15-ARS-MCI"),
            [entry],
        )
        self.assertEqual(config.probability_bar.icon, "football")

    def test_soccer_event_source_enables_espn(self) -> None:
        entry = registry_entry(
            id="epl-2026-08-15-ARS-MCI",
            event_source={
                "provider": "espn",
                "league": "soccer/eng.1",
                "event_id": "740001",
            },
        )
        config = self.load(
            self.base_config(active_canonical_event="epl-2026-08-15-ARS-MCI"),
            [entry],
        )
        self.assertTrue(config.espn.enabled)
        self.assertEqual(config.espn.league, "eng.1")
        self.assertEqual(config.espn.event_id, "740001")

    def test_unconfirmed_entry_is_rejected(self) -> None:
        entry = registry_entry()
        entry["pairing"]["confirmed"] = False
        with self.assertRaisesRegex(watcher.ConfigError, "not confirmed"):
            self.load(self.base_config(), [entry])

    def test_unknown_event_id_is_rejected(self) -> None:
        with self.assertRaisesRegex(watcher.ConfigError, "not found"):
            self.load(
                self.base_config(active_canonical_event="nope"), [registry_entry()]
            )

    def test_multi_outcome_entries_wait_for_p5(self) -> None:
        entry = registry_entry(outcomes=["A", "B", "C"])
        with self.assertRaisesRegex(watcher.ConfigError, "P5"):
            self.load(self.base_config(), [entry])

    def test_kalshi_outcome_map_mismatch_is_rejected(self) -> None:
        entry = registry_entry()
        entry["venue_markets"][0]["outcome_map"] = {"LAD": "X", "BOS": "Y"}
        with self.assertRaisesRegex(watcher.ConfigError, "kalshi outcome_map"):
            self.load(self.base_config(), [entry])

    def test_entry_without_polymarket_clears_mapping(self) -> None:
        entry = registry_entry()
        entry["venue_markets"] = [entry["venue_markets"][0]]
        config = self.load(self.base_config(), [entry])
        self.assertEqual(config.probability_bar.polymarket_market_id, "")

    def test_missing_display_hints_keep_existing_bar_style(self) -> None:
        entry = registry_entry()
        entry.pop("display")
        config = self.load(
            self.base_config(
                probability_bar={"left_flag": "fr", "right_flag": "ma"}
            ),
            [entry],
        )
        self.assertTrue(config.probability_bar.enabled)
        self.assertEqual(config.probability_bar.left_flag, "fr")
        self.assertEqual(config.probability_bar.right_flag, "ma")

    def test_explicitly_disabled_bar_stays_off(self) -> None:
        config = self.load(
            self.base_config(probability_bar={"enabled": False}), [registry_entry()]
        )
        self.assertFalse(config.probability_bar.enabled)

    def test_registry_overrides_manual_markets_with_note(self) -> None:
        manual = {
            "ticker": "KXOLD",
            "label": {"zh": "旧市场", "en": "Old market"},
        }
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            config = self.load(self.base_config(markets=[manual]), [registry_entry()])
        self.assertEqual(
            [market.ticker for market in config.markets],
            [
                "KXMLBGAME-26JUL201910LADPHI-LAD",
                "KXMLBGAME-26JUL201910LADPHI-PHI",
            ],
        )
        self.assertIn("overrides", stderr.getvalue())


class ValidateRegistryVenuesTest(unittest.TestCase):
    def build_config(self) -> "watcher.WatchConfig":
        tmpdir = tempfile.mkdtemp()
        config_path = Path(tmpdir) / "watchlist.json"
        registry_path = Path(tmpdir) / "pairing_registry.json"
        config_path.write_text(
            json.dumps(
                {"language": "zh", "active_canonical_event": "mlb-2026-07-20-LAD-PHI"}
            ),
            encoding="utf-8",
        )
        registry_path.write_text(
            json.dumps({"canonical_events": [registry_entry()]}, ensure_ascii=False),
            encoding="utf-8",
        )
        return watcher.load_config(config_path)

    def fake_http(self, kalshi_status: str, gamma_outcomes: list[str]):
        def dispatch(url: str):
            if "/markets/KXMLBGAME" in url:
                ticker = url.rsplit("/", 1)[-1].split("?")[0]
                return {
                    "market": {
                        "ticker": ticker,
                        "status": kalshi_status,
                        "title": "test",
                    }
                }
            if "gamma-api" in url or "id=2922141" in url:
                return [
                    {
                        "id": "2922141",
                        "question": "Dodgers vs Phillies",
                        "outcomes": json.dumps(gamma_outcomes),
                        "active": True,
                        "closed": False,
                    }
                ]
            raise AssertionError(f"unexpected URL {url}")

        return dispatch

    def test_healthy_setup_returns_no_warnings(self) -> None:
        config = self.build_config()
        fake = self.fake_http("active", ["Los Angeles Dodgers", "Philadelphia Phillies"])
        with patch.object(watcher, "http_json", side_effect=fake):
            warnings = watcher.validate_registry_venues(config)
        self.assertEqual(warnings, [])
        self.assertEqual(config.probability_bar.polymarket_market_id, "2922141")

    def test_settled_kalshi_market_warns_but_keeps_running(self) -> None:
        config = self.build_config()
        fake = self.fake_http("finalized", ["Los Angeles Dodgers", "Philadelphia Phillies"])
        with patch.object(watcher, "http_json", side_effect=fake):
            warnings = watcher.validate_registry_venues(config)
        self.assertTrue(any("already settled" in warning for warning in warnings))

    def test_polymarket_outcome_mismatch_drops_mapping(self) -> None:
        config = self.build_config()
        fake = self.fake_http("active", ["Yes", "No"])
        with patch.object(watcher, "http_json", side_effect=fake):
            warnings = watcher.validate_registry_venues(config)
        self.assertTrue(any("dropping the polymarket mapping" in w for w in warnings))
        self.assertEqual(config.probability_bar.polymarket_market_id, "")

    def test_network_failure_degrades_to_warnings(self) -> None:
        config = self.build_config()
        with patch.object(watcher, "http_json", side_effect=OSError("offline")):
            warnings = watcher.validate_registry_venues(config)
        self.assertTrue(warnings)
        self.assertEqual(config.probability_bar.polymarket_market_id, "2922141")

    def test_legacy_config_skips_registry_checks(self) -> None:
        config = watcher.WatchConfig(
            markets=[watcher.MarketConfig("KXA", "A")],
        )
        with patch.object(watcher, "http_json", side_effect=AssertionError("no fetch")):
            self.assertEqual(watcher.validate_registry_venues(config), [])


if __name__ == "__main__":
    unittest.main()
