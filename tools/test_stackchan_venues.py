from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import stackchan_venues as venues
from stackchan_venues import (
    KalshiVenueAdapter,
    PolymarketMarketRef,
    PolymarketVenueAdapter,
    VenueQuote,
    aggregate_probability,
    max_divergence,
    same_direction_jump,
)

NOW = datetime(2026, 7, 19, 12, 0, tzinfo=timezone.utc)


def quote(**overrides) -> VenueQuote:
    base = dict(
        venue="kalshi",
        market_id="M",
        outcome="NYY",
        prob_mid=0.5,
        bid=None,
        ask=None,
        volume_usd=None,
        liquidity_usd=None,
        status="open",
        close_time=None,
        fetched_at=NOW,
    )
    base.update(overrides)
    return VenueQuote(**base)


class RecordingFetch:
    def __init__(self, payload):
        self.payload = payload
        self.urls: list[str] = []

    def __call__(self, url: str):
        self.urls.append(url)
        return self.payload


class KalshiAdapterTest(unittest.TestCase):
    def test_raw_markets_preserves_legacy_url_shape(self) -> None:
        fetch = RecordingFetch({"markets": []})
        adapter = KalshiVenueAdapter("https://example.test/v2", fetch=fetch)
        adapter.raw_markets(["AAA", "BBB"])
        self.assertEqual(
            fetch.urls,
            ["https://example.test/v2/markets?tickers=AAA%2CBBB&limit=100"],
        )

    def test_quotes_normalizes_dollars_to_probability(self) -> None:
        fetch = RecordingFetch(
            {
                "markets": [
                    {
                        "ticker": "KXTEST-A",
                        "status": "active",
                        "yes_bid_dollars": "0.61",
                        "yes_ask_dollars": "0.65",
                        "last_price_dollars": "0.62",
                        "volume_24h_fp": "1500",
                        "liquidity_dollars": "5200",
                        "close_time": "2026-07-21T23:10:00Z",
                    }
                ]
            }
        )
        adapter = KalshiVenueAdapter(fetch=fetch)
        (result,) = adapter.quotes(["kxtest-a"])
        self.assertEqual(result.venue, "kalshi")
        self.assertEqual(result.market_id, "KXTEST-A")
        self.assertEqual(result.status, "open")
        self.assertAlmostEqual(result.prob_mid, 0.63)
        self.assertAlmostEqual(result.bid, 0.61)
        self.assertAlmostEqual(result.ask, 0.65)
        self.assertAlmostEqual(result.liquidity_usd, 5200.0)
        self.assertEqual(result.close_time.year, 2026)

    def test_quotes_settled_market_uses_result(self) -> None:
        fetch = RecordingFetch(
            {
                "markets": [
                    {"ticker": "KXTEST-B", "status": "finalized", "result": "yes"},
                ]
            }
        )
        (result,) = KalshiVenueAdapter(fetch=fetch).quotes(["KXTEST-B"])
        self.assertEqual(result.status, "settled")
        self.assertEqual(result.prob_mid, 1.0)

    def test_quotes_falls_back_to_last_price_without_book(self) -> None:
        fetch = RecordingFetch(
            {
                "markets": [
                    {
                        "ticker": "KXTEST-C",
                        "status": "active",
                        "last_price_dollars": "0.44",
                    }
                ]
            }
        )
        (result,) = KalshiVenueAdapter(fetch=fetch).quotes(["KXTEST-C"])
        self.assertAlmostEqual(result.prob_mid, 0.44)
        self.assertIsNone(result.spread())


class PolymarketAdapterTest(unittest.TestCase):
    GAMMA_MARKET = {
        "id": "516871",
        "question": "Yankees vs. Red Sox",
        "outcomes": '["Yankees", "Red Sox"]',
        "outcomePrices": '["0.58", "0.42"]',
        "bestBid": 0.57,
        "bestAsk": 0.59,
        "volume24hr": 120000.5,
        "liquidityNum": 30000.25,
        "active": True,
        "closed": False,
        "endDate": "2026-07-22T03:00:00Z",
    }

    def test_quotes_maps_canonical_outcomes(self) -> None:
        fetch = RecordingFetch([self.GAMMA_MARKET])
        adapter = PolymarketVenueAdapter(fetch=fetch)
        ref = PolymarketMarketRef(
            market_id="516871",
            outcomes={"NYY": "Yankees", "BOS": "Red Sox"},
        )
        results = adapter.quotes([ref])
        self.assertEqual(fetch.urls, ["https://gamma-api.polymarket.com/markets?id=516871"])
        by_outcome = {q.outcome: q for q in results}
        self.assertEqual(set(by_outcome), {"NYY", "BOS"})
        self.assertAlmostEqual(by_outcome["NYY"].prob_mid, 0.58)
        self.assertAlmostEqual(by_outcome["BOS"].prob_mid, 0.42)
        self.assertAlmostEqual(by_outcome["NYY"].bid, 0.57)
        self.assertAlmostEqual(by_outcome["BOS"].bid, 1.0 - 0.59)
        self.assertAlmostEqual(by_outcome["NYY"].liquidity_usd, 30000.25)
        self.assertEqual(by_outcome["NYY"].status, "open")

    def test_quotes_reports_closed_status(self) -> None:
        market = dict(self.GAMMA_MARKET, closed=True)
        adapter = PolymarketVenueAdapter(fetch=RecordingFetch([market]))
        results = adapter.quotes([PolymarketMarketRef(market_id="516871")])
        self.assertTrue(all(q.status == "closed" for q in results))

    def test_metadata_decodes_outcomes(self) -> None:
        adapter = PolymarketVenueAdapter(fetch=RecordingFetch([self.GAMMA_MARKET]))
        meta = adapter.metadata("516871")
        self.assertEqual(meta.title, "Yankees vs. Red Sox")
        self.assertEqual(meta.outcomes, ["Yankees", "Red Sox"])


class AggregationTest(unittest.TestCase):
    def test_liquidity_weighted_mid(self) -> None:
        quotes = [
            quote(venue="kalshi", prob_mid=0.60, liquidity_usd=1000.0),
            quote(venue="polymarket", prob_mid=0.50, liquidity_usd=3000.0),
        ]
        self.assertAlmostEqual(aggregate_probability(quotes), 0.525)

    def test_equal_weight_when_any_liquidity_unknown(self) -> None:
        quotes = [
            quote(venue="kalshi", prob_mid=0.60, liquidity_usd=None),
            quote(venue="polymarket", prob_mid=0.50, liquidity_usd=3000.0),
        ]
        self.assertAlmostEqual(aggregate_probability(quotes), 0.55)

    def test_wide_spread_quote_is_dropped_when_tighter_book_exists(self) -> None:
        quotes = [
            quote(venue="kalshi", prob_mid=0.60, bid=0.40, ask=0.80),
            quote(venue="polymarket", prob_mid=0.50, bid=0.49, ask=0.51),
        ]
        self.assertAlmostEqual(aggregate_probability(quotes), 0.50)

    def test_settled_quote_wins(self) -> None:
        quotes = [
            quote(venue="kalshi", prob_mid=1.0, status="settled"),
            quote(venue="polymarket", prob_mid=0.95),
        ]
        self.assertEqual(aggregate_probability(quotes), 1.0)

    def test_empty_and_unpriced_quotes(self) -> None:
        self.assertIsNone(aggregate_probability([]))
        self.assertIsNone(aggregate_probability([quote(prob_mid=None)]))

    def test_single_source_degrades_gracefully(self) -> None:
        self.assertAlmostEqual(aggregate_probability([quote(prob_mid=0.7)]), 0.7)


class DivergenceTest(unittest.TestCase):
    def test_reports_cross_venue_gap_over_threshold(self) -> None:
        result = max_divergence(
            [
                quote(venue="kalshi", prob_mid=0.62),
                quote(venue="polymarket", prob_mid=0.51),
            ]
        )
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result.gap, 0.11)
        self.assertEqual(result.outcome, "NYY")

    def test_ignores_small_gaps_same_venue_and_other_outcomes(self) -> None:
        self.assertIsNone(
            max_divergence(
                [
                    quote(venue="kalshi", prob_mid=0.62),
                    quote(venue="polymarket", prob_mid=0.57),
                ]
            )
        )
        self.assertIsNone(
            max_divergence(
                [
                    quote(venue="kalshi", prob_mid=0.10),
                    quote(venue="kalshi", prob_mid=0.90),
                ]
            )
        )
        self.assertIsNone(
            max_divergence(
                [
                    quote(venue="kalshi", outcome="NYY", prob_mid=0.10),
                    quote(venue="polymarket", outcome="BOS", prob_mid=0.90),
                ]
            )
        )


class SameDirectionJumpTest(unittest.TestCase):
    def test_requires_both_venues_over_threshold_same_sign(self) -> None:
        self.assertTrue(same_direction_jump(0.06, 0.05, 0.05))
        self.assertTrue(same_direction_jump(-0.08, -0.05, 0.05))
        self.assertFalse(same_direction_jump(0.06, -0.06, 0.05))
        self.assertFalse(same_direction_jump(0.06, 0.03, 0.05))
        self.assertFalse(same_direction_jump(None, 0.06, 0.05))


MODULE_PATH = Path(__file__).with_name("stackchan_kalshi_watch.py")
SPEC = importlib.util.spec_from_file_location("stackchan_kalshi_watch", MODULE_PATH)
assert SPEC and SPEC.loader
watcher = importlib.util.module_from_spec(SPEC)
sys.modules.setdefault(SPEC.name, watcher)
SPEC.loader.exec_module(watcher)


def two_way_bar_config(**bar_overrides) -> "watcher.WatchConfig":
    france = watcher.MarketConfig("FRA", "法国晋级")
    morocco = watcher.MarketConfig("MAR", "摩洛哥晋级")
    bar = watcher.ProbabilityBarConfig(
        enabled=True,
        mode="normalized_outcomes",
        market_ticker="FRA",
        right_market_ticker="MAR",
        left_flag="fr",
        left_color="#0055A4",
        right_flag="ma",
        right_color="#C1272D",
        **bar_overrides,
    )
    return watcher.WatchConfig(probability_bar=bar, markets=[france, morocco])


def live_snapshots() -> dict:
    return {
        "FRA": watcher.MarketSnapshot("FRA", "法国晋级", "active", "E", 77, 79, 21, 23, 78, "", None),
        "MAR": watcher.MarketSnapshot("MAR", "摩洛哥晋级", "active", "E", 23, 25, 75, 77, 24, "", None),
    }


class AggregatedProbabilityBarTest(unittest.TestCase):
    def test_without_venue_quotes_matches_single_source_behavior(self) -> None:
        command = watcher.persistent_display_command(two_way_bar_config(), live_snapshots())
        self.assertEqual(command, "pkbar fr 76 0055A4 ma 24 C1272D")

    def test_polymarket_quote_shifts_aggregate(self) -> None:
        # Kalshi says 78, Polymarket says 50 with unknown liquidity on the
        # Kalshi side -> equal weight mean 64 vs right 24 -> 73%.
        extra = {"left": [quote(venue="polymarket", prob_mid=0.50)]}
        command = watcher.persistent_display_command(
            two_way_bar_config(), live_snapshots(), extra
        )
        self.assertEqual(command, "pkbar fr 73 0055A4 ma 27 C1272D")

    def test_polymarket_only_renders_when_kalshi_missing(self) -> None:
        extra = {
            "left": [quote(venue="polymarket", outcome="left", prob_mid=0.60)],
            "right": [quote(venue="polymarket", outcome="right", prob_mid=0.40)],
        }
        command = watcher.persistent_display_command(two_way_bar_config(), {}, extra)
        self.assertEqual(command, "pkbar fr 60 0055A4 ma 40 C1272D")

    def test_non_football_icon_appends_token(self) -> None:
        config = two_way_bar_config(icon="baseball")
        command = watcher.persistent_display_command(config, live_snapshots())
        self.assertEqual(command, "pkbar fr 76 0055A4 ma 24 C1272D baseball")

    def test_unknown_icon_is_rejected_by_validation(self) -> None:
        config = two_way_bar_config(icon="cricket")
        with self.assertRaisesRegex(watcher.ConfigError, "probability_bar.icon"):
            watcher.validate_config(config, dry_run=True)


class VenueQuoteFromSnapshotTest(unittest.TestCase):
    def test_open_snapshot_maps_book_to_probability_space(self) -> None:
        snapshot = watcher.MarketSnapshot(
            "FRA", "法国晋级", "active", "E", 61, 65, 35, 39, 62, "1500", None,
            liquidity_usd=5200.0,
        )
        converted = watcher.venue_quote_from_snapshot(snapshot, "left")
        self.assertEqual(converted.status, "open")
        self.assertEqual(converted.outcome, "left")
        self.assertAlmostEqual(converted.prob_mid, 0.63)
        self.assertAlmostEqual(converted.bid, 0.61)
        self.assertAlmostEqual(converted.liquidity_usd, 5200.0)

    def test_settled_snapshot_reports_settlement(self) -> None:
        snapshot = watcher.MarketSnapshot(
            "FRA", "法国晋级", "finalized", "E", 0, 100, 0, 100, 99, "", None,
            result="yes", settlement_value_cents=100,
        )
        converted = watcher.venue_quote_from_snapshot(snapshot, "left")
        self.assertEqual(converted.status, "settled")
        self.assertEqual(converted.prob_mid, 1.0)


class VenueDivergenceAlertTest(unittest.TestCase):
    def divergence(self) -> venues.VenueDivergence:
        return venues.VenueDivergence(
            outcome="left",
            quote_a=quote(venue="kalshi", prob_mid=0.62),
            quote_b=quote(venue="polymarket", prob_mid=0.51),
            gap=0.11,
        )

    def test_chinese_alert_is_informational(self) -> None:
        config = two_way_bar_config()
        alert = watcher.venue_divergence_alert(config, self.divergence(), "法国晋级")
        self.assertEqual(alert.kind, "venue_divergence")
        self.assertEqual(alert.priority, 60)
        self.assertTrue(alert.spoiler_sensitive)
        self.assertIn("Kalshi", alert.balloon)
        self.assertIn("Polymarket", alert.balloon)
        self.assertIn("62", alert.speech)
        self.assertIn("51", alert.speech)
        for banned in ("买", "卖", "下注", "套利"):
            self.assertNotIn(banned, alert.speech)

    def test_english_alert_names_both_venues(self) -> None:
        config = two_way_bar_config()
        config.language = "en"
        alert = watcher.venue_divergence_alert(config, self.divergence(), "France to advance")
        self.assertIn("Kalshi says 62 percent", alert.speech)
        self.assertIn("Polymarket says 51 percent", alert.speech)


class CorroborateGoalSignalTest(unittest.TestCase):
    def test_upgrade_boosts_priority_and_wording(self) -> None:
        config = two_way_bar_config()
        base = watcher.Alert(
            ticker="FRA",
            label="法国晋级",
            kind="market_goal_signal",
            priority=930,
            face="happy",
            balloon="盘口突变 +6",
            speech="盘口突然拉升！",
            detail="rapid yes move 60c -> 66c",
            clip_id="odds-up",
            spoiler_sensitive=True,
        )
        upgraded = watcher.corroborate_goal_signal_alert(base, config)
        self.assertEqual(upgraded.priority, 960)
        self.assertTrue(upgraded.balloon.startswith("双平台确认"))
        self.assertIn("可信度很高", upgraded.speech)
        self.assertIn("corroborated by polymarket", upgraded.detail)
        # The original stays untouched (replace, not mutation).
        self.assertEqual(base.priority, 930)


class BarPolymarketRefTest(unittest.TestCase):
    def test_none_without_mapping(self) -> None:
        self.assertIsNone(watcher.bar_polymarket_ref(two_way_bar_config()))

    def test_none_when_outcomes_incomplete(self) -> None:
        config = two_way_bar_config(
            polymarket_market_id="516871",
            polymarket_left_outcome="Yankees",
        )
        self.assertIsNone(watcher.bar_polymarket_ref(config))

    def test_ref_when_fully_configured(self) -> None:
        config = two_way_bar_config(
            polymarket_market_id="516871",
            polymarket_left_outcome="Yankees",
            polymarket_right_outcome="Red Sox",
        )
        ref = watcher.bar_polymarket_ref(config)
        self.assertEqual(ref.market_id, "516871")
        self.assertEqual(ref.outcomes, {"left": "Yankees", "right": "Red Sox"})

    def test_none_when_polymarket_disabled(self) -> None:
        config = two_way_bar_config(
            polymarket_market_id="516871",
            polymarket_left_outcome="Yankees",
            polymarket_right_outcome="Red Sox",
        )
        config.polymarket.enabled = False
        self.assertIsNone(watcher.bar_polymarket_ref(config))


class VenueConfigLoadingTest(unittest.TestCase):
    BASE_CONFIG = {
        "language": "zh",
        "markets": [
            {"ticker": "KXNYY", "label": {"zh": "扬基获胜", "en": "Yankees win"}},
            {"ticker": "KXBOS", "label": {"zh": "红袜获胜", "en": "Red Sox win"}},
        ],
        "probability_bar": {
            "enabled": True,
            "mode": "normalized_outcomes",
            "market_ticker": "KXNYY",
            "right_market_ticker": "KXBOS",
            "left_flag": "us",
            "right_flag": "us",
            "polymarket": {
                "market_id": "516871",
                "left_outcome": "Yankees",
                "right_outcome": "Red Sox",
            },
        },
        "polymarket": {"enabled": True, "poll_seconds": 20},
    }

    def load(self, config_dict) -> "watcher.WatchConfig":
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            json.dump(config_dict, handle)
            path = Path(handle.name)
        try:
            return watcher.load_config(path)
        finally:
            path.unlink()

    def test_parses_polymarket_sections(self) -> None:
        config = self.load(self.BASE_CONFIG)
        self.assertEqual(config.probability_bar.polymarket_market_id, "516871")
        self.assertEqual(config.probability_bar.polymarket_left_outcome, "Yankees")
        self.assertEqual(config.probability_bar.polymarket_right_outcome, "Red Sox")
        self.assertTrue(config.polymarket.enabled)
        self.assertEqual(config.polymarket.poll_seconds, 20)
        watcher.validate_config(config, dry_run=True)

    def test_poll_seconds_floor_protects_rate_limit(self) -> None:
        raw = json.loads(json.dumps(self.BASE_CONFIG))
        raw["polymarket"]["poll_seconds"] = 1
        config = self.load(raw)
        self.assertEqual(config.polymarket.poll_seconds, 15)

    def test_validate_rejects_incomplete_outcome_mapping(self) -> None:
        raw = json.loads(json.dumps(self.BASE_CONFIG))
        raw["probability_bar"]["polymarket"].pop("right_outcome")
        config = self.load(raw)
        with self.assertRaises(watcher.ConfigError):
            watcher.validate_config(config, dry_run=True)


if __name__ == "__main__":
    unittest.main()
