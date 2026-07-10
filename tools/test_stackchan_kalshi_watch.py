from __future__ import annotations

import importlib.util
import io
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
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
        self.assertIn("法国 1-0 摩洛哥", alerts[0].balloon)
        self.assertIn("姆巴佩！姆巴佩！打进去了！", alerts[0].speech)

    def test_non_star_goal_uses_jersey_number_and_name(self):
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
        self.assertIn("24号球员Jean Prospect", alert.balloon)
        self.assertIn("24号球员Jean Prospect！球进啦！", alert.speech)

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
        self.assertIn("布努神扑", alert.speech)
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
        self.assertIn("摩洛哥获得右路任意球", alerts[0].speech)
        self.assertIn("8号球员Azzedine Ounahi", alerts[0].speech)

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
        self.assertIn("姆巴佩在禁区里制造犯规", alert.speech)

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
        self.assertIn("15号球员Jean-Philippe Mateta上场", alert.speech)
        self.assertIn("姆巴佩被换下", alert.speech)
        self.assertIn("姆巴佩可能有伤", alert.speech)


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
            "http://192.168.0.117:8788/setup",
        )
        output = io.StringIO()

        with redirect_stdout(output):
            watcher.send_alert(config, alert, quiet=False, dry_run=True, no_say=False)

        commands = output.getvalue()
        self.assertIn(
            "dry-run stackchan: setup show http://192.168.0.117:8788/setup",
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
            "http://192.168.0.117:8788/setup",
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
        self.assertIn("dry-run stackchan: celebrate result win 0 85 164", commands)
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

    def test_personalized_goal_sequences_local_celebration_then_dynamic_speech(self):
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
        self.assertIn("dry-run stackchan: celebrate goal 0 85 164", commands)
        self.assertIn("dry-run stackchan: say 姆巴佩！姆巴佩！打进去了！", commands)
        self.assertNotIn("celebrate say", commands)
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


if __name__ == "__main__":
    unittest.main()
