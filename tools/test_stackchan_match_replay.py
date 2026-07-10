from __future__ import annotations

import importlib.util
import io
import re
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch


MODULE_PATH = Path(__file__).with_name("stackchan_match_replay.py")
SPEC = importlib.util.spec_from_file_location("stackchan_match_replay", MODULE_PATH)
assert SPEC and SPEC.loader
replay = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = replay
SPEC.loader.exec_module(replay)
watcher = replay.watcher


def historical_snapshot() -> watcher.MatchSnapshot:
    mbappe = watcher.MatchPlayer("1", "Kylian Mbappé", "K. Mbappé", "10", "France", "FRA")
    dembele = watcher.MatchPlayer("2", "Ousmane Dembélé", "O. Dembélé", "11", "France", "FRA")
    commentary = [
        {
            "sequence": 54,
            "time": {"displayValue": "60'"},
            "text": "Goal! France 1, Morocco 0. Kylian Mbappé scores.",
            "play": {
                "type": {"type": "goal"},
                "team": {"displayName": "France"},
                "participants": [{"athlete": {"id": "1", "displayName": "Kylian Mbappé"}}],
            },
        },
        {
            "sequence": 59,
            "time": {"displayValue": "63'"},
            "text": "Issa Diop (Morocco) is shown the yellow card.",
            "play": {"type": {"type": "yellow-card"}, "team": {"displayName": "Morocco"}},
        },
        {
            "sequence": 60,
            "time": {"displayValue": "66'"},
            "text": "Goal! France 2, Morocco 0. Ousmane Dembélé scores.",
            "play": {
                "type": {"type": "goal"},
                "team": {"displayName": "France"},
                "participants": [{"athlete": {"id": "2", "displayName": "Ousmane Dembélé"}}],
            },
        },
        {
            "sequence": 102,
            "text": "Match ends, France 2, Morocco 0.",
            "play": {"type": {"type": "full-time"}},
        },
    ]
    players = {}
    for player in (mbappe, dembele):
        for key in watcher._player_lookup_keys(player.athlete_id, player.name, player.short_name):
            players[key] = player
    return watcher.MatchSnapshot(
        event_id="760510",
        status_state="post",
        status_name="STATUS_FINAL",
        status_detail="FT",
        home=watcher.MatchTeam("France", "FRA", "home", "2"),
        away=watcher.MatchTeam("Morocco", "MAR", "away", "0"),
        commentary=commentary,
        players=players,
    )


def historical_config() -> watcher.ESPNConfig:
    return watcher.ESPNConfig(
        enabled=True,
        event_id="760510",
        label="法国 vs 摩洛哥",
        favorite_team="France",
        position_team="France",
        team_names={"France": "法国", "FRA": "法国", "Morocco": "摩洛哥", "MAR": "摩洛哥"},
        team_colors={"France": "#0055A4", "FRA": "#0055A4"},
        player_names={"Kylian Mbappé": "姆巴佩", "Ousmane Dembélé": "登贝莱"},
        star_chants={"Kylian Mbappé": "{name}！{name}！打进去了！"},
    )


def historical_english_config() -> watcher.ESPNConfig:
    return watcher.ESPNConfig(
        language="en",
        enabled=True,
        event_id="760510",
        label="France vs Morocco",
        favorite_team="France",
        position_team="France",
        team_names={"France": "France", "FRA": "France", "Morocco": "Morocco", "MAR": "Morocco"},
        team_colors={"France": "#0055A4", "FRA": "#0055A4"},
        player_names={
            "Kylian Mbappé": "Kylian Mbappé",
            "Ousmane Dembélé": "Ousmane Dembélé",
        },
        star_chants={
            "Kylian Mbappé": "{name}! {name}! He's scored! France's number {number} delivers!",
        },
    )


class ReplayTests(unittest.TestCase):
    def test_safe_status_requires_reported_light_state(self):
        status = {
            "pose": {"yaw": 0, "pitch": 0, "roll": 0},
            "torque": False,
            "celebrating": False,
            "tts": {"busy": False},
            "lastError": "",
        }

        self.assertIn(
            "light state is missing from device status",
            replay.verify_safe_status(status),
        )

    def test_score_reconstruction_tracks_each_historical_goal(self):
        snapshot = historical_snapshot()

        self.assertEqual(
            replay.score_from_commentary(snapshot.commentary[0]["text"], snapshot, (0, 0)),
            (1, 0),
        )
        self.assertEqual(
            replay.score_from_commentary(snapshot.commentary[2]["text"], snapshot, (1, 0)),
            (2, 0),
        )

    def test_default_replay_selects_goals_and_result_in_order(self):
        frames = replay.build_replay_frames(historical_snapshot(), historical_config())

        self.assertEqual([frame.sequence for frame in frames], ["54", "60", "102"])
        self.assertEqual(
            [(frame.snapshot.home.score, frame.snapshot.away.score) for frame in frames],
            [("1", "0"), ("2", "0"), ("2", "0")],
        )
        self.assertEqual([frame.alert.celebration for frame in frames], ["goal", "goal", "result-win"])
        self.assertIn("现在法国1比0摩洛哥", frames[0].alert.speech)

    def test_english_replay_goal_and_final_are_fully_localized(self):
        frames = replay.build_replay_frames(
            historical_snapshot(),
            historical_english_config(),
        )
        goal = frames[0].alert
        result = frames[-1].alert

        self.assertEqual(goal.kind, "espn_goal")
        self.assertEqual(
            goal.balloon,
            "60' Kylian Mbappé scores! | France 1-0 Morocco",
        )
        self.assertEqual(
            goal.speech,
            "Kylian Mbappé! Kylian Mbappé! He's scored! France's number 10 delivers! "
            "It is France 1, Morocco 0.",
        )
        self.assertFalse(goal.is_final)

        self.assertEqual(result.kind, "espn_status")
        self.assertEqual(result.balloon, "Full time | France 2-0 Morocco")
        self.assertEqual(result.speech, "Full time. It is France 2, Morocco 0.")
        self.assertTrue(result.is_final)
        self.assertTrue(watcher.is_final_status_alert(result))

        han = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
        for text in (goal.balloon, goal.speech, result.balloon, result.speech):
            with self.subTest(text=text):
                self.assertIsNone(han.search(text))

    def test_explicit_sequences_can_include_a_card(self):
        frames = replay.build_replay_frames(
            historical_snapshot(),
            historical_config(),
            sequences={"59"},
        )

        self.assertEqual([frame.sequence for frame in frames], ["59"])
        self.assertEqual(frames[0].alert.kind, "espn_yellow_card")

    def test_cleanup_waits_for_centering_before_torque_off(self):
        config = watcher.WatchConfig()
        status = {
            "pose": {"yaw": 0, "pitch": 0, "roll": 0},
            "torque": False,
            "light": {"on": False},
            "celebrating": False,
            "tts": {"busy": False},
            "lastError": "",
        }

        with patch.object(watcher, "send_stackchan_commands") as send:
            with patch.object(replay.time, "sleep") as sleep:
                with patch.object(replay, "wait_for_device_idle", return_value=status):
                    replay.cleanup_device(config)

        self.assertEqual(send.call_count, 2)
        self.assertEqual(send.call_args_list[0].args[1][0], "pose 0 0 0 0.25")
        self.assertEqual(send.call_args_list[1].args[1], ["torque off"])
        sleep.assert_called_once_with(0.4)

    def test_execute_failure_still_runs_cleanup(self):
        config = watcher.WatchConfig(espn=historical_config())
        status = {
            "version": "1.1.0",
            "pose": {"yaw": 0, "pitch": 0, "roll": 0},
            "torque": False,
            "light": {"on": False},
            "celebrating": False,
            "tts": {"busy": False},
            "lastError": "",
        }

        with patch.object(watcher, "load_config", return_value=config):
            with patch.object(watcher, "fetch_espn_match", return_value=historical_snapshot()):
                with patch.object(
                    watcher,
                    "detect_dynamic_voice_commands",
                    return_value=(True, "1.1.0", "stackchan_matchday"),
                ):
                    with patch.object(watcher, "send_alert", return_value=False):
                        with patch.object(replay, "cleanup_device", return_value=status) as cleanup:
                            with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
                                result = replay.main(["--config", "ignored.json", "--execute"])

        self.assertEqual(result, 1)
        cleanup.assert_called_once_with(config)

    def test_event_error_is_captured_before_cleanup_clears_it(self):
        config = watcher.WatchConfig(espn=historical_config())
        event_status = {
            "pose": {"yaw": 0, "pitch": 0, "roll": 0},
            "torque": False,
            "light": {"on": False},
            "celebrating": False,
            "tts": {"busy": False},
            "lastError": "celebrate goal: servo timeout",
        }
        clean_status = {**event_status, "version": "1.1.0", "lastError": ""}
        stderr = io.StringIO()

        with patch.object(watcher, "load_config", return_value=config):
            with patch.object(watcher, "fetch_espn_match", return_value=historical_snapshot()):
                with patch.object(
                    watcher,
                    "detect_dynamic_voice_commands",
                    return_value=(True, "1.1.0", "stackchan_matchday"),
                ):
                    with patch.object(watcher, "send_alert", return_value=True):
                        with patch.object(replay, "wait_for_device_idle", return_value=event_status):
                            with patch.object(replay, "cleanup_device", return_value=clean_status) as cleanup:
                                with redirect_stderr(stderr), redirect_stdout(io.StringIO()):
                                    result = replay.main(
                                        ["--config", "ignored.json", "--execute", "--interval", "0"]
                                    )

        self.assertEqual(result, 1)
        self.assertIn("sequence 54 feedback failed", stderr.getvalue())
        cleanup.assert_called_once_with(config)


if __name__ == "__main__":
    unittest.main()
