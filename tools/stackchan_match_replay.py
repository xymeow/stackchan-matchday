#!/usr/bin/env python3
"""Replay selected ESPN match events through the real Stack-chan alert path.

The command is dry-run by default. Pass --execute only after the continuous
watcher has been stopped, otherwise both processes can write to the device.
"""

from __future__ import annotations

import argparse
import re
import sys
import time
import urllib.error
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterable

try:
    import stackchan_kalshi_watch as watcher
except ModuleNotFoundError:  # pragma: no cover - supports importlib-based tests.
    sys.path.insert(0, str(Path(__file__).parent))
    import stackchan_kalshi_watch as watcher


DEFAULT_EVENT_ID = "760510"
DEFAULT_TEAM = "France"
DEFAULT_INCLUDE = frozenset({"goals", "result"})


@dataclass(frozen=True)
class ReplayFrame:
    sequence: str
    clock: str
    snapshot: watcher.MatchSnapshot
    alert: watcher.Alert


def _aliases(team: watcher.MatchTeam) -> list[str]:
    return [value for value in (team.name, team.abbreviation) if value]


def score_from_commentary(
    text: str,
    snapshot: watcher.MatchSnapshot,
    current: tuple[int, int],
) -> tuple[int, int]:
    """Extract the score embedded in an ESPN commentary line, if present."""
    for home_name in _aliases(snapshot.home):
        for away_name in _aliases(snapshot.away):
            home_first = re.search(
                rf"{re.escape(home_name)}\s+(\d+)\s*,\s*{re.escape(away_name)}\s+(\d+)",
                text,
                re.IGNORECASE,
            )
            if home_first:
                return int(home_first.group(1)), int(home_first.group(2))
            away_first = re.search(
                rf"{re.escape(away_name)}\s+(\d+)\s*,\s*{re.escape(home_name)}\s+(\d+)",
                text,
                re.IGNORECASE,
            )
            if away_first:
                return int(away_first.group(2)), int(away_first.group(1))
    return current


def _sequence_sort_key(item: dict, index: int) -> tuple[int, int]:
    try:
        return int(item.get("sequence")), index
    except (TypeError, ValueError):
        return 1_000_000_000, index


def _selected(alert: watcher.Alert, include: set[str]) -> bool:
    if alert.kind == "espn_goal":
        return "goals" in include
    if watcher.is_final_status_alert(alert):
        return "result" in include
    if alert.kind in {"espn_penalty", "espn_penalty_awarded"}:
        return "penalties" in include
    if alert.kind in {"espn_red_card", "espn_yellow_card"}:
        return "cards" in include
    return "all" in include


def build_replay_frames(
    snapshot: watcher.MatchSnapshot,
    config: watcher.ESPNConfig,
    include: Iterable[str] = DEFAULT_INCLUDE,
    sequences: set[str] | None = None,
) -> list[ReplayFrame]:
    """Build chronological alerts with the score as it stood at each event."""
    selected_kinds = {value.strip().lower() for value in include if value.strip()}
    current_score = (0, 0)
    history: list[dict] = []
    frames: list[ReplayFrame] = []
    indexed = list(enumerate(snapshot.commentary))
    indexed.sort(key=lambda pair: _sequence_sort_key(pair[1], pair[0]))

    for _index, item in indexed:
        history.append(item)
        text = str(item.get("text") or (item.get("play") or {}).get("text") or "")
        current_score = score_from_commentary(text, snapshot, current_score)
        lower_text = text.lower()
        is_final = lower_text.startswith(("match ends", "penalty shootout ends"))
        frame_snapshot = replace(
            snapshot,
            status_state="post" if is_final else "in",
            status_name="STATUS_FINAL" if is_final else "STATUS_IN_PROGRESS",
            home=replace(snapshot.home, score=str(current_score[0])),
            away=replace(snapshot.away, score=str(current_score[1])),
            commentary=list(history),
        )
        alert = watcher.alert_for_espn_commentary(item, frame_snapshot, config)
        if alert is None:
            continue
        sequence = str(item.get("sequence") or "")
        if sequences is not None:
            if sequence not in sequences:
                continue
        elif not _selected(alert, selected_kinds):
            continue
        clock = str(
            (item.get("time") or {}).get("displayValue")
            or ((item.get("play") or {}).get("clock") or {}).get("displayValue")
            or ""
        )
        frames.append(ReplayFrame(sequence, clock, frame_snapshot, alert))
    return frames


def wait_for_device_idle(config: watcher.WatchConfig, timeout: float) -> dict:
    if config.stackchan_transport != "http":
        time.sleep(min(timeout, 8))
        return {}
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = watcher.http_json(f"http://{config.stackchan_host}/api/status")
        tts = status.get("tts") or {}
        light = status.get("light") or {}
        if not status.get("celebrating") and not tts.get("busy") and not light.get("on"):
            return status
        time.sleep(0.25)
    raise TimeoutError(f"Stack-chan celebration did not finish within {timeout:g}s")


def cleanup_device(config: watcher.WatchConfig) -> dict:
    watcher.send_stackchan_commands(
        config,
        ["pose 0 0 0 0.25", "mouth 0", "light off", "face neutral"],
    )
    # The pose command resolves when the servo target is written, not when the
    # 250 ms move finishes. Do not remove torque until the head has arrived.
    time.sleep(0.4)
    watcher.send_stackchan_commands(config, ["torque off"])
    return wait_for_device_idle(config, 10)


def verify_safe_status(status: dict) -> list[str]:
    if not status:
        return ["device status is unavailable; cleanup could not be verified"]
    problems: list[str] = []
    pose = status.get("pose") or {}
    if any(abs(float(pose.get(axis) or 0)) > 0.1 for axis in ("yaw", "pitch", "roll")):
        problems.append(f"pose is not centered: {pose}")
    if status.get("torque"):
        problems.append("torque is still on")
    if "light" not in status:
        problems.append("light state is missing from device status")
    elif (status.get("light") or {}).get("on"):
        problems.append("head light is still on")
    if status.get("celebrating"):
        problems.append("celebration is still running")
    if (status.get("tts") or {}).get("busy"):
        problems.append("TTS is still busy")
    if status.get("lastError"):
        problems.append(f"device lastError: {status['lastError']}")
    return problems


def parse_include(value: str) -> set[str]:
    include = {item.strip().lower() for item in value.split(",") if item.strip()}
    allowed = {"goals", "result", "penalties", "cards", "all"}
    unknown = include - allowed
    if unknown:
        raise argparse.ArgumentTypeError(f"unknown replay groups: {', '.join(sorted(unknown))}")
    return include


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Replay historical ESPN events on Stack-chan")
    parser.add_argument("--config", default="config/kalshi_watchlist.json")
    parser.add_argument("--language", default="", help="Override config language (zh or en)")
    parser.add_argument("--event-id", default=DEFAULT_EVENT_ID)
    parser.add_argument("--label", default="", help="Override the localized match label")
    parser.add_argument("--favorite-team", default=DEFAULT_TEAM)
    parser.add_argument("--position-team", default=DEFAULT_TEAM)
    parser.add_argument("--include", type=parse_include, default=set(DEFAULT_INCLUDE))
    parser.add_argument("--sequences", default="", help="Comma-separated ESPN sequence ids")
    parser.add_argument("--interval", type=float, default=1.5, help="Extra pause after each event")
    parser.add_argument("--timeout", type=float, default=25, help="Per-event device timeout")
    parser.add_argument("--execute", action="store_true", help="Send commands; default is dry-run")
    parser.add_argument("--no-say", action="store_true")
    parser.add_argument("--no-cleanup", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config: watcher.WatchConfig | None = None
    cleanup_required = False
    exit_code = 0
    try:
        config = watcher.load_config(Path(args.config), args.language or None)
        configured_event_id = config.espn.event_id
        configured_label = config.espn.label
        config.espn.enabled = True
        config.espn.event_id = args.event_id
        config.espn.label = args.label or (
            configured_label if configured_event_id == args.event_id else ""
        )
        config.espn.favorite_team = args.favorite_team
        config.espn.position_team = args.position_team
        snapshot = watcher.fetch_espn_match(config.espn)
        config.espn.label = config.espn.label or watcher.match_label(snapshot, config.espn)
        sequences = {value.strip() for value in args.sequences.split(",") if value.strip()} or None
        frames = build_replay_frames(snapshot, config.espn, args.include, sequences)
        if not frames:
            raise RuntimeError("no matching ESPN alerts found")

        mode = "EXECUTE" if args.execute else "DRY RUN"
        print(f"{mode}: ESPN {snapshot.event_id} {config.espn.label}; {len(frames)} event(s)")
        if args.execute and config.stackchan_transport == "http":
            dynamic, mod_version, mod_name = watcher.detect_dynamic_voice_commands(config)
            config.dynamic_voice_commands = dynamic
            config.result_celebration_commands = watcher._version_tuple(mod_version) >= (0, 11, 0)
            config.result_speech_commands = (
                mod_name == "stackchan_matchday"
                and watcher._version_tuple(mod_version) >= (1, 1, 0)
            )
            if not config.result_speech_commands:
                print(f"warning: {mod_name or 'unknown MOD'} {mod_version} lacks synchronized result speech")
        elif args.execute:
            config.dynamic_voice_commands = False
            config.result_celebration_commands = False
            config.result_speech_commands = False
            print("warning: serial transport cannot verify MOD capabilities; using legacy feedback")
        cleanup_required = args.execute and not args.no_cleanup
        for index, frame in enumerate(frames, start=1):
            score = f"{frame.snapshot.home.score}-{frame.snapshot.away.score}"
            print(
                f"[{index}/{len(frames)}] seq={frame.sequence} {frame.clock} "
                f"{frame.alert.kind} score={score}: {frame.alert.detail}"
            )
            delivered = watcher.send_alert(
                config,
                frame.alert,
                quiet=False,
                dry_run=not args.execute,
                no_say=args.no_say,
            )
            if not delivered:
                raise RuntimeError(f"delivery failed for ESPN sequence {frame.sequence}")
            if args.execute:
                event_status = wait_for_device_idle(config, args.timeout)
                if event_status.get("lastError"):
                    raise RuntimeError(
                        f"ESPN sequence {frame.sequence} feedback failed: "
                        f"{event_status['lastError']}"
                    )
                if args.interval > 0 and index < len(frames):
                    time.sleep(args.interval)

    except KeyboardInterrupt:
        print("error: replay interrupted", file=sys.stderr)
        exit_code = 130
    except (
        watcher.ConfigError,
        OSError,
        RuntimeError,
        TimeoutError,
        ValueError,
        urllib.error.URLError,
    ) as error:
        print(f"error: {error}", file=sys.stderr)
        exit_code = 1
    finally:
        if cleanup_required and config is not None:
            try:
                status = cleanup_device(config)
                problems = verify_safe_status(status)
                if problems:
                    for problem in problems:
                        print(f"error: {problem}", file=sys.stderr)
                    exit_code = 1 if exit_code == 0 else exit_code
                else:
                    print(
                        f"safe idle: version={status.get('version')} pose={status.get('pose')} "
                        f"torque={status.get('torque')} light={status.get('light')}"
                    )
            except (OSError, RuntimeError, TimeoutError, ValueError, urllib.error.URLError) as error:
                print(f"error: cleanup failed: {error}", file=sys.stderr)
                exit_code = 1 if exit_code == 0 else exit_code
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
