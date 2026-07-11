from __future__ import annotations

import importlib.util
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch


MODULE_PATH = Path(__file__).with_name("stackchan_kalshi_watch.py")
SPEC = importlib.util.spec_from_file_location("stackchan_kalshi_watch", MODULE_PATH)
assert SPEC and SPEC.loader
watcher = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = watcher
SPEC.loader.exec_module(watcher)


def match_snapshot(commentary=None, status="in", home_score="1", away_score="0", players=None):
    return watcher.MatchSnapshot(
        event_id="760510",
        status_state=status,
        status_name="STATUS_IN_PROGRESS",
        status_detail="32'",
        home=watcher.MatchTeam("France", "FRA", "home", home_score),
        away=watcher.MatchTeam("Morocco", "MAR", "away", away_score),
        commentary=commentary or [],
        players=players or {},
    )


def espn_config():
    return watcher.ESPNConfig(
        enabled=True,
        event_id="760510",
        label="法国 vs 摩洛哥",
        favorite_team="France",
        position_team="France",
        announce_fouls=False,
        announce_opponent_free_kicks=True,
        team_names={"France": "法国", "FRA": "法国", "Morocco": "摩洛哥", "MAR": "摩洛哥"},
        team_colors={"France": "#0055A4", "FRA": "#0055A4", "Morocco": "#C1272D", "MAR": "#C1272D"},
        player_names={
            "Kylian Mbappé": "姆巴佩",
            "Ousmane Dembélé": "登贝莱",
            "Dayot Upamecano": "于帕梅卡诺",
            "Yassine Bounou": "布努",
        },
        star_chants={"Kylian Mbappé": "{name}！{name}！打进去了！"},
    )


def english_espn_config():
    config = espn_config()
    config.language = "en"
    config.label = "France vs Morocco"
    config.team_names = {
        "France": "France",
        "FRA": "France",
        "Morocco": "Morocco",
        "MAR": "Morocco",
    }
    config.player_names = {
        "Kylian Mbappé": "Kylian Mbappe",
        "Ousmane Dembélé": "Ousmane Dembele",
        "Yassine Bounou": "Yassine Bounou",
    }
    config.star_chants = {
        "Kylian Mbappé": "{name}! {name}! He scores! France's number {number} delivers!",
    }
    return config


def contains_han(value: str) -> bool:
    return any("\u3400" <= character <= "\u9fff" for character in value)


class SetupServerConfigTests(unittest.TestCase):
    def _load_with_setup_server(self, setup_server: dict) -> watcher.WatchConfig:
        raw = {
            "setup_server": setup_server,
            "markets": [{"ticker": "TEST", "label": "test"}],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            return watcher.load_config(path)

    def test_setup_server_defaults_to_loopback_when_host_is_omitted(self):
        self.assertEqual(watcher.SetupServerConfig().host, "127.0.0.1")
        for setup_server in (
            {"enabled": True},
            {"enabled": True, "host": "  "},
        ):
            with self.subTest(setup_server=setup_server):
                config = self._load_with_setup_server(setup_server)
                self.assertEqual(config.setup_server.host, "127.0.0.1")

    def test_setup_server_honors_explicit_host(self):
        config = self._load_with_setup_server(
            {"enabled": True, "host": "0.0.0.0"}
        )

        self.assertEqual(config.setup_server.host, "0.0.0.0")

    def test_setup_server_rejects_unsupported_ipv6_host(self):
        with self.assertRaisesRegex(
            watcher.ConfigError,
            "setup_server.host must be an IPv4 address or hostname",
        ):
            self._load_with_setup_server({"enabled": True, "host": "::1"})

    def test_advertised_url_prefers_public_base_url(self):
        config = watcher.WatchConfig(
            setup_server=watcher.SetupServerConfig(
                host="127.0.0.1",
                port=8788,
                public_base_url="https://setup.example.test",
            )
        )

        with patch.object(watcher.socket, "socket") as socket_factory:
            url = watcher.advertised_setup_url(config)

        self.assertEqual(url, "https://setup.example.test/setup")
        socket_factory.assert_not_called()

    def test_advertised_url_uses_concrete_bind_host_without_discovery(self):
        config = watcher.WatchConfig(
            setup_server=watcher.SetupServerConfig(host="127.0.0.1", port=8788)
        )

        with patch.object(watcher.socket, "socket") as socket_factory:
            url = watcher.advertised_setup_url(config)

        self.assertEqual(url, "http://127.0.0.1:8788/setup")
        socket_factory.assert_not_called()

    def test_advertised_url_discovers_lan_host_for_wildcard_bind(self):
        config = watcher.WatchConfig(
            stackchan_host="192.0.2.1",
            setup_server=watcher.SetupServerConfig(
                host="0.0.0.0",
                port=8788,
            ),
        )
        with patch.object(watcher.socket, "socket") as socket_factory:
            probe = socket_factory.return_value.__enter__.return_value
            probe.getsockname.return_value = ("192.168.1.23", 54321)

            url = watcher.advertised_setup_url(config)

        self.assertEqual(url, "http://192.168.1.23:8788/setup")
        probe.connect.assert_called_once_with(("192.0.2.1", 80))


class ConfigLocalizationTests(unittest.TestCase):
    def localized_config(self) -> dict:
        return {
            "language": "en",
            "mac_voice": {"zh": "Tingting", "en": "Samantha"},
            "espn": {
                "label": {"zh": "法国 vs 摩洛哥", "en": "France vs Morocco"},
                "team_names": {
                    "France": {"zh": "法国", "en": "France"},
                    "Morocco": {"zh": "摩洛哥", "en": "Morocco"},
                },
                "player_names": {
                    "Kylian Mbappé": {"zh": "姆巴佩", "en": "Kylian Mbappe"},
                },
                "star_chants": {
                    "Kylian Mbappé": {"zh": "进球了", "en": "What a goal"},
                },
            },
            "markets": [
                {
                    "ticker": "TEST",
                    "label": {"zh": "法国晋级", "en": "France to advance"},
                }
            ],
        }

    def test_localized_leaves_follow_config_language_and_cli_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps(self.localized_config(), ensure_ascii=False), encoding="utf-8")

            english = watcher.load_config(path)
            chinese = watcher.load_config(path, "zh-CN")

        self.assertEqual(english.language, "en")
        self.assertEqual(english.mac_voice, "Samantha")
        self.assertEqual(english.espn.label, "France vs Morocco")
        self.assertEqual(english.espn.player_names["Kylian Mbappé"], "Kylian Mbappe")
        self.assertEqual(english.markets[0].label, "France to advance")
        self.assertEqual(chinese.language, "zh")
        self.assertEqual(chinese.mac_voice, "Tingting")
        self.assertEqual(chinese.espn.label, "法国 vs 摩洛哥")
        self.assertEqual(chinese.markets[0].label, "法国晋级")

    def test_legacy_strings_remain_literal(self):
        raw = self.localized_config()
        raw["language"] = "en"
        raw["espn"]["label"] = "旧中文标签"
        raw["markets"][0]["label"] = "旧盘口"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
            config = watcher.load_config(path)

        self.assertEqual(config.espn.label, "旧中文标签")
        self.assertEqual(config.markets[0].label, "旧盘口")

    def test_invalid_localized_leaf_has_config_path(self):
        raw = self.localized_config()
        raw["espn"]["label"] = {"en": ["bad"]}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(watcher.ConfigError, "espn.label.en"):
                watcher.load_config(path)

    def test_missing_selected_locale_uses_semantic_fallbacks_not_other_language(self):
        raw = self.localized_config()
        raw["espn"]["label"] = {"zh": "法国 vs 摩洛哥"}
        raw["espn"]["team_names"]["France"] = {"zh": "法国"}
        raw["markets"][0]["label"] = {"zh": "法国晋级"}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
            config = watcher.load_config(path)

        self.assertEqual(config.espn.label, "")
        self.assertNotIn("France", config.espn.team_names)
        self.assertEqual(config.markets[0].label, "TEST")

    def test_device_setup_sync_uses_effective_service_language(self):
        config = watcher.WatchConfig(
            stackchan_transport="http",
            stackchan_host="192.0.2.1",
            language="zh",
        )
        current = {"language": "en", "label": "Spain vs Belgium"}

        with patch.object(watcher, "post_json") as post:
            watcher.sync_device_match_setup(config, [], current)

        self.assertEqual(post.call_args.args[1]["language"], "en")
        self.assertEqual(post.call_args.args[1]["commentary_style"], "balanced")

    def test_style_sync_is_lightweight_and_does_not_send_options(self):
        config = watcher.WatchConfig(
            stackchan_transport="http",
            stackchan_host="192.0.2.1",
        )

        with patch.object(watcher, "post_json") as post:
            synced = watcher.sync_device_commentary_style(config, "professional")

        self.assertTrue(synced)
        self.assertEqual(
            post.call_args.args,
            (
                "http://192.0.2.1/api/match-setup/options",
                {"commentary_style": "professional"},
            ),
        )
        self.assertEqual(post.call_args.kwargs, {"timeout": 2})

        with patch.object(
            watcher,
            "post_json",
            side_effect=watcher.urllib.error.URLError("offline"),
        ):
            self.assertFalse(
                watcher.sync_device_commentary_style(config, "professional")
            )

    def test_commentary_style_defaults_validates_and_propagates_to_markets(self):
        raw = self.localized_config()
        raw["espn"]["commentary_style"] = "professional"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
            professional = watcher.load_config(path)
            del raw["espn"]["commentary_style"]
            path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
            defaulted = watcher.load_config(path)
            raw["espn"]["commentary_style"] = "dramatic"
            path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(watcher.ConfigError, "espn.commentary_style"):
                watcher.load_config(path)

        self.assertEqual(professional.espn.commentary_style, "professional")
        self.assertEqual(professional.markets[0].commentary_style, "professional")
        self.assertEqual(defaulted.espn.commentary_style, "balanced")
        with self.assertRaisesRegex(watcher.ConfigError, "espn.commentary_style"):
            watcher.normalize_commentary_style("")

    def test_live_style_update_mutates_only_rendering_preferences(self):
        config = watcher.WatchConfig(
            espn=espn_config(),
            markets=[
                watcher.MarketConfig(
                    "FRA",
                    "法国晋级",
                    alerts_enabled=False,
                    goal_signal_move_cents=8,
                )
            ],
        )
        espn_config_object = config.espn
        market_config_object = config.markets[0]

        style = watcher.apply_live_commentary_style(config, "casual")

        self.assertEqual(style, "casual")
        self.assertIs(config.espn, espn_config_object)
        self.assertIs(config.markets[0], market_config_object)
        self.assertEqual(config.espn.commentary_style, "casual")
        self.assertEqual(config.markets[0].commentary_style, "casual")
        self.assertEqual(config.espn.event_id, "760510")
        self.assertFalse(config.markets[0].alerts_enabled)
        self.assertEqual(config.markets[0].goal_signal_move_cents, 8)

    def test_old_goal_signal_speeches_migrate_exact_team_facts(self):
        raw = {
            "language": "zh",
            "espn": {
                "team_names": {
                    "France": "法国",
                    "FRA": "法国",
                    "Morocco": "摩洛哥",
                    "MAR": "摩洛哥",
                }
            },
            "markets": [
                {
                    "ticker": "FRA",
                    "label": "法国晋级",
                    "goal_signal_up_speech": "盘口突然拉升！法国这边很可能进球了！等待文字直播确认。",
                    "goal_signal_down_speech": "盘口突然跳水！摩洛哥可能进球了，等待文字直播确认。",
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
            config = watcher.load_config(path)

        self.assertEqual(config.markets[0].goal_signal_up_team, "法国")
        self.assertEqual(config.markets[0].goal_signal_down_team, "摩洛哥")

    def test_market_position_context_is_inferred_only_for_matching_team(self):
        raw = self.localized_config()
        raw["language"] = "zh"
        raw["espn"].update(
            {
                "favorite_team": "Morocco",
                "position_team": "France",
            }
        )
        raw["markets"][0].update(
            {
                "goal_signal_up_team": {"zh": "法国", "en": "France"},
                "goal_signal_down_team": {"zh": "摩洛哥", "en": "Morocco"},
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
            matching = watcher.load_config(path)
            raw["espn"]["position_team"] = "Morocco"
            path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
            not_matching = watcher.load_config(path)

        self.assertEqual(matching.markets[0].favorite_team, "摩洛哥")
        self.assertEqual(matching.markets[0].position_team, "法国")
        self.assertTrue(matching.markets[0].tracks_position)
        self.assertFalse(not_matching.markets[0].tracks_position)


class ESPNAlertTests(unittest.TestCase):
    def test_backed_team_win_gets_result_voice_motion_and_team_light(self):
        item = {
            "sequence": 90,
            "text": "Match ends, France 2, Morocco 0.",
            "play": {"type": {"type": "full-time"}},
        }

        alert = watcher.alert_for_espn_commentary(
            item,
            match_snapshot([item], status="post", home_score="2", away_score="0"),
            espn_config(),
        )

        self.assertIsNotNone(alert)
        self.assertEqual(alert.clip_id, "favorite-win")
        self.assertEqual(alert.celebration, "result-win")
        self.assertEqual(alert.light_rgb, (0, 85, 164))
        self.assertEqual(alert.face, "happy")
        self.assertFalse(alert.prefer_dynamic_voice)

    def test_backed_team_loss_gets_loss_reaction_in_backed_team_color(self):
        item = {
            "sequence": 90,
            "text": "Match ends, France 0, Morocco 1.",
            "play": {"type": {"type": "full-time"}},
        }

        alert = watcher.alert_for_espn_commentary(
            item,
            match_snapshot([item], status="post", home_score="0", away_score="1"),
            espn_config(),
        )

        self.assertEqual(alert.clip_id, "favorite-lose")
        self.assertEqual(alert.celebration, "result-lose")
        self.assertEqual(alert.light_rgb, (0, 85, 164))
        self.assertEqual(alert.face, "sad")

    def test_no_position_uses_neutral_dynamic_final_score(self):
        config = espn_config()
        config.position_team = ""
        item = {
            "sequence": 90,
            "text": "Match ends, France 2, Morocco 0.",
            "play": {"type": {"type": "full-time"}},
        }

        alert = watcher.alert_for_espn_commentary(
            item,
            match_snapshot([item], status="post", home_score="2", away_score="0"),
            config,
        )

        self.assertIsNone(alert.clip_id)
        self.assertIsNone(alert.celebration)
        self.assertIsNone(alert.light_rgb)
        self.assertEqual(alert.face, "neutral")
        self.assertTrue(alert.prefer_dynamic_voice)

    def test_post_status_fallback_uses_same_position_result_reaction(self):
        alert = watcher._status_change_alert(
            match_snapshot([], status="post", home_score="2", away_score="0"),
            espn_config(),
        )

        self.assertEqual(alert.clip_id, "favorite-win")
        self.assertEqual(alert.celebration, "result-win")

    def test_later_post_status_does_not_repeat_match_ends_celebration(self):
        item = {
            "sequence": 90,
            "text": "Match ends, France 2, Morocco 0.",
            "play": {"type": {"type": "full-time"}},
        }
        state = watcher.ESPNState(initialized=True, last_status_state="in")

        first_alerts = watcher.evaluate_espn_match(
            match_snapshot([item], status="in", home_score="2", away_score="0"),
            espn_config(),
            state,
        )
        later_alerts = watcher.evaluate_espn_match(
            match_snapshot([item], status="post", home_score="2", away_score="0"),
            espn_config(),
            state,
        )

        self.assertEqual([alert.celebration for alert in first_alerts], ["result-win"])
        self.assertEqual(later_alerts, [])

    def test_second_half_start_uses_dynamic_voice(self):
        item = {
            "sequence": 50,
            "time": {"displayValue": "45'"},
            "text": "Second Half begins France 0, Morocco 0.",
            "play": {"type": {"type": "kickoff"}},
        }

        alert = watcher.alert_for_espn_commentary(item, match_snapshot([item]), espn_config())

        self.assertIsNotNone(alert)
        self.assertEqual(alert.kind, "espn_status")
        self.assertIn("下半场开始", alert.speech)
        self.assertTrue(alert.prefer_dynamic_voice)
        self.assertIsNone(alert.clip_id)

    def test_drinks_break_start_and_end_are_announced(self):
        start = {
            "sequence": 23,
            "time": {"displayValue": "29'"},
            "text": "Delay in match for a drinks break.",
            "play": {"type": {"type": "start-delay"}},
        }
        end = {
            "sequence": 24,
            "time": {"displayValue": "32'"},
            "text": "Delay over. They are ready to continue.",
            "play": {"type": {"type": "end-delay"}},
        }
        snapshot = match_snapshot([start, end])

        start_alert = watcher.alert_for_espn_commentary(start, snapshot, espn_config())
        end_alert = watcher.alert_for_espn_commentary(end, snapshot, espn_config())

        self.assertEqual(start_alert.kind, "espn_drinks_break")
        self.assertEqual(end_alert.kind, "espn_drinks_break_end")
        self.assertTrue(start_alert.prefer_dynamic_voice)
        self.assertTrue(end_alert.prefer_dynamic_voice)

    def test_injury_delay_is_not_called_a_drinks_break(self):
        start = {
            "sequence": 60,
            "text": "Delay in match because of an injury.",
            "play": {"type": {"type": "start-delay"}},
        }
        end = {
            "sequence": 61,
            "text": "Delay over. They are ready to continue.",
            "play": {"type": {"type": "end-delay"}},
        }
        snapshot = match_snapshot([start, end])

        self.assertIsNone(watcher.alert_for_espn_commentary(start, snapshot, espn_config()))
        self.assertIsNone(watcher.alert_for_espn_commentary(end, snapshot, espn_config()))

    def test_first_fetch_establishes_baseline_without_replaying_history(self):
        old_goal = {
            "sequence": 7,
            "time": {"displayValue": "12'"},
            "text": "Goal! France 1, Morocco 0.",
            "play": {"type": {"type": "goal"}, "team": {"displayName": "France"}},
        }
        state = watcher.ESPNState()

        alerts = watcher.evaluate_espn_match(match_snapshot([old_goal]), espn_config(), state)

        self.assertEqual(alerts, [])
        self.assertIn("sequence:7", state.seen_commentary)

    def test_commentary_key_prefers_stable_play_id_over_sequence(self):
        item = {"sequence": 2, "play": {"id": "49747102"}}

        self.assertEqual(watcher.commentary_key(item), "play:49747102")

    def test_resequenced_play_is_not_announced_twice(self):
        state = watcher.ESPNState()
        watcher.evaluate_espn_match(match_snapshot([]), espn_config(), state)
        first = {
            "sequence": 2,
            "time": {"displayValue": "2'"},
            "text": "Corner, France. Conceded by Morocco.",
            "play": {
                "id": "49747102",
                "text": "Corner, France. Conceded by Morocco.",
                "type": {"type": "corner-awarded"},
                "team": {"displayName": "France"},
            },
        }
        revised = {**first, "sequence": 3}

        first_alerts = watcher.evaluate_espn_match(
            match_snapshot([first]), espn_config(), state
        )
        revised_alerts = watcher.evaluate_espn_match(
            match_snapshot([revised]), espn_config(), state
        )

        self.assertEqual(len(first_alerts), 1)
        self.assertEqual(revised_alerts, [])
        self.assertIn("play:49747102", state.seen_commentary)

    def test_paired_play_rows_use_one_canonical_description(self):
        secondary = {
            "sequence": 2,
            "time": {"displayValue": "2'"},
            "text": "Conceded by Morocco.",
            "play": {
                "id": "49747102",
                "text": "Corner, France. Conceded by Morocco.",
                "type": {"type": "corner-awarded"},
                "team": {"displayName": "France"},
            },
        }
        canonical = {**secondary, "sequence": 3, "text": secondary["play"]["text"]}
        state = watcher.ESPNState(initialized=True, last_status_state="in")

        alerts = watcher.evaluate_espn_match(
            match_snapshot([secondary, canonical]), espn_config(), state
        )

        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].detail, canonical["text"])

    def test_first_fetch_replays_recent_critical_goal(self):
        wallclock = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
        goal = {
            "sequence": 54,
            "time": {"displayValue": "60'"},
            "text": "Goal! France 1, Morocco 0. Kylian Mbappé scores.",
            "play": {
                "type": {"type": "goal"},
                "team": {"displayName": "France"},
                "wallclock": wallclock,
            },
        }
        config = espn_config()
        config.startup_replay_critical_seconds = 180

        alerts = watcher.evaluate_espn_match(
            match_snapshot([goal]),
            config,
            watcher.ESPNState(),
        )

        self.assertEqual([alert.kind for alert in alerts], ["espn_goal"])
        self.assertIsNotNone(alerts[0].source_event_at)

    def test_new_france_goal_is_happy_and_localized(self):
        state = watcher.ESPNState(initialized=True, last_status_state="in")
        mbappe = watcher.MatchPlayer("231388", "Kylian Mbappé", "K. Mbappé", "10", "France", "FRA")
        goal = {
            "sequence": 8,
            "time": {"displayValue": "32'"},
            "text": "Goal! France 1, Morocco 0.",
            "play": {
                "type": {"type": "goal"},
                "team": {"displayName": "France"},
                "participants": [{"athlete": {"displayName": "Kylian Mbappé"}}],
            },
        }

        alerts = watcher.evaluate_espn_match(
            match_snapshot([goal], players={"kylian mbappé": mbappe}),
            espn_config(),
            state,
        )

        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].kind, "espn_goal")
        self.assertEqual(alerts[0].face, "happy")
        self.assertEqual(alerts[0].clip_id, "favorite-goal")
        self.assertEqual(alerts[0].light_rgb, (0, 85, 164))
        self.assertEqual(alerts[0].celebration, "goal")
        self.assertTrue(alerts[0].prefer_dynamic_voice)
        self.assertIn("法国", alerts[0].balloon)
        self.assertIn("姆巴佩进球", alerts[0].balloon)
        self.assertIn("FRA 1-0 MAR", alerts[0].balloon)
        self.assertIn("姆巴佩为法国打进一球", alerts[0].speech)

    def test_non_star_goal_uses_name_without_roster_style_prefix(self):
        player = watcher.MatchPlayer("999", "Jean Prospect", "J. Prospect", "24", "France", "FRA")
        goal = {
            "sequence": 80,
            "time": {"displayValue": "74'"},
            "text": "Goal! France 2, Morocco 0.",
            "play": {
                "type": {"type": "goal"},
                "team": {"displayName": "France"},
                "participants": [{"athlete": {"id": "999", "displayName": "Jean Prospect"}}],
            },
        }

        alert = watcher.alert_for_espn_commentary(
            goal,
            match_snapshot([goal], home_score="2", players={"999": player}),
            espn_config(),
        )

        self.assertIsNotNone(alert)
        self.assertIn("Prospect", alert.balloon)
        self.assertIn("Jean Prospect为法国打进一球", alert.speech)
        self.assertNotIn("号球员", alert.speech)

    def test_catalog_nickname_is_casual_only_and_balloon_stays_formal(self):
        yamal = watcher.MatchPlayer(
            "362150", "Lamine Yamal", "L. Yamal", "19", "France", "FRA"
        )
        goal = {
            "time": {"displayValue": "32'"},
            "text": "Goal! France. Lamine Yamal scores.",
            "play": {
                "type": {"type": "goal"},
                "team": {"displayName": "France"},
                "participants": [
                    {"athlete": {"id": "362150", "displayName": "Lamine Yamal"}}
                ],
            },
        }
        snapshot = match_snapshot([goal], players={"362150": yamal})

        for style, expected_name, excluded_name in (
            ("casual", "小孩哥", "亚马尔为法国"),
            ("balanced", "亚马尔", "小孩哥"),
            ("professional", "亚马尔", "小孩哥"),
        ):
            config = espn_config()
            config.commentary_style = style
            config.player_names = {}
            config.star_chants = {}
            alert = watcher.alert_for_espn_commentary(goal, snapshot, config)
            with self.subTest(style=style):
                self.assertIn(expected_name, alert.speech)
                self.assertNotIn(excluded_name, alert.speech)
                self.assertIn("亚马尔", alert.balloon)
                self.assertNotIn("小孩哥", alert.balloon)

        red_card = {
            **goal,
            "text": "Lamine Yamal (France) is shown the red card.",
            "play": {
                **goal["play"],
                "type": {"type": "red-card"},
            },
        }
        config = espn_config()
        config.commentary_style = "casual"
        config.player_names = {}
        config.star_chants = {}
        card_alert = watcher.alert_for_espn_commentary(red_card, snapshot, config)
        self.assertIn("亚马尔", card_alert.speech)
        self.assertNotIn("小孩哥", card_alert.speech)

    def test_player_catalog_coverage_counts_profiles_and_raw_fallbacks(self):
        yamal = watcher.MatchPlayer(
            "362150", "Lamine Yamal", "L. Yamal", "19", "France", "FRA"
        )
        unknown = watcher.MatchPlayer(
            "999999", "New Prospect", "N. Prospect", "24", "Morocco", "MAR"
        )
        snapshot = match_snapshot(
            players={
                "362150": yamal,
                "lamine yamal": yamal,
                "999999": unknown,
                "new prospect": unknown,
            }
        )
        config = espn_config()
        config.player_names = {}
        config.star_chants = {}

        total, matched, featured, fallback, signature = watcher.player_catalog_coverage(
            snapshot, config
        )

        self.assertEqual((total, matched, featured), (2, 1, 1))
        self.assertEqual(fallback, ("New Prospect",))
        self.assertEqual(signature, ("362150", "999999"))

    def test_roster_parser_indexes_player_id_and_names(self):
        payload = {
            "rosters": [{
                "team": {"displayName": "France", "abbreviation": "FRA"},
                "roster": [{
                    "jersey": "10",
                    "athlete": {
                        "id": "231388",
                        "displayName": "Kylian Mbappé",
                        "shortName": "K. Mbappé",
                    },
                }],
            }],
        }

        players = watcher._match_players(payload)

        self.assertEqual(players["231388"].jersey, "10")
        self.assertIs(players["kylian mbappé"], players["k. mbappé"])

    def test_new_morocco_goal_is_sad(self):
        state = watcher.ESPNState(initialized=True, last_status_state="in")
        goal = {
            "sequence": 9,
            "time": {"displayValue": "61'"},
            "text": "Goal! France 1, Morocco 1.",
            "play": {"type": {"type": "goal"}, "team": {"displayName": "Morocco"}},
        }

        alerts = watcher.evaluate_espn_match(
            match_snapshot([goal], home_score="1", away_score="1"), espn_config(), state
        )

        self.assertEqual(alerts[0].face, "sad")
        self.assertEqual(alerts[0].clip_id, "opponent-goal")
        self.assertEqual(alerts[0].light_rgb, (193, 39, 45))
        self.assertIsNone(alerts[0].celebration)
        self.assertIn("摩洛哥", alerts[0].speech)

    def test_penalty_miss_is_important(self):
        state = watcher.ESPNState(initialized=True, last_status_state="in")
        penalty = {
            "sequence": 10,
            "time": {"displayValue": "90'"},
            "text": "Penalty missed. France.",
            "play": {
                "type": {"type": "penalty---missed"},
                "team": {"displayName": "France"},
            },
        }

        alerts = watcher.evaluate_espn_match(match_snapshot([penalty]), espn_config(), state)

        self.assertEqual(alerts[0].kind, "espn_penalty")
        self.assertEqual(alerts[0].face, "sad")
        self.assertEqual(alerts[0].clip_id, "favorite-penalty-missed")
        self.assertIsNone(alerts[0].light_rgb)

    def test_close_range_header_names_shooter_assistant_and_goalkeeper(self):
        dayot = watcher.MatchPlayer("1", "Dayot Upamecano", "D. Upamecano", "4", "France", "FRA")
        dembele = watcher.MatchPlayer("2", "Ousmane Dembélé", "O. Dembélé", "11", "France", "FRA")
        bounou = watcher.MatchPlayer("3", "Yassine Bounou", "Y. Bounou", "1", "Morocco", "MAR", "G")
        shot = {
            "sequence": 6,
            "time": {"displayValue": "4'"},
            "text": (
                "Attempt saved. Dayot Upamecano (France) header from very close range "
                "is saved by Yassine Bounou (Morocco). Assisted by Ousmane Dembélé with a cross."
            ),
            "play": {
                "type": {"type": "shot-on-target"},
                "team": {"displayName": "France"},
                "participants": [
                    {"athlete": {"displayName": "Dayot Upamecano"}},
                    {"athlete": {"displayName": "Ousmane Dembélé"}},
                ],
            },
        }
        players = {
            "dayot upamecano": dayot,
            "ousmane dembélé": dembele,
            "yassine bounou": bounou,
        }

        alert = watcher.alert_for_espn_commentary(
            shot,
            match_snapshot([shot], players=players),
            espn_config(),
        )

        self.assertIsNotNone(alert)
        self.assertEqual(alert.kind, "espn_shot_saved")
        self.assertEqual(alert.priority, 820)
        self.assertIn("于帕梅卡诺", alert.speech)
        self.assertIn("登贝莱送出传中", alert.speech)
        self.assertIn("布努扑出", alert.speech)
        self.assertTrue(alert.prefer_dynamic_voice)
        self.assertIsNone(alert.clip_id)

    def test_close_long_range_miss_is_announced_but_routine_miss_is_quiet(self):
        mbappe = watcher.MatchPlayer("1", "Kylian Mbappé", "K. Mbappé", "10", "France", "FRA")
        close_shot = {
            "sequence": 4,
            "time": {"displayValue": "4'"},
            "text": (
                "Attempt missed. Kylian Mbappé (France) right footed shot from outside "
                "the box is close, but misses to the left."
            ),
            "play": {
                "type": {"type": "shot-off-target"},
                "team": {"displayName": "France"},
                "participants": [{"athlete": {"displayName": "Kylian Mbappé"}}],
            },
        }
        routine_shot = {
            "sequence": 26,
            "time": {"displayValue": "33'"},
            "text": (
                "Attempt missed. Ousmane Dembélé (France) left footed shot from outside "
                "the box misses to the left."
            ),
            "play": {
                "type": {"type": "shot-off-target"},
                "team": {"displayName": "France"},
                "participants": [{"athlete": {"displayName": "Ousmane Dembélé"}}],
            },
        }
        snapshot = match_snapshot(players={"kylian mbappé": mbappe})

        close_alert = watcher.alert_for_espn_commentary(close_shot, snapshot, espn_config())
        routine_alert = watcher.alert_for_espn_commentary(routine_shot, snapshot, espn_config())

        self.assertIsNotNone(close_alert)
        self.assertEqual(close_alert.kind, "espn_close_miss")
        self.assertIn("姆巴佩", close_alert.speech)
        self.assertIsNone(routine_alert)

    def test_cards_corner_and_opponent_free_kick_replace_small_foul(self):
        state = watcher.ESPNState(initialized=True, last_status_state="in")
        commentary = [
            {
                "sequence": 11,
                "time": {"displayValue": "51'"},
                "text": "France is shown the yellow card for a bad foul.",
                "play": {
                    "type": {"type": "yellow-card"},
                    "team": {"displayName": "France"},
                },
            },
            {
                "sequence": 12,
                "time": {"displayValue": "52'"},
                "text": "Corner, France.",
                "play": {
                    "type": {"type": "corner-awarded"},
                    "team": {"displayName": "France"},
                },
            },
            {
                "sequence": 13,
                "time": {"displayValue": "53'"},
                "text": "Morocco wins a free kick.",
                "play": {
                    "type": {"type": "foul"},
                    "team": {"displayName": "France"},
                },
            },
            {
                "sequence": 14,
                "time": {"displayValue": "53'"},
                "text": "Foul by France.",
                "play": {
                    "type": {"type": "foul"},
                    "team": {"displayName": "France"},
                },
            },
        ]

        alerts = watcher.evaluate_espn_match(match_snapshot(commentary), espn_config(), state)

        self.assertEqual(
            [alert.kind for alert in alerts],
            ["espn_yellow_card", "espn_corner", "espn_opponent_free_kick"],
        )
        self.assertEqual([alert.clip_id for alert in alerts], ["yellow-card", "corner", None])

    def test_only_opponent_free_kick_is_announced(self):
        state = watcher.ESPNState(initialized=True, last_status_state="in")
        ounahi = watcher.MatchPlayer("8", "Azzedine Ounahi", "A. Ounahi", "8", "Morocco", "MAR")
        commentary = [
            {
                "sequence": 12,
                "time": {"displayValue": "15'"},
                "text": "Azzedine Ounahi (Morocco) wins a free kick on the right wing.",
                "play": {
                    "type": {"type": "foul"},
                    "team": {"displayName": "France"},
                    "participants": [
                        {"athlete": {"displayName": "Lucas Digne"}},
                        {"athlete": {"displayName": "Azzedine Ounahi"}},
                    ],
                },
            },
            {
                "sequence": 13,
                "time": {"displayValue": "15'"},
                "text": "Foul by Lucas Digne (France).",
                "play": {
                    "type": {"type": "foul"},
                    "team": {"displayName": "France"},
                    "participants": [
                        {"athlete": {"displayName": "Lucas Digne"}},
                        {"athlete": {"displayName": "Azzedine Ounahi"}},
                    ],
                },
            },
            {
                "sequence": 14,
                "time": {"displayValue": "16'"},
                "text": "Manu Koné (France) wins a free kick in the attacking half.",
                "play": {
                    "type": {"type": "foul"},
                    "team": {"displayName": "Morocco"},
                    "participants": [
                        {"athlete": {"displayName": "Neil El Aynaoui"}},
                        {"athlete": {"displayName": "Manu Koné"}},
                    ],
                },
            },
        ]

        alerts = watcher.evaluate_espn_match(
            match_snapshot(commentary, players={"azzedine ounahi": ounahi}),
            espn_config(),
            state,
        )

        self.assertEqual([alert.kind for alert in alerts], ["espn_opponent_free_kick"])
        self.assertEqual(alerts[0].priority, 740)
        self.assertIn("摩洛哥赢得右路任意球", alerts[0].speech)
        self.assertIn("Azzedine Ounahi造到犯规", alerts[0].speech)
        self.assertNotIn("号球员", alerts[0].speech)

    def test_penalty_drawn_from_foul_commentary_is_announced(self):
        mbappe = watcher.MatchPlayer("231388", "Kylian Mbappé", "K. Mbappé", "10", "France", "FRA")
        penalty = {
            "sequence": 21,
            "time": {"displayValue": "25'"},
            "text": "Penalty France. Kylian Mbappé draws a foul in the penalty area.",
            "play": {
                "type": {"type": "foul"},
                "team": {"displayName": "Morocco"},
                "participants": [
                    {"athlete": {"displayName": "Noussair Mazraoui"}},
                    {"athlete": {"displayName": "Kylian Mbappé"}},
                ],
            },
        }

        alert = watcher.alert_for_espn_commentary(
            penalty,
            match_snapshot([penalty], players={"kylian mbappé": mbappe}),
            espn_config(),
        )

        self.assertIsNotNone(alert)
        self.assertEqual(alert.kind, "espn_penalty_awarded")
        self.assertIn("姆巴佩在禁区内造点", alert.speech)

    def test_injury_substitution_names_player_on_and_player_off(self):
        mateta = watcher.MatchPlayer(
            "1", "Jean-Philippe Mateta", "J. Mateta", "15", "France", "FRA"
        )
        mbappe = watcher.MatchPlayer(
            "231388", "Kylian Mbappé", "K. Mbappé", "10", "France", "FRA"
        )
        substitution = {
            "sequence": 71,
            "time": {"displayValue": "77'"},
            "text": (
                "Substitution, France. Jean-Philippe Mateta replaces Kylian Mbappé "
                "because of an injury."
            ),
            "play": {
                "type": {"type": "substitution"},
                "team": {"displayName": "France"},
                "participants": [
                    {"athlete": {"displayName": "Jean-Philippe Mateta"}},
                    {"athlete": {"displayName": "Kylian Mbappé"}},
                ],
            },
        }
        snapshot = match_snapshot(
            [substitution],
            players={"jean-philippe mateta": mateta, "kylian mbappé": mbappe},
        )

        alert = watcher.alert_for_espn_commentary(substitution, snapshot, espn_config())

        self.assertIsNotNone(alert)
        self.assertEqual(alert.kind, "espn_substitution")
        self.assertEqual(alert.priority, 620)
        self.assertIn("Jean-Philippe Mateta登场", alert.speech)
        self.assertIn("换下姆巴佩", alert.speech)
        self.assertNotIn("号球员", alert.speech)
        self.assertIn("姆巴佩因伤被换下", alert.speech)


class CommentaryStyleTests(unittest.TestCase):
    def _players(self):
        mbappe = watcher.MatchPlayer(
            "231388", "Kylian Mbappé", "K. Mbappé", "10", "France", "FRA"
        )
        dembele = watcher.MatchPlayer(
            "229744", "Ousmane Dembélé", "O. Dembélé", "11", "France", "FRA"
        )
        bounou = watcher.MatchPlayer(
            "3", "Yassine Bounou", "Y. Bounou", "1", "Morocco", "MAR", "G"
        )
        return {
            "kylian mbappé": mbappe,
            "ousmane dembélé": dembele,
            "yassine bounou": bounou,
        }

    def test_core_facts_are_present_for_representative_events_in_all_styles(self):
        cases = [
            (
                "goal",
                {
                    "time": {"displayValue": "32'"},
                    "text": "Goal! France 1, Morocco 0. Kylian Mbappé scores.",
                    "play": {
                        "type": {"type": "goal"},
                        "team": {"displayName": "France"},
                        "participants": [{"athlete": {"displayName": "Kylian Mbappé"}}],
                    },
                },
                ("姆巴佩",),
                ("法国", "姆巴佩", "进球"),
                "in",
            ),
            (
                "penalty",
                {
                    "time": {"displayValue": "41'"},
                    "text": "Penalty scored. France. Kylian Mbappé scores.",
                    "play": {
                        "type": {"type": "penalty---scored"},
                        "team": {"displayName": "France"},
                        "participants": [{"athlete": {"displayName": "Kylian Mbappé"}}],
                    },
                },
                ("姆巴佩", "点球", "命中"),
                ("法国", "姆巴佩", "点球", "命中"),
                "in",
            ),
            (
                "red",
                {
                    "time": {"displayValue": "55'"},
                    "text": "Kylian Mbappé (France) is shown the red card.",
                    "play": {
                        "type": {"type": "red-card"},
                        "team": {"displayName": "France"},
                        "participants": [{"athlete": {"displayName": "Kylian Mbappé"}}],
                    },
                },
                ("姆巴佩", "罚下"),
                ("法国", "姆巴佩", "红牌"),
                "in",
            ),
            (
                "substitution",
                {
                    "time": {"displayValue": "66'"},
                    "text": "Substitution, France. Ousmane Dembélé replaces Kylian Mbappé.",
                    "play": {
                        "type": {"type": "substitution"},
                        "team": {"displayName": "France"},
                        "participants": [
                            {"athlete": {"displayName": "Ousmane Dembélé"}},
                            {"athlete": {"displayName": "Kylian Mbappé"}},
                        ],
                    },
                },
                ("登贝莱", "姆巴佩"),
                ("法国", "换人", "登贝莱", "姆巴佩"),
                "in",
            ),
            (
                "save",
                {
                    "time": {"displayValue": "71'"},
                    "text": (
                        "Attempt saved. Kylian Mbappé (France) right footed shot from "
                        "the centre of the box is saved by Yassine Bounou (Morocco)."
                    ),
                    "play": {
                        "type": {"type": "shot-on-target"},
                        "team": {"displayName": "France"},
                        "participants": [{"athlete": {"displayName": "Kylian Mbappé"}}],
                    },
                },
                ("姆巴佩", "布努", "扑"),
                ("法国", "姆巴佩", "布努", "扑出"),
                "in",
            ),
            (
                "final",
                {
                    "time": {"displayValue": "90+5'"},
                    "text": "Match ends, France 1, Morocco 0.",
                    "play": {"type": {"type": "full-time"}},
                },
                ("结束",),
                ("比赛结束",),
                "post",
            ),
        ]

        for style in ("casual", "balanced", "professional"):
            for name, item, expected_terms, balloon_terms, status in cases:
                config = espn_config()
                config.commentary_style = style
                snapshot = match_snapshot(
                    [item],
                    status=status,
                    players=self._players(),
                )
                alert = watcher.alert_for_espn_commentary(item, snapshot, config)
                with self.subTest(style=style, event=name):
                    self.assertIsNotNone(alert)
                    self.assertTrue(alert.speech.startswith("第"))
                    expected_score = (
                        "法国1比0战胜摩洛哥"
                        if name == "final"
                        else "法国1比0领先摩洛哥"
                    )
                    self.assertIn(expected_score, alert.speech)
                    self.assertIn("FRA 1-0 MAR", alert.balloon)
                    self.assertTrue(alert.balloon.startswith(item["time"]["displayValue"]))
                    for term in expected_terms:
                        if term == "姆巴佩" and style == "casual":
                            self.assertTrue(
                                "姆巴佩" in alert.speech or "姆总" in alert.speech
                            )
                        else:
                            self.assertIn(term, alert.speech)
                    for term in balloon_terms:
                        self.assertIn(term, alert.balloon)

    def test_style_changes_only_text_not_goal_behavior(self):
        item = {
            "time": {"displayValue": "32'"},
            "text": "Goal! France. Kylian Mbappé scores.",
            "play": {
                "type": {"type": "goal"},
                "team": {"displayName": "France"},
                "participants": [{"athlete": {"displayName": "Kylian Mbappé"}}],
            },
        }
        behavior = []
        balloons = []
        speeches = []
        for style in ("casual", "balanced", "professional"):
            config = espn_config()
            config.commentary_style = style
            alert = watcher.alert_for_espn_commentary(
                item,
                match_snapshot([item], players=self._players()),
                config,
            )
            behavior.append(
                (
                    alert.kind,
                    alert.priority,
                    alert.face,
                    alert.clip_id,
                    alert.light_rgb,
                    alert.celebration,
                    alert.prefer_dynamic_voice,
                )
            )
            balloons.append(alert.balloon)
            speeches.append(alert.speech)

        self.assertEqual(len(set(behavior)), 1)
        self.assertEqual(len(set(balloons)), 1)
        self.assertEqual(len(set(speeches)), 3)

    def test_goal_perspective_distinguishes_alignment_conflict_and_no_position(self):
        item = {
            "time": {"displayValue": "32'"},
            "text": "Goal! France. Kylian Mbappé scores.",
            "play": {
                "type": {"type": "goal"},
                "team": {"displayName": "France"},
                "participants": [{"athlete": {"displayName": "Kylian Mbappé"}}],
            },
        }
        snapshot = match_snapshot([item], players=self._players())
        cases = (
            ("France", "France", ("benefit", "benefit", "aligned")),
            ("France", "Morocco", ("benefit", "harm", "conflict")),
            ("Morocco", "France", ("harm", "benefit", "conflict")),
            ("France", "", ("benefit", "none", "support_only")),
            ("", "France", ("neutral", "benefit", "position_only")),
        )

        for favorite, position, expected in cases:
            config = espn_config()
            config.favorite_team = favorite
            config.position_team = position
            facts = watcher.parse_espn_event_facts(item, snapshot, config)
            perspective = watcher.event_perspective(snapshot, config, facts, "espn_goal")
            with self.subTest(favorite=favorite, position=position):
                self.assertEqual(
                    (
                        perspective.support_outcome,
                        perspective.position_outcome,
                        perspective.alignment,
                    ),
                    expected,
                )

    def test_final_score_uses_result_language_instead_of_live_lead_language(self):
        item = {
            "time": {"displayValue": "90+5'"},
            "text": "Match ends, France 1, Morocco 0.",
            "play": {"type": {"type": "full-time"}},
        }
        draw_item = {
            **item,
            "text": "Match ends, France 1, Morocco 1.",
        }
        config = espn_config()

        winner = watcher.alert_for_espn_commentary(
            item,
            match_snapshot([item], status="post", home_score="1", away_score="0"),
            config,
        )
        draw = watcher.alert_for_espn_commentary(
            draw_item,
            match_snapshot(
                [draw_item], status="post", home_score="1", away_score="1"
            ),
            config,
        )

        self.assertIn("法国1比0战胜摩洛哥", winner.speech)
        self.assertNotIn("领先摩洛哥", winner.speech)
        self.assertIn("法国与摩洛哥1比1战平", draw.speech)

    def test_same_poll_goals_keep_each_rows_score_in_all_styles(self):
        first = {
            "sequence": 1,
            "time": {"displayValue": "18'"},
            "text": "Goal! France 1, Morocco 0. Kylian Mbappé scores.",
            "play": {
                "type": {"type": "goal"},
                "team": {"displayName": "France"},
                "participants": [
                    {
                        "athlete": {
                            "id": "231388",
                            "displayName": "Kylian Mbappé",
                        }
                    }
                ],
            },
        }
        second = {
            "sequence": 2,
            "time": {"displayValue": "24'"},
            "text": "Goal! France 1, Morocco 1. Hakim Ziyech scores.",
            "play": {
                "type": {"type": "goal"},
                "team": {"displayName": "Morocco"},
                "participants": [
                    {"athlete": {"displayName": "Hakim Ziyech"}}
                ],
            },
        }

        for style in ("casual", "balanced", "professional"):
            config = espn_config()
            config.commentary_style = style
            alerts = watcher.evaluate_espn_match(
                match_snapshot(
                    [first, second],
                    home_score="1",
                    away_score="1",
                    players=self._players(),
                ),
                config,
                watcher.ESPNState(initialized=True, last_status_state="in"),
            )

            with self.subTest(language="zh", style=style):
                self.assertEqual(len(alerts), 2)
                self.assertIn("法国1比0领先摩洛哥", alerts[0].speech)
                self.assertIn("FRA 1-0 MAR", alerts[0].balloon)
                self.assertNotIn("扳平", alerts[0].speech)
                self.assertNotIn("1比1", alerts[0].speech)
                self.assertIn("法国和摩洛哥打成1比1", alerts[1].speech)
                self.assertIn("FRA 1-1 MAR", alerts[1].balloon)
                self.assertIn("扳平", alerts[1].speech)
                self.assertNotIn("1比0", alerts[1].speech)

        for style in ("casual", "balanced", "professional"):
            config = english_espn_config()
            config.commentary_style = style
            alerts = watcher.evaluate_espn_match(
                match_snapshot(
                    [first, second],
                    home_score="1",
                    away_score="1",
                    players=self._players(),
                ),
                config,
                watcher.ESPNState(initialized=True, last_status_state="in"),
            )

            with self.subTest(language="en", style=style):
                self.assertEqual(len(alerts), 2)
                self.assertIn("It is France 1, Morocco 0", alerts[0].speech)
                self.assertNotIn("It is France 1, Morocco 1", alerts[0].speech)
                self.assertIn("It is France 1, Morocco 1", alerts[1].speech)
                self.assertNotIn("It is France 1, Morocco 0", alerts[1].speech)
                if style == "balanced":
                    self.assertEqual(alerts[0].speech.count("It is France"), 1)
                    self.assertEqual(alerts[1].speech.count("It is France"), 1)

    def test_historical_goal_drops_later_shootout_score(self):
        goal = {
            "sequence": 1,
            "time": {"displayValue": "18'"},
            "text": "Goal! France 1, Morocco 0. Kylian Mbappé scores.",
            "play": {
                "type": {"type": "goal"},
                "team": {"displayName": "France"},
                "participants": [
                    {
                        "athlete": {
                            "id": "231388",
                            "displayName": "Kylian Mbappé",
                        }
                    }
                ],
            },
        }
        snapshot = match_snapshot(
            [goal],
            status="post",
            home_score="1",
            away_score="1",
            players=self._players(),
        )
        snapshot.home.shootout_score = "4"
        snapshot.away.shootout_score = "3"

        alert = watcher.alert_for_espn_commentary(goal, snapshot, espn_config())

        self.assertIn("法国1比0领先摩洛哥", alert.speech)
        self.assertIn("FRA 1-0 MAR", alert.balloon)
        for stale_final_fact in ("点球比分", "(4)", "(3)", "1比1"):
            self.assertNotIn(stale_final_fact, alert.speech)
            self.assertNotIn(stale_final_fact, alert.balloon)

        shootout_end = {
            "sequence": 2,
            "time": {"displayValue": "120'"},
            "text": "Penalty shootout ends. France win on penalties.",
            "play": {"type": {"type": "full-time"}},
        }
        shootout_alert = watcher.alert_for_espn_commentary(
            shootout_end, snapshot, espn_config()
        )

        self.assertIn("法国和摩洛哥比赛战成1比1", shootout_alert.speech)
        self.assertIn("点球比分4比3", shootout_alert.speech)
        self.assertNotIn("常规时间", shootout_alert.speech)

    def test_parenthesized_espn_shootout_score_is_kept_per_kick(self):
        snapshot = match_snapshot([], status="in", home_score="1", away_score="1")
        snapshot.home.shootout_score = "3"
        snapshot.away.shootout_score = "4"
        rows = (
            (
                "Goal! France 1, Morocco 1(1). Penalty scored.",
                "FRA 1(0)-1(1) MAR",
                "点球比分0比1",
            ),
            (
                "Goal! France 1(1), Morocco 1(2). Penalty scored.",
                "FRA 1(1)-1(2) MAR",
                "点球比分1比2",
            ),
        )

        for sequence, (text, compact_score, spoken_score) in enumerate(rows, start=1):
            item = {
                "sequence": sequence,
                "time": {"displayValue": "120'"},
                "text": text,
                "play": {
                    "type": {"type": "penalty---scored"},
                    "team": {"displayName": "Morocco"},
                },
            }
            alert = watcher.alert_for_espn_commentary(item, snapshot, espn_config())
            with self.subTest(sequence=sequence):
                self.assertIn(compact_score, alert.balloon)
                self.assertIn(spoken_score, alert.speech)
                self.assertNotIn("点球比分3比4", alert.speech)

        final = {
            "sequence": 3,
            "text": "Match ends, France 1(3), Morocco 1(4).",
            "play": {"type": {"type": "full-time"}},
        }
        final_alert = watcher.alert_for_espn_commentary(
            final,
            replace(snapshot, status_state="post"),
            espn_config(),
        )
        self.assertIn("点球比分3比4", final_alert.speech)
        self.assertIn("FRA 1(3)-1(4) MAR", final_alert.balloon)

    def test_opponent_events_avoid_supportive_core_language(self):
        goal = {
            "time": {"displayValue": "18'"},
            "text": "Goal! France 1, Morocco 0. Kylian Mbappé scores.",
            "play": {
                "type": {"type": "goal"},
                "team": {"displayName": "France"},
                "participants": [
                    {
                        "athlete": {
                            "id": "231388",
                            "displayName": "Kylian Mbappé",
                        }
                    }
                ],
            },
        }
        penalty_miss = {
            "time": {"displayValue": "39'"},
            "text": "Penalty missed. Kylian Mbappé (France).",
            "play": {
                "type": {"type": "penalty---missed"},
                "team": {"displayName": "France"},
                "participants": [
                    {
                        "athlete": {
                            "id": "231388",
                            "displayName": "Kylian Mbappé",
                        }
                    }
                ],
            },
        }
        snapshot = match_snapshot(
            [goal, penalty_miss], players=self._players()
        )

        for style in ("casual", "balanced", "professional"):
            config = espn_config()
            config.commentary_style = style
            config.favorite_team = "Morocco"
            config.position_team = ""
            goal_alert = watcher.alert_for_espn_commentary(goal, snapshot, config)
            miss_alert = watcher.alert_for_espn_commentary(
                penalty_miss, snapshot, config
            )

            with self.subTest(style=style):
                self.assertNotIn("破门啦", goal_alert.speech)
                self.assertNotIn("可惜", miss_alert.speech)

    def test_casual_failed_attempts_use_formal_name_not_nickname(self):
        yamal = watcher.MatchPlayer(
            "362150", "Lamine Yamal", "L. Yamal", "19", "France", "FRA"
        )
        missed_shot = {
            "time": {"displayValue": "28'"},
            "text": (
                "Attempt missed. Lamine Yamal (France) left footed shot from "
                "the centre of the box is close, but misses to the left."
            ),
            "play": {
                "type": {"type": "shot-off-target"},
                "team": {"displayName": "France"},
                "participants": [
                    {
                        "athlete": {
                            "id": "362150",
                            "displayName": "Lamine Yamal",
                        }
                    }
                ],
            },
        }
        penalty_miss = {
            "time": {"displayValue": "39'"},
            "text": "Penalty missed. Lamine Yamal (France).",
            "play": {
                "type": {"type": "penalty---missed"},
                "team": {"displayName": "France"},
                "participants": [
                    {
                        "athlete": {
                            "id": "362150",
                            "displayName": "Lamine Yamal",
                        }
                    }
                ],
            },
        }
        config = espn_config()
        config.commentary_style = "casual"
        config.player_names = {}
        config.star_chants = {}
        snapshot = match_snapshot(
            [missed_shot, penalty_miss], players={"362150": yamal}
        )

        for item in (missed_shot, penalty_miss):
            alert = watcher.alert_for_espn_commentary(item, snapshot, config)
            with self.subTest(play_type=item["play"]["type"]["type"]):
                self.assertIn("亚马尔", alert.speech)
                self.assertNotIn("小孩哥", alert.speech)

    def test_professional_failed_attack_uses_delivery_not_assist(self):
        config = espn_config()
        config.commentary_style = "professional"
        players = self._players()
        cases = (
            ("with a cross", "登贝莱送出传中"),
            ("with a through ball", "登贝莱送出直塞"),
            ("", "登贝莱送出传球"),
        )

        for delivery, expected in cases:
            suffix = f" {delivery}" if delivery else ""
            item = {
                "time": {"displayValue": "42'"},
                "text": (
                    "Attempt saved. Kylian Mbappé (France) right footed shot "
                    "from the centre of the box is saved by Yassine Bounou "
                    f"(Morocco). Assisted by Ousmane Dembélé{suffix}."
                ),
                "play": {
                    "type": {"type": "shot-on-target"},
                    "team": {"displayName": "France"},
                    "participants": [
                        {
                            "athlete": {
                                "id": "231388",
                                "displayName": "Kylian Mbappé",
                            }
                        },
                        {
                            "athlete": {
                                "id": "229744",
                                "displayName": "Ousmane Dembélé",
                            }
                        },
                    ],
                },
            }
            alert = watcher.alert_for_espn_commentary(
                item, match_snapshot([item], players=players), config
            )

            with self.subTest(delivery=delivery or "pass"):
                self.assertIn(expected, alert.speech)
                self.assertNotIn("助攻", alert.speech)

    def test_professional_unknown_player_yellow_card_has_clean_subject(self):
        item = {
            "time": {"displayValue": "51'"},
            "text": "A France player is shown the yellow card.",
            "play": {
                "type": {"type": "yellow-card"},
                "team": {"displayName": "France"},
            },
        }
        config = espn_config()
        config.commentary_style = "professional"

        alert = watcher.alert_for_espn_commentary(
            item, match_snapshot([item]), config
        )

        self.assertIn("法国一名球员", alert.speech)
        self.assertNotIn("球员场上球员", alert.speech)

    def test_all_styles_voice_major_support_and_position_context_naturally(self):
        item = {
            "time": {"displayValue": "32'"},
            "text": "Goal! France. Kylian Mbappé scores.",
            "play": {
                "type": {"type": "goal"},
                "team": {"displayName": "France"},
                "participants": [{"athlete": {"displayName": "Kylian Mbappé"}}],
            },
        }
        snapshot = match_snapshot([item], players=self._players())
        for style in ("casual", "balanced", "professional"):
            config = espn_config()
            config.favorite_team = "Morocco"
            config.position_team = "France"
            config.commentary_style = style
            speech = watcher.alert_for_espn_commentary(item, snapshot, config).speech
            with self.subTest(style=style):
                self.assertTrue("姆巴佩" in speech or "姆总" in speech)
                for fact in ("法国", "法国1比0领先摩洛哥", "摩洛哥", "仓位"):
                    self.assertIn(fact, speech)
                for mechanical in ("法国的姆巴佩", "现在法国", "家人们", "号球员"):
                    self.assertNotIn(mechanical, speech)

    def test_routine_save_uses_support_view_without_position_commentary(self):
        item = {
            "time": {"displayValue": "71'"},
            "text": (
                "Attempt saved. Kylian Mbappé (France) right footed shot from the "
                "centre of the box is saved by Yassine Bounou (Morocco)."
            ),
            "play": {
                "type": {"type": "shot-on-target"},
                "team": {"displayName": "France"},
                "participants": [{"athlete": {"displayName": "Kylian Mbappé"}}],
            },
        }
        snapshot = match_snapshot([item], players=self._players())
        for favorite, support_phrase in (("France", "法国"), ("Morocco", "摩洛哥")):
            for style in ("casual", "balanced", "professional"):
                config = espn_config()
                config.favorite_team = favorite
                config.position_team = favorite
                config.commentary_style = style
                speech = watcher.alert_for_espn_commentary(item, snapshot, config).speech
                with self.subTest(favorite=favorite, style=style):
                    self.assertIn(support_phrase, speech)
                    self.assertNotIn("仓位", speech)

    def test_professional_goal_and_save_use_only_reliably_parsed_details(self):
        players = self._players()
        goal = {
            "time": {"displayValue": "32'"},
            "text": (
                "Goal! Kylian Mbappé (France) header from the centre of the box to the "
                "bottom right corner. Assisted by Ousmane Dembélé with a cross."
            ),
            "play": {
                "type": {"type": "goal"},
                "team": {"displayName": "France"},
                "participants": [
                    {"athlete": {"displayName": "Kylian Mbappé"}},
                    {"athlete": {"displayName": "Ousmane Dembélé"}},
                ],
            },
        }
        saved = {
            "time": {"displayValue": "73'"},
            "text": (
                "Attempt saved. Kylian Mbappé (France) right footed low shot from the centre "
                "of the box towards the bottom right corner is saved by Yassine Bounou."
            ),
            "play": {
                "type": {"type": "shot-on-target"},
                "team": {"displayName": "France"},
                "participants": [{"athlete": {"displayName": "Kylian Mbappé"}}],
            },
        }
        config = espn_config()
        config.commentary_style = "professional"
        snapshot = match_snapshot([goal, saved], players=players)

        facts = watcher.parse_espn_event_facts(goal, snapshot, config)
        goal_alert = watcher.alert_for_espn_commentary(goal, snapshot, config)
        save_alert = watcher.alert_for_espn_commentary(saved, snapshot, config)

        self.assertEqual(facts.delivery, "cross")
        self.assertEqual(facts.shot_body_part, "header")
        self.assertEqual(facts.shot_area, "centre_of_box")
        self.assertEqual(facts.shot_direction, "bottom_right")
        self.assertIn("登贝莱传中助攻", goal_alert.speech)
        self.assertIn("姆巴佩在禁区中路头球攻门", goal_alert.speech)
        self.assertIn("球门右下角", goal_alert.speech)
        self.assertIn("布努", save_alert.speech)
        self.assertIn("右脚低射", save_alert.speech)
        self.assertIn("射门攻向球门右下角", save_alert.speech)
        self.assertNotIn("Assisted by", goal_alert.speech)
        self.assertLess(goal_alert.speech.index("完成破门"), goal_alert.speech.index("传中助攻"))
        self.assertLess(goal_alert.speech.index("法国1比0领先摩洛哥"), goal_alert.speech.index("传中助攻"))
        self.assertLess(save_alert.speech.index("法国1比0领先摩洛哥"), save_alert.speech.index("射门攻向"))
        self.assertGreater(len(goal_alert.speech), len(goal_alert.balloon))

    def test_professional_goal_identifies_explicit_far_post_equalizer(self):
        item = {
            "time": {"displayValue": "83'"},
            "text": (
                "Goal! Kylian Mbappé (France) header at the far post to the bottom left corner."
            ),
            "play": {
                "type": {"type": "goal"},
                "team": {"displayName": "France"},
                "participants": [{"athlete": {"displayName": "Kylian Mbappé"}}],
            },
        }
        config = espn_config()
        config.commentary_style = "professional"
        snapshot = match_snapshot(
            [item],
            home_score="1",
            away_score="1",
            players=self._players(),
        )

        facts = watcher.parse_espn_event_facts(item, snapshot, config)
        alert = watcher.alert_for_espn_commentary(item, snapshot, config)

        self.assertTrue(facts.is_equalizer)
        self.assertEqual(facts.shot_area, "far_post")
        self.assertIn("扳平比分", alert.speech)
        self.assertIn("后点包抄", alert.speech)
        self.assertIn("法国和摩洛哥打成1比1", alert.speech)

    def test_professional_ignores_unrecognized_prose_instead_of_guessing(self):
        item = {
            "time": {"displayValue": "20'"},
            "text": "Goal! France. Kylian Mbappé finishes a wonderful move in style.",
            "play": {
                "type": {"type": "goal"},
                "team": {"displayName": "France"},
                "participants": [{"athlete": {"displayName": "Kylian Mbappé"}}],
            },
        }
        config = espn_config()
        config.commentary_style = "professional"

        alert = watcher.alert_for_espn_commentary(
            item,
            match_snapshot([item], players=self._players()),
            config,
        )

        for guessed_detail in ("头球", "左脚", "右脚", "低射", "禁区中路", "助攻"):
            self.assertNotIn(guessed_detail, alert.speech)
        self.assertNotIn("wonderful move", alert.speech)

    def test_professional_card_and_miss_do_not_infer_unstated_reasons_or_closeness(self):
        config = espn_config()
        config.commentary_style = "professional"
        yellow = {
            "time": {"displayValue": "18'"},
            "text": "Kylian Mbappé (France) is shown the yellow card.",
            "play": {
                "type": {"type": "yellow-card"},
                "team": {"displayName": "France"},
                "participants": [{"athlete": {"displayName": "Kylian Mbappé"}}],
            },
        }
        ordinary_miss = {
            "time": {"displayValue": "22'"},
            "text": (
                "Attempt missed. Kylian Mbappé left footed shot from the centre of the "
                "box misses to the left."
            ),
            "play": {
                "type": {"type": "shot-off-target"},
                "team": {"displayName": "France"},
                "participants": [{"athlete": {"displayName": "Kylian Mbappé"}}],
            },
        }
        close_miss = {
            **ordinary_miss,
            "time": {"displayValue": "24'"},
            "text": ordinary_miss["text"].replace("misses to the left", "is close but misses to the left"),
        }
        snapshot = match_snapshot(
            [yellow, ordinary_miss, close_miss],
            players=self._players(),
        )

        yellow_alert = watcher.alert_for_espn_commentary(yellow, snapshot, config)
        ordinary_alert = watcher.alert_for_espn_commentary(ordinary_miss, snapshot, config)
        close_alert = watcher.alert_for_espn_commentary(close_miss, snapshot, config)

        self.assertIn("黄牌警告", yellow_alert.speech)
        self.assertNotIn("犯规", yellow_alert.speech)
        self.assertIn("偏出球门", ordinary_alert.speech)
        self.assertNotIn("擦着门边偏出", ordinary_alert.speech)
        self.assertIn("擦着门边偏出", close_alert.speech)

    def test_player_name_switch_is_honored_in_styled_templates(self):
        item = {
            "time": {"displayValue": "32'"},
            "text": "Goal! France. Kylian Mbappé scores.",
            "play": {
                "type": {"type": "goal"},
                "team": {"displayName": "France"},
                "participants": [{"athlete": {"displayName": "Kylian Mbappé"}}],
            },
        }
        for style in ("casual", "professional"):
            config = espn_config()
            config.commentary_style = style
            config.announce_player_names = False
            alert = watcher.alert_for_espn_commentary(
                item,
                match_snapshot([item], players=self._players()),
                config,
            )
            with self.subTest(style=style):
                self.assertNotIn("姆巴佩", alert.speech)
                self.assertIn("法国", alert.speech)

    def test_professional_substitution_core_and_score_precede_injury_reason(self):
        item = {
            "time": {"displayValue": "77'"},
            "text": (
                "Substitution, France. Ousmane Dembélé replaces Kylian Mbappé "
                "because of an injury."
            ),
            "play": {
                "type": {"type": "substitution"},
                "team": {"displayName": "France"},
                "participants": [
                    {"athlete": {"displayName": "Ousmane Dembélé"}},
                    {"athlete": {"displayName": "Kylian Mbappé"}},
                ],
            },
        }
        config = espn_config()
        config.commentary_style = "professional"
        alert = watcher.alert_for_espn_commentary(
            item,
            match_snapshot([item], players=self._players()),
            config,
        )

        self.assertLess(alert.speech.index("完成换人"), alert.speech.index("法国1比0领先摩洛哥"))
        self.assertLess(alert.speech.index("法国1比0领先摩洛哥"), alert.speech.index("因伤"))

    def test_chinese_substitution_balloon_finishes_before_default_hide(self):
        mateta = watcher.MatchPlayer(
            "4", "Jean-Philippe Mateta", "J. Mateta", "15", "France", "FRA"
        )
        mbappe = self._players()["kylian mbappé"]
        item = {
            "time": {"displayValue": "77'"},
            "text": "Substitution, France. Jean-Philippe Mateta replaces Kylian Mbappé.",
            "play": {
                "type": {"type": "substitution"},
                "team": {"displayName": "France"},
                "participants": [
                    {"athlete": {"displayName": "Jean-Philippe Mateta"}},
                    {"athlete": {"displayName": "Kylian Mbappé"}},
                ],
            },
        }
        alert = watcher.alert_for_espn_commentary(
            item,
            match_snapshot(
                [item],
                players={"jean-philippe mateta": mateta, "kylian mbappé": mbappe},
            ),
            espn_config(),
        )

        for fact in ("77'", "法国", "Mateta", "姆巴佩", "FRA 1-0 MAR"):
            self.assertIn(fact, alert.balloon)
        estimated_width_px = sum(
            24 if "\u3400" <= character <= "\u9fff" else 11.1
            for character in alert.balloon
        )
        estimated_marquee_seconds = 1.4 + max(0, estimated_width_px - 248) / 42
        self.assertLessEqual(estimated_marquee_seconds, 8)

    def test_style_switch_is_immediate_without_replaying_seen_events(self):
        first = {
            "sequence": 1,
            "time": {"displayValue": "10'"},
            "text": "Goal! France. Kylian Mbappé scores.",
            "play": {
                "type": {"type": "goal"},
                "team": {"displayName": "France"},
                "participants": [{"athlete": {"displayName": "Kylian Mbappé"}}],
            },
        }
        second = {
            **first,
            "sequence": 2,
            "time": {"displayValue": "12'"},
        }
        config = espn_config()
        config.commentary_style = "professional"
        state = watcher.ESPNState(initialized=True, last_status_state="in")
        snapshot = match_snapshot([first], players=self._players())

        first_alerts = watcher.evaluate_espn_match(snapshot, config, state)
        config.commentary_style = "casual"
        second_alerts = watcher.evaluate_espn_match(
            match_snapshot([first, second], players=self._players()),
            config,
            state,
        )

        self.assertEqual(len(first_alerts), 1)
        self.assertEqual(len(second_alerts), 1)
        self.assertIn("姆总", second_alerts[0].speech)
        self.assertIn("打进去了", second_alerts[0].speech)
        self.assertIn("法国1比0领先摩洛哥", second_alerts[0].speech)
        self.assertNotIn("家人们", second_alerts[0].speech)
        self.assertTrue(second_alerts[0].speech.startswith("第12分钟"))

    def test_status_fallback_uses_explicit_snapshot_clock(self):
        config = espn_config()
        config.commentary_style = "professional"

        alert = watcher._status_change_alert(match_snapshot(status="in"), config)

        self.assertTrue(alert.speech.startswith("第32分钟"))
        self.assertTrue(alert.balloon.startswith("32'"))
        self.assertIn("FRA 1-0 MAR", alert.balloon)


class EnglishAlertTests(unittest.TestCase):
    def test_player_number_is_grammatical_inside_possessive_templates(self):
        player = watcher.MatchPlayer(
            "4", "Dayot Upamecano", "D. Upamecano", "4", "France", "FRA"
        )

        self.assertEqual(
            watcher.player_announcement(english_espn_config(), player),
            "number 4 Dayot Upamecano",
        )

    def test_professional_goal_and_save_have_natural_english_sentences(self):
        mbappe = watcher.MatchPlayer(
            "1", "Kylian Mbappé", "K. Mbappé", "10", "France", "FRA"
        )
        dembele = watcher.MatchPlayer(
            "2", "Ousmane Dembélé", "O. Dembélé", "11", "France", "FRA"
        )
        bounou = watcher.MatchPlayer(
            "3", "Yassine Bounou", "Y. Bounou", "1", "Morocco", "MAR", "G"
        )
        players = {
            "kylian mbappé": mbappe,
            "ousmane dembélé": dembele,
            "yassine bounou": bounou,
        }
        goal = {
            "time": {"displayValue": "32'"},
            "text": (
                "Goal! Kylian Mbappé right footed shot from the centre of the box to "
                "the top left corner. Assisted by Ousmane Dembélé with a cross."
            ),
            "play": {
                "type": {"type": "goal"},
                "team": {"displayName": "France"},
                "participants": [
                    {"athlete": {"displayName": "Kylian Mbappé"}},
                    {"athlete": {"displayName": "Ousmane Dembélé"}},
                ],
            },
        }
        saved = {
            "time": {"displayValue": "38'"},
            "text": (
                "Attempt saved. Kylian Mbappé right footed shot from the centre of the "
                "box is saved by Yassine Bounou."
            ),
            "play": {
                "type": {"type": "shot-on-target"},
                "team": {"displayName": "France"},
                "participants": [{"athlete": {"displayName": "Kylian Mbappé"}}],
            },
        }
        config = english_espn_config()
        config.commentary_style = "professional"
        snapshot = match_snapshot([goal, saved], players=players)

        goal_alert = watcher.alert_for_espn_commentary(goal, snapshot, config)
        save_alert = watcher.alert_for_espn_commentary(saved, snapshot, config)

        self.assertIn("Ousmane Dembele supplies the assist with a cross.", goal_alert.speech)
        self.assertIn("The finish is a right-footed shot from the centre of the box.", goal_alert.speech)
        self.assertIn("tries a right-footed shot from the centre of the box", save_alert.speech)
        self.assertNotIn("Dembelesupplies", goal_alert.speech)
        self.assertNotIn("'s a right-footed", save_alert.speech)
        self.assertNotIn("Assisted by", goal_alert.speech)

    def test_english_substitution_balloon_is_compact_but_complete(self):
        mateta = watcher.MatchPlayer(
            "4", "Jean-Philippe Mateta", "J. Mateta", "15", "France", "FRA"
        )
        mbappe = watcher.MatchPlayer(
            "1", "Kylian Mbappé", "K. Mbappé", "10", "France", "FRA"
        )
        item = {
            "time": {"displayValue": "77'"},
            "text": "Substitution, France. Jean-Philippe Mateta replaces Kylian Mbappé.",
            "play": {
                "type": {"type": "substitution"},
                "team": {"displayName": "France"},
                "participants": [
                    {"athlete": {"displayName": "Jean-Philippe Mateta"}},
                    {"athlete": {"displayName": "Kylian Mbappé"}},
                ],
            },
        }
        config = english_espn_config()
        alert = watcher.alert_for_espn_commentary(
            item,
            match_snapshot(
                [item],
                players={"jean-philippe mateta": mateta, "kylian mbappé": mbappe},
            ),
            config,
        )

        self.assertLessEqual(len(alert.balloon), 72)
        for fact in ("77'", "FRA", "Mateta", "Mbappe", "FRA 1-0 MAR"):
            self.assertIn(fact, alert.balloon)
        estimated_width_px = len(alert.balloon) * 11.1
        estimated_marquee_seconds = 1.4 + max(0, estimated_width_px - 248) / 42
        self.assertLessEqual(estimated_marquee_seconds, 8)

    def test_styled_penalty_save_uses_player_possessive_not_team_possessive(self):
        mbappe = watcher.MatchPlayer(
            "1", "Kylian Mbappé", "K. Mbappé", "10", "France", "FRA"
        )
        bounou = watcher.MatchPlayer(
            "3", "Yassine Bounou", "Y. Bounou", "1", "Morocco", "MAR", "G"
        )
        item = {
            "time": {"displayValue": "81'"},
            "text": "Penalty saved. Kylian Mbappé is denied by Yassine Bounou.",
            "play": {
                "type": {"type": "penalty---saved"},
                "team": {"displayName": "France"},
                "participants": [{"athlete": {"displayName": "Kylian Mbappé"}}],
            },
        }
        snapshot = match_snapshot(
            [item],
            players={"kylian mbappé": mbappe, "yassine bounou": bounou},
        )

        for style in ("casual", "professional"):
            config = english_espn_config()
            config.commentary_style = style
            alert = watcher.alert_for_espn_commentary(item, snapshot, config)
            with self.subTest(style=style):
                self.assertIn("Yassine Bounou saves Kylian Mbappe's penalty", alert.speech)
                self.assertNotIn("France's penalty", alert.speech)

    def test_free_kick_without_location_has_clean_spacing(self):
        item = {
            "sequence": 1,
            "text": "Morocco wins a free kick.",
            "play": {
                "type": {"type": "foul"},
                "team": {"displayName": "France"},
            },
        }

        alert = watcher.alert_for_espn_commentary(
            item,
            match_snapshot([item]),
            english_espn_config(),
        )

        self.assertEqual(
            alert.balloon,
            "MAR free kick | FRA 1-0 MAR",
        )
        self.assertEqual(
            alert.speech,
            (
                "Free kick to Morocco. The player draws the foul. "
                "It is France 1, Morocco 0. France must defend this carefully."
            ),
        )

    def test_representative_event_catalog_has_no_hardcoded_chinese(self):
        config = english_espn_config()
        config.announce_fouls = True
        cases = [
            (
                "drinks_break",
                {
                    "sequence": 1,
                    "text": "Delay in match for a drinks break.",
                    "play": {"type": {"type": "start-delay"}},
                },
            ),
            (
                "penalty_scored",
                {
                    "sequence": 2,
                    "text": "Penalty scored. France.",
                    "play": {
                        "type": {"type": "penalty---scored"},
                        "team": {"displayName": "France"},
                    },
                },
            ),
            (
                "penalty_missed",
                {
                    "sequence": 3,
                    "text": "Penalty missed. France.",
                    "play": {
                        "type": {"type": "penalty---missed"},
                        "team": {"displayName": "France"},
                    },
                },
            ),
            (
                "penalty_awarded",
                {
                    "sequence": 4,
                    "text": "Penalty awarded to France.",
                    "play": {
                        "type": {"type": "penalty-awarded"},
                        "team": {"displayName": "France"},
                    },
                },
            ),
            (
                "goal",
                {
                    "sequence": 5,
                    "text": "Goal! France 1, Morocco 0.",
                    "play": {
                        "type": {"type": "goal"},
                        "team": {"displayName": "France"},
                    },
                },
            ),
            (
                "substitution",
                {
                    "sequence": 6,
                    "text": "Substitution, France.",
                    "play": {
                        "type": {"type": "substitution"},
                        "team": {"displayName": "France"},
                    },
                },
            ),
            (
                "woodwork",
                {
                    "sequence": 7,
                    "text": "France hits the post from the centre of the box.",
                    "play": {
                        "type": {"type": "hit-woodwork"},
                        "team": {"displayName": "France"},
                    },
                },
            ),
            (
                "shot_saved",
                {
                    "sequence": 8,
                    "text": "Attempt saved. France header from very close range is saved.",
                    "play": {
                        "type": {"type": "shot-on-target"},
                        "team": {"displayName": "France"},
                    },
                },
            ),
            (
                "close_miss",
                {
                    "sequence": 9,
                    "text": "Attempt missed. France shot is close, but misses to the left.",
                    "play": {
                        "type": {"type": "shot-off-target"},
                        "team": {"displayName": "France"},
                    },
                },
            ),
            (
                "blocked_shot",
                {
                    "sequence": 10,
                    "text": "Attempt blocked. France shot from the centre of the box is blocked.",
                    "play": {
                        "type": {"type": "shot-blocked"},
                        "team": {"displayName": "France"},
                    },
                },
            ),
            (
                "red_card",
                {
                    "sequence": 11,
                    "text": "France is shown the red card.",
                    "play": {
                        "type": {"type": "red-card"},
                        "team": {"displayName": "France"},
                    },
                },
            ),
            (
                "yellow_card",
                {
                    "sequence": 12,
                    "text": "France is shown the yellow card.",
                    "play": {
                        "type": {"type": "yellow-card"},
                        "team": {"displayName": "France"},
                    },
                },
            ),
            (
                "corner",
                {
                    "sequence": 13,
                    "text": "Corner, France.",
                    "play": {
                        "type": {"type": "corner-awarded"},
                        "team": {"displayName": "France"},
                    },
                },
            ),
            (
                "free_kick",
                {
                    "sequence": 14,
                    "text": "Morocco wins a free kick on the right wing.",
                    "play": {
                        "type": {"type": "foul"},
                        "team": {"displayName": "France"},
                    },
                },
            ),
            (
                "foul",
                {
                    "sequence": 15,
                    "text": "Foul by France.",
                    "play": {
                        "type": {"type": "foul"},
                        "team": {"displayName": "France"},
                    },
                },
            ),
            (
                "second_half",
                {
                    "sequence": 16,
                    "text": "Second Half begins France 1, Morocco 0.",
                    "play": {"type": {"type": "kickoff"}},
                },
            ),
            (
                "full_time",
                {
                    "sequence": 17,
                    "text": "Match ends, France 2, Morocco 0.",
                    "play": {"type": {"type": "full-time"}},
                },
            ),
        ]

        alerts = {}
        for name, item in cases:
            snapshot = match_snapshot(
                [item],
                status="post" if name == "full_time" else "in",
                home_score="2" if name == "full_time" else "1",
            )
            alert = watcher.alert_for_espn_commentary(item, snapshot, config)
            with self.subTest(event=name):
                self.assertIsNotNone(alert)
                self.assertFalse(contains_han(alert.balloon))
                self.assertFalse(contains_han(alert.speech))
                self.assertFalse(contains_han(alert.label))
            alerts[name] = alert

        self.assertEqual(alerts["free_kick"].priority, 740)
        self.assertIn("on the right wing", alerts["free_kick"].speech)
        self.assertTrue(alerts["full_time"].is_final)
        self.assertTrue(watcher.is_final_status_alert(alerts["full_time"]))


class PersistentDisplayTests(unittest.TestCase):
    def test_finalized_outcomes_use_settlement_probability_not_empty_book_midpoint(self):
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
        )
        config = watcher.WatchConfig(probability_bar=bar, markets=[france, morocco])
        snapshots = {
            "FRA": watcher.MarketSnapshot(
                "FRA", "法国晋级", "finalized", "E", 0, 100, 0, 100, 99, "", None,
                result="yes", settlement_value_cents=100,
            ),
            "MAR": watcher.MarketSnapshot(
                "MAR", "摩洛哥晋级", "finalized", "E", 0, 100, 0, 100, 1, "", None,
                result="no", settlement_value_cents=0,
            ),
        }

        command = watcher.persistent_display_command(config, snapshots)

        self.assertEqual(command, "pkbar fr 100 0055A4 ma 0 C1272D")

    def test_probability_bar_normalizes_both_outcome_yes_midpoints(self):
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
        )
        config = watcher.WatchConfig(probability_bar=bar, markets=[france, morocco])
        snapshots = {
            "FRA": watcher.MarketSnapshot("FRA", "法国晋级", "active", "E", 77, 79, 21, 23, 78, "", None),
            "MAR": watcher.MarketSnapshot("MAR", "摩洛哥晋级", "active", "E", 23, 25, 75, 77, 24, "", None),
        }

        command = watcher.persistent_display_command(config, snapshots)

        self.assertEqual(command, "pkbar fr 76 0055A4 ma 24 C1272D")

    def test_probability_bar_uses_france_yes_and_binary_complement(self):
        france = watcher.MarketConfig("FRA", "法国晋级", show_in_ticker=True)
        morocco = watcher.MarketConfig("MAR", "摩洛哥晋级", show_in_ticker=False)
        bar = watcher.ProbabilityBarConfig(
            enabled=True,
            market_ticker="FRA",
            side="yes",
            left_flag="fr",
            left_color="#0055A4",
            right_flag="ma",
            right_color="#C1272D",
        )
        config = watcher.WatchConfig(probability_bar=bar, markets=[france, morocco])
        snapshots = {
            "FRA": watcher.MarketSnapshot("FRA", "法国晋级", "active", "E", 77, 78, 22, 23, 77, "", None),
            "MAR": watcher.MarketSnapshot("MAR", "摩洛哥晋级", "active", "E", 22, 23, 77, 78, 22, "", None),
        }

        command = watcher.persistent_display_command(config, snapshots)

        self.assertEqual(command, "pkbar fr 78 0055A4 ma 22 C1272D")


class MarketAlertTests(unittest.TestCase):
    def test_english_price_and_near_close_speech_use_singular_units(self):
        market = watcher.MarketConfig(
            "FRA",
            "France to advance",
            language="en",
            alert_move_cents=1,
            speak_move_cents=1,
            min_seconds_between_alerts=0,
        )
        now = datetime.now(timezone.utc)
        snapshot = watcher.MarketSnapshot(
            "FRA",
            "France to advance",
            "active",
            "E",
            0,
            2,
            98,
            100,
            1,
            "",
            now + timedelta(seconds=30),
        )
        state = watcher.MarketState(
            last_alert_mid_cents=0,
            last_observed_mid_cents=0,
        )

        alerts = watcher.evaluate_market(snapshot, market, state, now)
        near_close = next(alert for alert in alerts if alert.kind == "near_close")
        price_move = next(alert for alert in alerts if alert.kind == "price_move")

        self.assertIn("1 minute", near_close.speech)
        self.assertNotIn("1 minutes", near_close.speech)
        self.assertIn("midpoint is 1 cent", price_move.speech)
        self.assertIn("by 1 cent", price_move.speech)

    def test_default_english_goal_signal_contains_no_chinese(self):
        market = watcher.MarketConfig(
            "FRA",
            "France to advance",
            language="en",
            min_seconds_between_alerts=0,
            goal_signal_enabled=True,
            goal_signal_move_cents=5,
            goal_signal_cooldown_seconds=1,
        )
        snapshot = watcher.MarketSnapshot(
            "FRA", "France to advance", "active", "E", 97, 99, 1, 3, 98, "", None
        )
        state = watcher.MarketState(last_observed_mid_cents=90)

        alerts = watcher.evaluate_market(
            snapshot,
            market,
            state,
            datetime.now(timezone.utc),
        )

        signal = next(alert for alert in alerts if alert.kind == "market_goal_signal")
        self.assertFalse(contains_han(signal.balloon))
        self.assertFalse(contains_han(signal.speech))

    def test_market_price_move_uses_three_distinct_voice_styles(self):
        snapshot = watcher.MarketSnapshot(
            "FRA", "法国晋级", "active", "E", 75, 77, 23, 25, 76, "", None
        )
        speeches = {}
        for style in ("casual", "balanced", "professional"):
            market = watcher.MarketConfig(
                "FRA",
                "法国晋级",
                commentary_style=style,
                speak_move_cents=5,
            )
            speeches[style] = watcher.speech_for_price_move(snapshot, market, 6, 76)

        self.assertEqual(len(set(speeches.values())), 3)
        self.assertIn("提醒一下", speeches["casual"])
        self.assertNotIn("家人们", speeches["casual"])
        self.assertIn("中间价升至", speeches["professional"])
        self.assertNotIn("告警基线", speeches["professional"])
        for speech in speeches.values():
            self.assertIn("法国晋级", speech)
            self.assertIn("76", speech)
            self.assertIn("6", speech)
            self.assertNotIn("持仓", speech)

        tracked = watcher.MarketConfig(
            "FRA",
            "法国晋级",
            tracks_position=True,
            position_team="法国",
        )
        self.assertIn(
            "持仓",
            watcher.speech_for_price_move(snapshot, tracked, 6, 76),
        )

    def test_tracked_english_price_moves_name_position_in_all_styles(self):
        snapshot = watcher.MarketSnapshot(
            "FRA",
            "France to advance",
            "active",
            "E",
            75,
            77,
            23,
            25,
            76,
            "",
            None,
        )

        for style in ("casual", "balanced", "professional"):
            market = watcher.MarketConfig(
                "FRA",
                "France to advance",
                language="en",
                commentary_style=style,
                speak_move_cents=5,
                tracks_position=True,
                position_team="France",
            )
            rising = watcher.speech_for_price_move(snapshot, market, 6, 76)
            falling = watcher.speech_for_price_move(snapshot, market, -6, 64)

            with self.subTest(style=style):
                self.assertIn("position", rising.casefold())
                self.assertIn("benefit", rising.casefold())
                self.assertIn("position", falling.casefold())
                self.assertIn("pressure", falling.casefold())
                if style == "professional":
                    self.assertIn("a sharp upward move", rising)
                    self.assertNotIn("a sharply upward move", rising)

    def test_suspected_goal_styles_name_team_both_directions_and_keep_uncertainty(self):
        for style in ("casual", "balanced", "professional"):
            for rising, midpoint, expected_team in (
                (True, 60, "法国"),
                (False, 40, "摩洛哥"),
            ):
                market = watcher.MarketConfig(
                    "FRA",
                    "法国晋级",
                    commentary_style=style,
                    min_seconds_between_alerts=0,
                    goal_signal_enabled=True,
                    goal_signal_move_cents=5,
                    goal_signal_cooldown_seconds=1,
                    goal_signal_up_team="法国",
                    goal_signal_down_team="摩洛哥",
                    # Deliberately unsafe legacy custom strings verify that
                    # balanced also restores the mandatory uncertainty.
                    goal_signal_up_speech="法国进球了！",
                    goal_signal_down_speech="摩洛哥进球了！",
                )
                snapshot = watcher.MarketSnapshot(
                    "FRA",
                    "法国晋级",
                    "active",
                    "E",
                    midpoint - 1,
                    midpoint + 1,
                    100 - midpoint - 1,
                    100 - midpoint + 1,
                    midpoint,
                    "",
                    None,
                )
                state = watcher.MarketState(last_observed_mid_cents=50)

                alerts = watcher.evaluate_market(
                    snapshot,
                    market,
                    state,
                    datetime.now(timezone.utc),
                )
                signal = next(alert for alert in alerts if alert.kind == "market_goal_signal")

                with self.subTest(style=style, rising=rising):
                    self.assertIn(expected_team, signal.speech)
                    self.assertIn(expected_team, signal.balloon)
                    self.assertTrue("疑似" in signal.speech or "可能" in signal.speech)
                    self.assertIn("确认", signal.speech)
                    self.assertNotIn("进球了", signal.speech)
                    self.assertIn("疑似", signal.balloon)
                    self.assertIn("等待确认", signal.balloon)
                    self.assertNotIn("仓位", signal.speech)

    def test_suspected_goal_marks_support_position_conflict_conditionally(self):
        snapshot = watcher.MarketSnapshot(
            "FRA", "法国晋级", "active", "E", 59, 61, 39, 41, 60, "", None
        )
        market = watcher.MarketConfig(
            "FRA",
            "法国晋级",
            commentary_style="balanced",
            goal_signal_up_team="法国",
            goal_signal_down_team="摩洛哥",
            favorite_team="摩洛哥",
            position_team="法国",
            tracks_position=True,
        )

        rising = watcher.speech_for_market_goal_signal(snapshot, market, True, 10, 60)
        falling = watcher.speech_for_market_goal_signal(snapshot, market, False, -10, 40)

        for speech in (rising, falling):
            self.assertIn("如果属实", speech)
            self.assertIn("仓位", speech)
            self.assertIn("确认", speech)
        self.assertIn("感情上不好受", rising)
        self.assertIn("仓位会受益", rising)
        self.assertIn("球迷这边开心", falling)
        self.assertIn("仓位却会承压", falling)

    def test_unsafe_english_custom_goal_claim_is_replaced_with_uncertainty(self):
        market = watcher.MarketConfig(
            "FRA",
            "France to advance",
            language="en",
            commentary_style="balanced",
            goal_signal_down_team="Morocco",
            goal_signal_down_speech=(
                "Possible market overreaction—Morocco scored; "
                "awaiting commentary confirmation."
            ),
        )
        snapshot = watcher.MarketSnapshot(
            "FRA", "France to advance", "active", "E", 39, 41, 59, 61, 40, "", None
        )

        speech = watcher.speech_for_market_goal_signal(snapshot, market, False, -10, 40)

        self.assertNotIn("Morocco scored", speech)
        self.assertIn("possible goal for Morocco", speech)
        self.assertIn("awaiting commentary confirmation", speech)

    def test_mixed_chinese_confirmation_and_uncertainty_is_replaced(self):
        market = watcher.MarketConfig(
            "FRA",
            "法国晋级",
            commentary_style="balanced",
            goal_signal_up_team="法国",
            goal_signal_up_speech="可能只是盘口误判，法国进球了，等待文字直播确认。",
        )
        snapshot = watcher.MarketSnapshot(
            "FRA", "法国晋级", "active", "E", 59, 61, 39, 41, 60, "", None
        )

        speech = watcher.speech_for_market_goal_signal(snapshot, market, True, 10, 60)

        self.assertNotIn("法国进球了", speech)
        self.assertIn("法国疑似进球", speech)
        self.assertIn("等待文字直播确认", speech)

    def test_inactive_market_freezes_last_trade_without_goal_signal(self):
        market = watcher.MarketConfig(
            "FRA",
            "法国晋级",
            goal_signal_enabled=True,
            goal_signal_move_cents=5,
            min_seconds_between_alerts=0,
        )
        snapshot = watcher.MarketSnapshot(
            "FRA", "法国晋级", "inactive", "E", 0, 100, 0, 100, 99, "", None
        )
        state = watcher.MarketState(
            last_observed_mid_cents=100,
            last_alert_mid_cents=100,
            last_status="inactive",
        )

        alerts = watcher.evaluate_market(
            snapshot,
            market,
            state,
            datetime.now(timezone.utc),
        )

        self.assertEqual(snapshot.implied_probability("yes"), 99)
        self.assertEqual(alerts, [])

    def test_finalized_market_does_not_emit_goal_or_price_signal(self):
        market = watcher.MarketConfig(
            "FRA",
            "法国晋级",
            goal_signal_enabled=True,
            goal_signal_move_cents=5,
            min_seconds_between_alerts=0,
        )
        snapshot = watcher.MarketSnapshot(
            "FRA", "法国晋级", "finalized", "E", 0, 100, 0, 100, 99, "", None,
            result="yes", settlement_value_cents=100,
        )
        state = watcher.MarketState(
            last_observed_mid_cents=98,
            last_alert_mid_cents=98,
            last_status="finalized",
        )

        alerts = watcher.evaluate_market(
            snapshot,
            market,
            state,
            datetime.now(timezone.utc),
        )

        self.assertEqual(alerts, [])

    def test_rapid_move_emits_goal_signal_even_during_price_alert_cooldown(self):
        market = watcher.MarketConfig(
            "FRA",
            "法国晋级",
            min_seconds_between_alerts=120,
            goal_signal_enabled=True,
            goal_signal_move_cents=5,
            goal_signal_cooldown_seconds=90,
            goal_signal_up_speech="法国可能进球，等待确认。",
        )
        snapshot = watcher.MarketSnapshot(
            "FRA", "法国晋级", "active", "E", 97, 99, 1, 3, 98, "", None
        )
        now = datetime.now(timezone.utc)
        state = watcher.MarketState(
            last_alert_mid_cents=92,
            last_observed_mid_cents=92,
            last_alert_at=now.timestamp(),
        )

        alerts = watcher.evaluate_market(snapshot, market, state, now)

        self.assertEqual([alert.kind for alert in alerts], ["market_goal_signal"])
        self.assertEqual(alerts[0].priority, 930)
        self.assertEqual(alerts[0].clip_id, "odds-up")
        self.assertTrue(alerts[0].prefer_dynamic_voice)
        self.assertIn("法国可能进球", alerts[0].speech)

    def test_large_favorite_move_uses_local_odds_clip(self):
        market = watcher.MarketConfig(
            "FRA",
            "法国晋级",
            alert_move_cents=3,
            speak_move_cents=5,
            min_seconds_between_alerts=0,
        )
        snapshot = watcher.MarketSnapshot(
            "FRA", "法国晋级", "active", "E", 75, 77, 23, 25, 76, "", None
        )
        state = watcher.MarketState(last_alert_mid_cents=70, last_observed_mid_cents=70)

        alerts = watcher.evaluate_market(snapshot, market, state, datetime.now(timezone.utc))

        price_alert = next(alert for alert in alerts if alert.kind == "price_move")
        self.assertEqual(price_alert.clip_id, "odds-up")


class AlertDeliveryTests(unittest.TestCase):
    def test_english_schedule_uses_natural_date_prepositions(self):
        starts_at = "2026-07-10T19:00:00+00:00"
        local_start = watcher.parse_datetime(starts_at).astimezone()
        month = (
            "Jan", "Feb", "Mar", "Apr", "May", "Jun",
            "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
        )[local_start.month - 1]

        alert = watcher.scheduled_setup_alert(
            {
                "event_id": "760511",
                "starts_at": starts_at,
                "label": "Spain vs Belgium",
            },
            "http://192.0.2.10:8788/setup",
            "en",
        )

        self.assertIn(
            f"kicking off on {month} {local_start.day} at {local_start:%H:%M}",
            alert.speech,
        )
        self.assertFalse(contains_han(alert.balloon))
        self.assertFalse(contains_han(alert.speech))

    def test_english_setup_confirmation_uses_complete_sentences(self):
        config = watcher.WatchConfig(
            language="en",
            espn=english_espn_config(),
        )

        alert = watcher.setup_confirmation_alert(config)

        self.assertEqual(
            alert.speech,
            "Now watching France vs Morocco. Supporting France. "
            "Position: France. Monitoring is active.",
        )

    def test_feedback_idle_wait_tracks_motion_tts_and_light(self):
        config = watcher.WatchConfig(stackchan_host="192.0.2.1")
        statuses = [
            {"celebrating": True, "tts": {"busy": False}, "light": {"on": True}},
            {"celebrating": False, "tts": {"busy": True}, "light": {"on": True}},
            {"celebrating": False, "tts": {"busy": False}, "light": {"on": True}},
            {"celebrating": False, "tts": {"busy": False}, "light": {"on": False}},
        ]

        with patch.object(watcher, "http_json", side_effect=statuses) as fetch_status:
            with patch.object(watcher.time, "sleep"):
                idle = watcher.wait_for_stackchan_feedback_idle(
                    config,
                    timeout=5,
                    include_light=True,
                )

        self.assertTrue(idle)
        self.assertEqual(fetch_status.call_count, 4)

    def test_pre_dispatch_idle_wait_does_not_block_on_persistent_light(self):
        config = watcher.WatchConfig(stackchan_host="192.0.2.1")
        status = {
            "celebrating": False,
            "tts": {"busy": False},
            "light": {"on": True},
        }

        with patch.object(watcher, "http_json", return_value=status):
            idle = watcher.wait_for_stackchan_feedback_idle(config, timeout=5)

        self.assertTrue(idle)

    def test_schedule_prompt_uses_setup_qr_and_dynamic_voice(self):
        config = watcher.WatchConfig(
            voice_transport="clip",
            setup_qr_commands=True,
        )
        alert = watcher.scheduled_setup_alert(
            {
                "event_id": "760511",
                "starts_at": "2026-07-10T19:00:00+00:00",
                "label": "西班牙 vs 比利时",
            },
            "http://192.0.2.10:8788/setup",
        )
        output = io.StringIO()

        with redirect_stdout(output):
            watcher.send_alert(config, alert, quiet=False, dry_run=True, no_say=False)

        commands = output.getvalue()
        self.assertIn(
            "dry-run stackchan: setup show http://192.0.2.10:8788/setup",
            commands,
        )
        self.assertIn("dry-run stackchan: say 下一场，西班牙 vs 比利时", commands)
        self.assertNotIn("balloon temp", commands)

    def test_old_mod_uses_balloon_url_for_schedule_prompt(self):
        config = watcher.WatchConfig(
            voice_transport="none",
            setup_qr_commands=False,
        )
        alert = watcher.scheduled_setup_alert(
            {
                "event_id": "760511",
                "starts_at": "2026-07-10T19:00:00+00:00",
                "label": "西班牙 vs 比利时",
            },
            "http://192.0.2.10:8788/setup",
        )
        output = io.StringIO()

        with redirect_stdout(output):
            watcher.send_alert(config, alert, quiet=False, dry_run=True, no_say=False)

        self.assertIn("dry-run stackchan: balloon temp", output.getvalue())
        self.assertNotIn("setup show", output.getvalue())

    def test_result_win_uses_atomic_voice_motion_and_light_command(self):
        config = watcher.WatchConfig(
            voice_transport="clip",
            result_celebration_commands=True,
        )
        alert = watcher.Alert(
            ticker="ESPN:760510",
            label="法国 vs 摩洛哥",
            kind="espn_status",
            priority=500,
            face="happy",
            balloon="比赛结束 | 法国 2 - 0 摩洛哥",
            speech="比赛结束。法国二比零摩洛哥。",
            detail="test",
            clip_id="favorite-win",
            light_rgb=(0, 85, 164),
            celebration="result-win",
        )
        output = io.StringIO()

        with redirect_stdout(output):
            watcher.send_alert(config, alert, quiet=False, dry_run=True, no_say=False)

        commands = output.getvalue()
        self.assertIn(
            "dry-run stackchan: celebrate result win 0 85 164 比赛结束。法国二比零摩洛哥。",
            commands,
        )
        self.assertNotIn("light flash", commands)
        self.assertNotIn("clip favorite-win", commands)

    def test_old_mod_falls_back_to_result_clip_and_light(self):
        config = watcher.WatchConfig(
            voice_transport="clip",
            result_celebration_commands=False,
        )
        alert = watcher.Alert(
            ticker="ESPN:760510",
            label="法国 vs 摩洛哥",
            kind="espn_status",
            priority=500,
            face="sad",
            balloon="比赛结束 | 法国 0 - 1 摩洛哥",
            speech="比赛结束。法国零比一摩洛哥。",
            detail="test",
            clip_id="favorite-lose",
            light_rgb=(0, 85, 164),
            celebration="result-lose",
        )
        output = io.StringIO()

        with redirect_stdout(output):
            watcher.send_alert(config, alert, quiet=False, dry_run=True, no_say=False)

        commands = output.getvalue()
        self.assertIn("dry-run stackchan: light flash 0 85 164", commands)
        self.assertIn("dry-run stackchan: clip favorite-lose", commands)
        self.assertNotIn("celebrate result", commands)

    def test_legacy_result_celebration_omits_unsupported_speech_argument(self):
        config = watcher.WatchConfig(
            voice_transport="clip",
            result_celebration_commands=True,
            result_speech_commands=False,
        )
        alert = watcher.Alert(
            ticker="ESPN:760510",
            label="法国 vs 摩洛哥",
            kind="espn_status",
            priority=500,
            face="happy",
            balloon="比赛结束 | 法国 2 - 0 摩洛哥",
            speech="比赛结束。法国二比零摩洛哥。",
            detail="test",
            clip_id="favorite-win",
            light_rgb=(0, 85, 164),
            celebration="result-win",
        )
        output = io.StringIO()

        with redirect_stdout(output):
            watcher.send_alert(config, alert, quiet=False, dry_run=True, no_say=False)

        self.assertIn(
            "dry-run stackchan: celebrate result win 0 85 164\n",
            output.getvalue(),
        )
        self.assertNotIn("比赛结束。法国二比零摩洛哥。", output.getvalue())

    def test_delivery_failure_is_reported_for_queue_retry(self):
        config = watcher.WatchConfig(voice_transport="none")
        alert = watcher.Alert(
            ticker="ESPN:760510",
            label="法国 vs 摩洛哥",
            kind="espn_shot_saved",
            priority=820,
            face="surprise",
            balloon="射正",
            speech=None,
            detail="test",
        )

        with patch.object(watcher, "send_stackchan_commands", side_effect=OSError("offline")):
            with redirect_stderr(io.StringIO()):
                delivered = watcher.send_alert(
                    config,
                    alert,
                    quiet=False,
                    dry_run=False,
                    no_say=False,
                )

        self.assertFalse(delivered)

    def test_highlight_without_local_clip_uses_dynamic_say(self):
        config = watcher.WatchConfig(
            voice_transport="clip",
            dynamic_voice_commands=True,
        )
        alert = watcher.Alert(
            ticker="ESPN:760510",
            label="法国 vs 摩洛哥",
            kind="espn_shot_saved",
            priority=820,
            face="surprise",
            balloon="于帕梅卡诺攻门，布努扑出",
            speech="于帕梅卡诺近距离头球，布努神扑救险！",
            detail="test",
            prefer_dynamic_voice=True,
        )
        output = io.StringIO()

        with redirect_stdout(output):
            watcher.send_alert(config, alert, quiet=False, dry_run=True, no_say=False)

        commands = output.getvalue()
        self.assertIn(
            "dry-run stackchan: say 于帕梅卡诺近距离头球，布努神扑救险！",
            commands,
        )
        self.assertNotIn("clip ", commands)

    def test_personalized_goal_uses_atomic_voice_motion_and_light_command(self):
        config = watcher.WatchConfig(voice_transport="clip")
        alert = watcher.Alert(
            ticker="ESPN:760510",
            label="法国 vs 摩洛哥",
            kind="espn_goal",
            priority=200,
            face="happy",
            balloon="进球测试",
            speech="姆巴佩！姆巴佩！打进去了！",
            detail="test",
            clip_id="favorite-goal",
            light_rgb=(0, 85, 164),
            celebration="goal",
            prefer_dynamic_voice=True,
        )
        output = io.StringIO()

        with redirect_stdout(output):
            watcher.send_alert(config, alert, quiet=False, dry_run=True, no_say=False)

        commands = output.getvalue()
        self.assertIn("dry-run stackchan: balloon temp 8000 进球测试", commands)
        self.assertIn(
            "dry-run stackchan: celebrate say 0 85 164 姆巴佩！姆巴佩！打进去了！",
            commands,
        )
        self.assertNotIn("dry-run stackchan: celebrate goal", commands)
        self.assertNotIn("dry-run stackchan: say ", commands)
        self.assertNotIn("light flash", commands)
        self.assertNotIn("clip favorite-goal", commands)

    def test_generic_goal_keeps_local_clip_celebration(self):
        config = watcher.WatchConfig(voice_transport="clip")
        alert = watcher.Alert(
            ticker="ESPN:760510",
            label="法国 vs 摩洛哥",
            kind="espn_goal",
            priority=200,
            face="happy",
            balloon="进球测试",
            speech="进球！",
            detail="test",
            clip_id="favorite-goal",
            light_rgb=(0, 85, 164),
            celebration="goal",
        )
        output = io.StringIO()

        with redirect_stdout(output):
            watcher.send_alert(config, alert, quiet=False, dry_run=True, no_say=False)

        self.assertIn("dry-run stackchan: celebrate goal 0 85 164", output.getvalue())

    def test_old_mod_uses_say_and_light_for_personalized_goal(self):
        config = watcher.WatchConfig(
            voice_transport="clip",
            dynamic_voice_commands=False,
        )
        alert = watcher.Alert(
            ticker="ESPN:760510",
            label="法国 vs 摩洛哥",
            kind="espn_goal",
            priority=1000,
            face="happy",
            balloon="姆巴佩进球",
            speech="姆巴佩！姆巴佩！打进去了！",
            detail="test",
            clip_id="favorite-goal",
            light_rgb=(0, 85, 164),
            celebration="goal",
            prefer_dynamic_voice=True,
        )
        output = io.StringIO()

        with redirect_stdout(output):
            watcher.send_alert(config, alert, quiet=False, dry_run=True, no_say=False)

        commands = output.getvalue()
        self.assertIn("dry-run stackchan: light flash 0 85 164", commands)
        self.assertIn("dry-run stackchan: say 姆巴佩！姆巴佩！打进去了！", commands)
        self.assertNotIn("celebrate ", commands)


class AdaptivePollingTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 7, 9, 18, tzinfo=timezone.utc)
        self.config = watcher.WatchConfig(
            poll_seconds=10,
            adaptive_polling=watcher.AdaptivePollingConfig(enabled=True),
            espn=watcher.ESPNConfig(enabled=True, poll_seconds=2),
        )

    def test_pregame_cadence_accelerates_toward_kickoff(self):
        cases = [
            (120, "far", 300, 300),
            (60, "warmup", 30, 60),
            (10, "full-speed", 10, 2),
        ]

        for minutes_until_start, tier, kalshi_seconds, espn_seconds in cases:
            with self.subTest(tier=tier):
                self.config.espn.starts_at = (
                    self.now + timedelta(minutes=minutes_until_start)
                ).isoformat()
                plan = watcher.adaptive_polling_plan(self.config, None, self.now)
                self.assertEqual(plan.tier, tier)
                self.assertEqual(plan.kalshi_seconds, kalshi_seconds)
                self.assertEqual(plan.espn_seconds, espn_seconds)

    def test_live_and_post_status_override_pregame_time(self):
        self.config.espn.starts_at = (self.now + timedelta(days=1)).isoformat()

        live = watcher.adaptive_polling_plan(
            self.config,
            match_snapshot(status="in"),
            self.now,
        )
        post = watcher.adaptive_polling_plan(
            self.config,
            match_snapshot(status="post"),
            self.now,
        )

        self.assertEqual((live.tier, live.kalshi_seconds, live.espn_seconds), ("live", 10, 2))
        self.assertEqual((post.tier, post.kalshi_seconds, post.espn_seconds), ("post", 60, 300))


class AlertQueueTests(unittest.TestCase):
    def test_confirmed_goal_drops_unplayed_market_goal_signal(self):
        signal = watcher.Alert(
            ticker="FRA",
            label="法国晋级",
            kind="market_goal_signal",
            priority=930,
            face="happy",
            balloon="疑似进球",
            speech="法国可能进球",
            detail="rapid move",
        )
        goal = watcher.Alert(
            ticker="ESPN:760510",
            label="法国 vs 摩洛哥",
            kind="espn_goal",
            priority=1000,
            face="happy",
            balloon="法国进球",
            speech="姆巴佩进球",
            detail="Goal! France.",
        )

        queue = watcher.merge_alert_queue(
            [],
            [(signal, None, None, None), (goal, None, None, None)],
            100.0,
        )

        self.assertEqual([item.alert.kind for item in queue], ["espn_goal"])

    def test_live_match_event_preempts_and_retains_market_alert(self):
        market = watcher.Alert(
            ticker="FRA",
            label="法国晋级",
            kind="price_move",
            priority=110,
            face="happy",
            balloon="盘口上涨",
            speech=None,
            detail="YES mid 70c -> 80c",
            clip_id="odds-up",
        )
        goal = watcher.Alert(
            ticker="ESPN:760510",
            label="法国 vs 摩洛哥",
            kind="espn_goal",
            priority=1000,
            face="happy",
            balloon="法国进球",
            speech=None,
            detail="Goal! France.",
            clip_id="favorite-goal",
        )

        queue = watcher.merge_alert_queue(
            [],
            [(market, None, None, None), (goal, None, None, None)],
            100.0,
        )

        self.assertEqual(queue[0].alert.kind, "espn_goal")
        deferred = watcher.merge_alert_queue(queue[1:], [], 110.0)
        self.assertEqual([item.alert.kind for item in deferred], ["price_move"])


class DailyPromptTests(unittest.TestCase):
    SETUP_URL = "http://192.0.2.9/setup"

    def _now_local(self):
        return datetime(2026, 7, 10, 10, 30).astimezone()

    def _match(self, hours_ahead: float, event_id: str = "760777", label: str = "西班牙 vs 比利时"):
        starts_at = (self._now_local() + timedelta(hours=hours_ahead)).astimezone(timezone.utc)
        return {"event_id": event_id, "label": label, "starts_at": starts_at.isoformat()}

    def test_unconfigured_match_today_prompts_setup(self):
        config = watcher.WatchConfig(espn=watcher.ESPNConfig(enabled=False))
        upcoming = [self._match(5), self._match(8, event_id="760778")]

        alert = watcher.choose_daily_prompt(upcoming, config, self.SETUP_URL, self._now_local())

        self.assertIsNotNone(alert)
        self.assertEqual(alert.kind, "daily_setup")
        self.assertEqual(alert.setup_url, self.SETUP_URL)
        self.assertIn("2场比赛", alert.speech)
        self.assertIn("扫码", alert.speech)
        self.assertIn("西班牙 vs 比利时", alert.speech)

    def test_configured_match_today_stays_quiet(self):
        espn = espn_config()
        espn.event_id = "760777"
        config = watcher.WatchConfig(espn=espn)
        upcoming = [self._match(5)]

        alert = watcher.choose_daily_prompt(upcoming, config, self.SETUP_URL, self._now_local())

        self.assertIsNone(alert)

    def test_matches_only_on_later_days_stay_quiet(self):
        config = watcher.WatchConfig(espn=watcher.ESPNConfig(enabled=False))
        upcoming = [self._match(30)]

        alert = watcher.choose_daily_prompt(upcoming, config, self.SETUP_URL, self._now_local())

        self.assertIsNone(alert)

    def test_no_fixtures_prompts_market_discovery_in_english(self):
        config = watcher.WatchConfig(
            language="en",
            espn=watcher.ESPNConfig(enabled=False),
            setup_server=watcher.SetupServerConfig(lookahead_days=7),
        )

        alert = watcher.choose_daily_prompt([], config, self.SETUP_URL, self._now_local())

        self.assertIsNotNone(alert)
        self.assertEqual(alert.kind, "daily_discover")
        self.assertEqual(alert.setup_url, self.SETUP_URL)
        self.assertIn("next 7 days", alert.speech)
        self.assertIn("Kalshi", alert.speech)
        self.assertFalse(contains_han(alert.speech))
        self.assertFalse(contains_han(alert.balloon))

    def test_daily_prompt_hour_parses_and_clamps(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(
                json.dumps(
                    {
                        "markets": [{"ticker": "KXTEST-1", "label": "test"}],
                        "setup_server": {"daily_prompt_hour": 99},
                    }
                ),
                encoding="utf-8",
            )
            config = watcher.load_config(path)
        self.assertEqual(config.setup_server.daily_prompt_hour, 23)


if __name__ == "__main__":
    unittest.main()
