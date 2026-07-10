#!/usr/bin/env python3
"""Watch Kalshi YES/NO markets and ESPN match events via Stack-chan.

REST market data is public, so the MVP intentionally does not use Kalshi API
keys. WebSocket support is left as a later extension where keys stay on this
Mac/server and never move into firmware.
"""

from __future__ import annotations

import argparse
import json
import math
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, time as dt_time, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Optional

try:
    import serial  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - depends on the Python environment.
    serial = None

try:
    from stackchan_i18n import (
        LocalizationError,
        join_sentences,
        normalize_language,
        pick,
        resolve_text,
        resolve_text_map,
    )
    from stackchan_match_setup import MatchSetupService, start_setup_server
except ModuleNotFoundError:  # pragma: no cover - supports importlib-based tests.
    sys.path.insert(0, str(Path(__file__).parent))
    from stackchan_i18n import (
        LocalizationError,
        join_sentences,
        normalize_language,
        pick,
        resolve_text,
        resolve_text_map,
    )
    from stackchan_match_setup import MatchSetupService, start_setup_server


DEFAULT_BASE_URL = "https://external-api.kalshi.com/trade-api/v2"
DEFAULT_ESPN_BASE_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer"
DEFAULT_STACKCHAN_HOST = "stackchan.local"
DEFAULT_STACKCHAN_TRANSPORT = "http"
DEFAULT_STACKCHAN_SERIAL_PORT = "/dev/cu.usbmodem101"
DEFAULT_STACKCHAN_SERIAL_BAUD = 115200
DEFAULT_VOICE_TRANSPORT = "clip"
DEFAULT_MAC_SAY_RATE = 185
DEFAULT_POLL_SECONDS = 10
DEFAULT_ALERT_MOVE_CENTS = 3
DEFAULT_SPEAK_MOVE_CENTS = 5
DEFAULT_SPREAD_MOVE_CENTS = 4
DEFAULT_MIN_ALERT_SECONDS = 120
DEFAULT_NEAR_CLOSE_MINUTES = 30
DEFAULT_MAX_ALERTS_PER_CYCLE = 2
DEFAULT_ALERT_BALLOON_SECONDS = 8
DEFAULT_ESPN_POLL_SECONDS = 10
DEFAULT_STARTUP_CRITICAL_REPLAY_SECONDS = 180
DEFAULT_DISPLAY_REFRESH_SECONDS = 30
DEFAULT_SETUP_PORT = 8788
REQUEST_TIMEOUT_SECONDS = 12
STACKCHAN_COMMAND_TIMEOUT_SECONDS = 20
MATCH_SETUP_PENDING_POLL_SECONDS = 2.5
MAX_QUEUED_ALERTS = 24
CLOSED_STATUSES = {"closed", "inactive", "settled", "finalized", "determined"}
STACKCHAN_DEVICE_HTTP_LOCK = threading.Lock()


@dataclass
class QuietHours:
    enabled: bool = False
    start: str = "23:30"
    end: str = "08:00"


@dataclass
class MarketConfig:
    ticker: str
    label: str
    side_i_care: str = "yes"
    alert_move_cents: int = DEFAULT_ALERT_MOVE_CENTS
    speak_move_cents: int = DEFAULT_SPEAK_MOVE_CENTS
    min_seconds_between_alerts: int = DEFAULT_MIN_ALERT_SECONDS
    near_close_minutes: int = DEFAULT_NEAR_CLOSE_MINUTES
    spread_move_cents: int = DEFAULT_SPREAD_MOVE_CENTS
    alerts_enabled: bool = True
    show_in_ticker: bool = True
    goal_signal_enabled: bool = False
    goal_signal_move_cents: int = 5
    goal_signal_cooldown_seconds: int = 90
    goal_signal_up_speech: str = ""
    goal_signal_down_speech: str = ""
    language: str = "zh"


@dataclass
class ProbabilityBarConfig:
    enabled: bool = False
    mode: str = "binary_complement"
    market_ticker: str = ""
    right_market_ticker: str = ""
    side: str = "yes"
    left_flag: str = "fr"
    left_color: str = "#0055A4"
    right_flag: str = "ma"
    right_color: str = "#C1272D"


@dataclass
class SetupServerConfig:
    enabled: bool = False
    host: str = "0.0.0.0"
    port: int = DEFAULT_SETUP_PORT
    public_base_url: str = ""
    kalshi_series_ticker: str = "KXWCADVANCE"
    lookahead_days: int = 10
    refresh_seconds: int = 900
    prompt_minutes_before: int = 90
    # Local hour (0-23) after which the watcher proactively asks once per day:
    # "matches today, scan to set one up" or, with no fixtures in the lookahead
    # window, "want to watch some other Kalshi market?". -1 disables.
    daily_prompt_hour: int = 10


@dataclass
class AdaptivePollingConfig:
    enabled: bool = False
    warmup_minutes_before: int = 90
    full_speed_minutes_before: int = 15
    far_kalshi_seconds: int = 300
    far_espn_seconds: int = 300
    warmup_kalshi_seconds: int = 30
    warmup_espn_seconds: int = 60
    post_kalshi_seconds: int = 60
    post_espn_seconds: int = 300


@dataclass
class ESPNConfig:
    enabled: bool = False
    event_id: str = ""
    league: str = "fifa.world"
    label: str = ""
    starts_at: str = ""
    base_url: str = DEFAULT_ESPN_BASE_URL
    poll_seconds: int = DEFAULT_ESPN_POLL_SECONDS
    favorite_team: str = ""
    position_team: str = ""
    favorite_goal_celebration: bool = True
    startup_replay_critical_seconds: int = DEFAULT_STARTUP_CRITICAL_REPLAY_SECONDS
    announce_status: bool = True
    announce_fouls: bool = True
    announce_opponent_free_kicks: bool = True
    announce_yellow_cards: bool = True
    announce_corners: bool = True
    announce_player_names: bool = True
    announce_shots_on_target: bool = True
    announce_close_misses: bool = True
    announce_dangerous_blocks: bool = True
    announce_substitutions: bool = True
    team_names: dict[str, str] = field(default_factory=dict)
    team_colors: dict[str, str] = field(default_factory=dict)
    player_names: dict[str, str] = field(default_factory=dict)
    star_chants: dict[str, str] = field(default_factory=dict)
    language: str = "zh"


@dataclass
class WatchConfig:
    stackchan_host: str = DEFAULT_STACKCHAN_HOST
    stackchan_transport: str = DEFAULT_STACKCHAN_TRANSPORT
    stackchan_serial_port: str = DEFAULT_STACKCHAN_SERIAL_PORT
    stackchan_serial_baud: int = DEFAULT_STACKCHAN_SERIAL_BAUD
    voice_transport: str = DEFAULT_VOICE_TRANSPORT
    mac_voice: str = ""
    mac_say_rate: int = DEFAULT_MAC_SAY_RATE
    kalshi_base_url: str = DEFAULT_BASE_URL
    poll_seconds: int = DEFAULT_POLL_SECONDS
    max_alerts_per_cycle: int = DEFAULT_MAX_ALERTS_PER_CYCLE
    startup_summary_on_watch: bool = True
    speak_startup_summary: bool = False
    ticker_enabled: bool = True
    display_refresh_seconds: int = DEFAULT_DISPLAY_REFRESH_SECONDS
    alert_balloon_seconds: int = DEFAULT_ALERT_BALLOON_SECONDS
    quiet_hours: QuietHours = field(default_factory=QuietHours)
    probability_bar: ProbabilityBarConfig = field(default_factory=ProbabilityBarConfig)
    setup_server: SetupServerConfig = field(default_factory=SetupServerConfig)
    adaptive_polling: AdaptivePollingConfig = field(default_factory=AdaptivePollingConfig)
    espn: ESPNConfig = field(default_factory=ESPNConfig)
    markets: list[MarketConfig] = field(default_factory=list)
    dynamic_voice_commands: bool = True
    result_celebration_commands: bool = True
    result_speech_commands: bool = True
    setup_qr_commands: bool = True
    language: str = "zh"


@dataclass
class MarketSnapshot:
    ticker: str
    label: str
    status: str
    event_ticker: str
    yes_bid_cents: int | None
    yes_ask_cents: int | None
    no_bid_cents: int | None
    no_ask_cents: int | None
    last_price_cents: int | None
    volume_24h: str
    close_time: datetime | None
    result: str = ""
    settlement_value_cents: int | None = None

    def bid(self, side: str) -> int | None:
        return self.yes_bid_cents if side == "yes" else self.no_bid_cents

    def ask(self, side: str) -> int | None:
        return self.yes_ask_cents if side == "yes" else self.no_ask_cents

    def mid(self, side: str) -> int | None:
        bid = self.bid(side)
        ask = self.ask(side)
        if bid is not None and ask is not None:
            return int(round((bid + ask) / 2))
        if side == "yes":
            return self.last_price_cents
        return None

    def yes_spread(self) -> int | None:
        if self.yes_bid_cents is None or self.yes_ask_cents is None:
            return None
        return max(0, self.yes_ask_cents - self.yes_bid_cents)

    def implied_probability(self, side: str) -> int | None:
        if self.status.lower() in CLOSED_STATUSES:
            yes_probability = self.settlement_value_cents
            if yes_probability is None:
                if self.result.lower() == "yes":
                    yes_probability = 100
                elif self.result.lower() == "no":
                    yes_probability = 0
                elif self.last_price_cents is not None:
                    yes_probability = self.last_price_cents
            if yes_probability is not None:
                return yes_probability if side == "yes" else 100 - yes_probability
        return self.mid(side)


@dataclass
class MarketState:
    last_alert_mid_cents: int | None = None
    last_observed_mid_cents: int | None = None
    last_yes_spread_cents: int | None = None
    last_status: str | None = None
    last_alert_at: float = 0
    last_goal_signal_at: float = 0
    near_close_alerted: bool = False


@dataclass
class Alert:
    ticker: str
    label: str
    kind: str
    priority: int
    face: str
    balloon: str
    speech: str | None
    detail: str
    clip_id: str | None = None
    light_rgb: tuple[int, int, int] | None = None
    celebration: str | None = None
    prefer_dynamic_voice: bool = False
    source_event_at: datetime | None = None
    setup_url: str | None = None
    is_final: bool = False


@dataclass
class MatchTeam:
    name: str
    abbreviation: str
    home_away: str
    score: str
    shootout_score: str = ""


@dataclass
class MatchPlayer:
    athlete_id: str
    name: str
    short_name: str
    jersey: str
    team_name: str
    team_abbreviation: str
    position: str = ""


@dataclass
class MatchSnapshot:
    event_id: str
    status_state: str
    status_name: str
    status_detail: str
    home: MatchTeam
    away: MatchTeam
    commentary: list[dict[str, Any]]
    starts_at: datetime | None = None
    players: dict[str, MatchPlayer] = field(default_factory=dict)


@dataclass
class ESPNState:
    initialized: bool = False
    seen_commentary: set[str] = field(default_factory=set)
    last_status_state: str = ""
    last_polled_at: float = 0
    final_result_announced: bool = False


PendingAlertContext = tuple[
    Alert,
    Optional[MarketSnapshot],
    Optional[MarketConfig],
    Optional[MarketState],
]


@dataclass
class QueuedAlert:
    alert: Alert
    snapshot: MarketSnapshot | None
    market: MarketConfig | None
    state: MarketState | None
    queued_at: float


class ConfigError(ValueError):
    pass


def _config_text(value: Any, language: str, *, path: str, fallback: str = "") -> str:
    try:
        return resolve_text(value, language, path=path, fallback=fallback)
    except LocalizationError as error:
        raise ConfigError(str(error)) from error


def _config_text_map(value: Any, language: str, *, path: str) -> dict[str, str]:
    try:
        return resolve_text_map(value, language, path=path)
    except LocalizationError as error:
        raise ConfigError(str(error)) from error


def load_config(path: Path, language_override: str | None = None) -> WatchConfig:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ConfigError(f"config not found: {path}") from error
    except json.JSONDecodeError as error:
        raise ConfigError(f"invalid JSON config {path}: {error}") from error

    try:
        language = normalize_language(
            language_override if language_override else raw.get("language", "zh"),
            path="language",
        )
    except LocalizationError as error:
        raise ConfigError(str(error)) from error

    quiet_raw = raw.get("quiet_hours") or {}
    quiet = QuietHours(
        enabled=bool(quiet_raw.get("enabled", False)),
        start=str(quiet_raw.get("start", "23:30")),
        end=str(quiet_raw.get("end", "08:00")),
    )

    bar_raw = raw.get("probability_bar") or {}
    if not isinstance(bar_raw, dict):
        raise ConfigError("probability_bar must be an object")
    probability_bar = ProbabilityBarConfig(
        enabled=bool(bar_raw.get("enabled", False)),
        mode=str(bar_raw.get("mode", "binary_complement")).strip().lower(),
        market_ticker=str(bar_raw.get("market_ticker", "")).strip().upper(),
        right_market_ticker=str(bar_raw.get("right_market_ticker", "")).strip().upper(),
        side=str(bar_raw.get("side", "yes")).strip().lower(),
        left_flag=str(bar_raw.get("left_flag", "fr")).strip().lower(),
        left_color=str(bar_raw.get("left_color", "#0055A4")).strip(),
        right_flag=str(bar_raw.get("right_flag", "ma")).strip().lower(),
        right_color=str(bar_raw.get("right_color", "#C1272D")).strip(),
    )

    setup_raw = raw.get("setup_server") or {}
    if not isinstance(setup_raw, dict):
        raise ConfigError("setup_server must be an object")
    setup_server = SetupServerConfig(
        enabled=bool(setup_raw.get("enabled", False)),
        host=str(setup_raw.get("host", "0.0.0.0")).strip(),
        port=max(1, min(65535, int(setup_raw.get("port", DEFAULT_SETUP_PORT)))),
        public_base_url=str(setup_raw.get("public_base_url", "")).strip().rstrip("/"),
        kalshi_series_ticker=str(
            setup_raw.get("kalshi_series_ticker", "KXWCADVANCE")
        ).strip().upper(),
        lookahead_days=max(1, min(30, int(setup_raw.get("lookahead_days", 10)))),
        refresh_seconds=max(60, int(setup_raw.get("refresh_seconds", 900))),
        prompt_minutes_before=max(5, int(setup_raw.get("prompt_minutes_before", 90))),
        daily_prompt_hour=max(-1, min(23, int(setup_raw.get("daily_prompt_hour", 10)))),
    )

    adaptive_raw = raw.get("adaptive_polling") or {}
    if not isinstance(adaptive_raw, dict):
        raise ConfigError("adaptive_polling must be an object")
    adaptive_polling = AdaptivePollingConfig(
        enabled=bool(adaptive_raw.get("enabled", False)),
        warmup_minutes_before=max(
            1, int(adaptive_raw.get("warmup_minutes_before", 90))
        ),
        full_speed_minutes_before=max(
            0, int(adaptive_raw.get("full_speed_minutes_before", 15))
        ),
        far_kalshi_seconds=max(1, int(adaptive_raw.get("far_kalshi_seconds", 300))),
        far_espn_seconds=max(1, int(adaptive_raw.get("far_espn_seconds", 300))),
        warmup_kalshi_seconds=max(
            1, int(adaptive_raw.get("warmup_kalshi_seconds", 30))
        ),
        warmup_espn_seconds=max(1, int(adaptive_raw.get("warmup_espn_seconds", 60))),
        post_kalshi_seconds=max(1, int(adaptive_raw.get("post_kalshi_seconds", 60))),
        post_espn_seconds=max(1, int(adaptive_raw.get("post_espn_seconds", 300))),
    )

    espn_raw = raw.get("espn") or {}
    team_names_raw = espn_raw.get("team_names") or {}
    team_colors_raw = espn_raw.get("team_colors") or {}
    player_names_raw = espn_raw.get("player_names") or {}
    star_chants_raw = espn_raw.get("star_chants") or {}
    if not isinstance(team_names_raw, dict):
        raise ConfigError("espn.team_names must be an object")
    if not isinstance(team_colors_raw, dict):
        raise ConfigError("espn.team_colors must be an object")
    if not isinstance(player_names_raw, dict):
        raise ConfigError("espn.player_names must be an object")
    if not isinstance(star_chants_raw, dict):
        raise ConfigError("espn.star_chants must be an object")
    espn = ESPNConfig(
        language=language,
        enabled=bool(espn_raw.get("enabled", False)),
        event_id=str(espn_raw.get("event_id", "")).strip(),
        league=str(espn_raw.get("league", "fifa.world")).strip(),
        label=_config_text(espn_raw.get("label"), language, path="espn.label"),
        starts_at=str(espn_raw.get("starts_at", "")).strip(),
        base_url=str(espn_raw.get("base_url", DEFAULT_ESPN_BASE_URL)).rstrip("/"),
        poll_seconds=max(1, int(espn_raw.get("poll_seconds", DEFAULT_ESPN_POLL_SECONDS))),
        favorite_team=str(espn_raw.get("favorite_team", "")).strip(),
        position_team=str(espn_raw.get("position_team", "")).strip(),
        favorite_goal_celebration=bool(espn_raw.get("favorite_goal_celebration", True)),
        startup_replay_critical_seconds=max(
            0,
            int(
                espn_raw.get(
                    "startup_replay_critical_seconds",
                    DEFAULT_STARTUP_CRITICAL_REPLAY_SECONDS,
                )
            ),
        ),
        announce_status=bool(espn_raw.get("announce_status", True)),
        announce_fouls=bool(espn_raw.get("announce_fouls", True)),
        announce_opponent_free_kicks=bool(espn_raw.get("announce_opponent_free_kicks", True)),
        announce_yellow_cards=bool(espn_raw.get("announce_yellow_cards", True)),
        announce_corners=bool(espn_raw.get("announce_corners", True)),
        announce_player_names=bool(espn_raw.get("announce_player_names", True)),
        announce_shots_on_target=bool(espn_raw.get("announce_shots_on_target", True)),
        announce_close_misses=bool(espn_raw.get("announce_close_misses", True)),
        announce_dangerous_blocks=bool(espn_raw.get("announce_dangerous_blocks", True)),
        announce_substitutions=bool(espn_raw.get("announce_substitutions", True)),
        team_names=_config_text_map(team_names_raw, language, path="espn.team_names"),
        team_colors={str(key): str(value) for key, value in team_colors_raw.items()},
        player_names=_config_text_map(player_names_raw, language, path="espn.player_names"),
        star_chants=_config_text_map(star_chants_raw, language, path="espn.star_chants"),
    )

    markets: list[MarketConfig] = []
    for idx, item in enumerate(raw.get("markets", []), start=1):
        ticker = str(item.get("ticker", "")).strip().upper()
        if not ticker:
            raise ConfigError(f"markets[{idx}] is missing ticker")
        side = str(item.get("side_i_care", "yes")).strip().lower()
        if side not in {"yes", "no"}:
            raise ConfigError(f"{ticker}: side_i_care must be yes or no")
        markets.append(
            MarketConfig(
                ticker=ticker,
                label=_config_text(
                    item.get("label"),
                    language,
                    path=f"markets[{idx}].label",
                    fallback=ticker,
                ),
                language=language,
                side_i_care=side,
                alert_move_cents=int(item.get("alert_move_cents", DEFAULT_ALERT_MOVE_CENTS)),
                speak_move_cents=int(item.get("speak_move_cents", DEFAULT_SPEAK_MOVE_CENTS)),
                min_seconds_between_alerts=int(
                    item.get("min_seconds_between_alerts", DEFAULT_MIN_ALERT_SECONDS)
                ),
                near_close_minutes=int(item.get("near_close_minutes", DEFAULT_NEAR_CLOSE_MINUTES)),
                spread_move_cents=int(item.get("spread_move_cents", DEFAULT_SPREAD_MOVE_CENTS)),
                alerts_enabled=bool(item.get("alerts_enabled", True)),
                show_in_ticker=bool(item.get("show_in_ticker", True)),
                goal_signal_enabled=bool(item.get("goal_signal_enabled", False)),
                goal_signal_move_cents=max(1, int(item.get("goal_signal_move_cents", 5))),
                goal_signal_cooldown_seconds=max(
                    1,
                    int(item.get("goal_signal_cooldown_seconds", 90)),
                ),
                goal_signal_up_speech=_config_text(
                    item.get("goal_signal_up_speech"),
                    language,
                    path=f"markets[{idx}].goal_signal_up_speech",
                ),
                goal_signal_down_speech=_config_text(
                    item.get("goal_signal_down_speech"),
                    language,
                    path=f"markets[{idx}].goal_signal_down_speech",
                ),
            )
        )

    if not markets:
        raise ConfigError("config must include at least one market")

    return WatchConfig(
        language=language,
        stackchan_host=str(raw.get("stackchan_host", DEFAULT_STACKCHAN_HOST)).strip(),
        stackchan_transport=str(raw.get("stackchan_transport", DEFAULT_STACKCHAN_TRANSPORT)).strip().lower(),
        stackchan_serial_port=str(raw.get("stackchan_serial_port", DEFAULT_STACKCHAN_SERIAL_PORT)).strip(),
        stackchan_serial_baud=int(raw.get("stackchan_serial_baud", DEFAULT_STACKCHAN_SERIAL_BAUD)),
        voice_transport=str(raw.get("voice_transport", DEFAULT_VOICE_TRANSPORT)).strip().lower(),
        mac_voice=_config_text(raw.get("mac_voice"), language, path="mac_voice"),
        mac_say_rate=int(raw.get("mac_say_rate", DEFAULT_MAC_SAY_RATE)),
        kalshi_base_url=str(raw.get("kalshi_base_url", DEFAULT_BASE_URL)).rstrip("/"),
        poll_seconds=max(1, int(raw.get("poll_seconds", DEFAULT_POLL_SECONDS))),
        max_alerts_per_cycle=max(1, int(raw.get("max_alerts_per_cycle", DEFAULT_MAX_ALERTS_PER_CYCLE))),
        startup_summary_on_watch=bool(raw.get("startup_summary_on_watch", True)),
        speak_startup_summary=bool(raw.get("speak_startup_summary", False)),
        ticker_enabled=bool(raw.get("ticker_enabled", True)),
        display_refresh_seconds=max(
            5,
            int(raw.get("display_refresh_seconds", DEFAULT_DISPLAY_REFRESH_SECONDS)),
        ),
        alert_balloon_seconds=max(
            1, min(30, int(raw.get("alert_balloon_seconds", DEFAULT_ALERT_BALLOON_SECONDS)))
        ),
        quiet_hours=quiet,
        probability_bar=probability_bar,
        setup_server=setup_server,
        adaptive_polling=adaptive_polling,
        espn=espn,
        markets=markets,
    )


def validate_config(config: WatchConfig, dry_run: bool = False) -> None:
    if config.stackchan_transport not in {"http", "serial"}:
        raise ConfigError("stackchan_transport must be http or serial")
    if config.voice_transport not in {"clip", "mac", "stackchan", "both", "none"}:
        raise ConfigError("voice_transport must be clip, mac, stackchan, both, or none")
    if config.stackchan_transport == "serial" and serial is None and not dry_run:
        raise ConfigError(
            "serial transport needs pyserial; run with PlatformIO's Python or install pyserial"
        )
    if config.espn.enabled and not config.espn.event_id:
        raise ConfigError("espn.event_id is required when ESPN monitoring is enabled")
    if (
        config.adaptive_polling.full_speed_minutes_before
        > config.adaptive_polling.warmup_minutes_before
    ):
        raise ConfigError(
            "adaptive_polling.full_speed_minutes_before must not exceed "
            "warmup_minutes_before"
        )
    if config.espn.starts_at and parse_datetime(config.espn.starts_at) is None:
        raise ConfigError("espn.starts_at must be an ISO 8601 timestamp")
    if config.probability_bar.enabled:
        configured_tickers = {market.ticker for market in config.markets}
        if config.probability_bar.mode not in {"binary_complement", "normalized_outcomes"}:
            raise ConfigError(
                "probability_bar.mode must be binary_complement or normalized_outcomes"
            )
        if config.probability_bar.market_ticker not in configured_tickers:
            raise ConfigError("probability_bar.market_ticker must match a configured market")
        if (
            config.probability_bar.mode == "normalized_outcomes"
            and config.probability_bar.right_market_ticker not in configured_tickers
        ):
            raise ConfigError(
                "probability_bar.right_market_ticker must match a configured market "
                "in normalized_outcomes mode"
            )
        if config.probability_bar.side not in {"yes", "no"}:
            raise ConfigError("probability_bar.side must be yes or no")
        for name, flag in (
            ("left_flag", config.probability_bar.left_flag),
            ("right_flag", config.probability_bar.right_flag),
        ):
            if not flag or any(character not in "abcdefghijklmnopqrstuvwxyz-" for character in flag):
                raise ConfigError(f"probability_bar.{name} must be a lowercase flag code")
        parse_hex_color(config.probability_bar.left_color)
        parse_hex_color(config.probability_bar.right_color)
    for name, color in config.espn.team_colors.items():
        try:
            parse_hex_color(color)
        except ConfigError as error:
            raise ConfigError(f"espn.team_colors[{name!r}]: {error}") from error


def parse_hex_color(value: str) -> tuple[int, int, int]:
    normalized = value.strip().removeprefix("#")
    if len(normalized) != 6 or any(character not in "0123456789abcdefABCDEF" for character in normalized):
        raise ConfigError(f"invalid RGB color {value!r}; use #RRGGBB")
    return tuple(int(normalized[index : index + 2], 16) for index in (0, 2, 4))


def command_hex_color(value: str) -> str:
    parse_hex_color(value)
    return value.strip().removeprefix("#").upper()


def dollars_to_cents(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        dollars = Decimal(str(value))
    except InvalidOperation:
        return None
    cents = (dollars * Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return int(cents)


def parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    raw = str(value)
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


@dataclass(frozen=True)
class PollingPlan:
    tier: str
    kalshi_seconds: int
    espn_seconds: int


def adaptive_polling_plan(
    config: WatchConfig,
    match: MatchSnapshot | None,
    now: datetime,
) -> PollingPlan:
    adaptive = config.adaptive_polling
    full_speed = PollingPlan("full-speed", config.poll_seconds, config.espn.poll_seconds)
    if not adaptive.enabled:
        return full_speed

    status_state = match.status_state if match is not None else "pre"
    if status_state == "in":
        return PollingPlan("live", config.poll_seconds, config.espn.poll_seconds)
    if status_state == "post":
        return PollingPlan(
            "post",
            max(config.poll_seconds, adaptive.post_kalshi_seconds),
            max(config.espn.poll_seconds, adaptive.post_espn_seconds),
        )

    starts_at = match.starts_at if match is not None else parse_datetime(config.espn.starts_at)
    if starts_at is None:
        return full_speed
    seconds_until_start = (starts_at - now).total_seconds()
    if seconds_until_start <= adaptive.full_speed_minutes_before * 60:
        return full_speed
    if seconds_until_start <= adaptive.warmup_minutes_before * 60:
        return PollingPlan(
            "warmup",
            max(config.poll_seconds, adaptive.warmup_kalshi_seconds),
            max(config.espn.poll_seconds, adaptive.warmup_espn_seconds),
        )
    return PollingPlan(
        "far",
        max(config.poll_seconds, adaptive.far_kalshi_seconds),
        max(config.espn.poll_seconds, adaptive.far_espn_seconds),
    )


def parse_hhmm(value: str) -> dt_time:
    hour_raw, minute_raw = value.split(":", 1)
    return dt_time(hour=int(hour_raw), minute=int(minute_raw))


def in_quiet_hours(quiet: QuietHours, now: datetime | None = None) -> bool:
    if not quiet.enabled:
        return False
    now = now or datetime.now().astimezone()
    start = parse_hhmm(quiet.start)
    end = parse_hhmm(quiet.end)
    current = now.time()
    if start <= end:
        return start <= current < end
    return current >= start or current < end


def http_json(url: str) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": "stackchan-matchday-watch/0.1"})
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_SECONDS) as res:
        return json.loads(res.read().decode("utf-8"))


def fetch_markets(config: WatchConfig) -> tuple[dict[str, MarketSnapshot], list[str]]:
    tickers = [market.ticker for market in config.markets]
    query = urllib.parse.urlencode({"tickers": ",".join(tickers), "limit": str(max(100, len(tickers)))})
    url = f"{config.kalshi_base_url}/markets?{query}"
    payload = http_json(url)
    by_label = {market.ticker: market.label for market in config.markets}
    snapshots: dict[str, MarketSnapshot] = {}

    for market in payload.get("markets", []):
        ticker = str(market.get("ticker", "")).upper()
        if ticker not in by_label:
            continue
        snapshots[ticker] = MarketSnapshot(
            ticker=ticker,
            label=by_label[ticker],
            status=str(market.get("status", "")),
            event_ticker=str(market.get("event_ticker", "")),
            yes_bid_cents=dollars_to_cents(market.get("yes_bid_dollars")),
            yes_ask_cents=dollars_to_cents(market.get("yes_ask_dollars")),
            no_bid_cents=dollars_to_cents(market.get("no_bid_dollars")),
            no_ask_cents=dollars_to_cents(market.get("no_ask_dollars")),
            last_price_cents=dollars_to_cents(market.get("last_price_dollars")),
            volume_24h=str(market.get("volume_24h_fp", "")),
            close_time=parse_datetime(market.get("close_time")),
            result=str(market.get("result") or ""),
            settlement_value_cents=dollars_to_cents(
                market.get("settlement_value_dollars")
            ),
        )

    missing = [ticker for ticker in tickers if ticker not in snapshots]
    return snapshots, missing


def _display_number(value: Any, fallback: str = "0") -> str:
    if value in (None, ""):
        return fallback
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return str(int(number)) if number.is_integer() else str(number)


def _match_team(raw: dict[str, Any]) -> MatchTeam:
    team = raw.get("team") or {}
    return MatchTeam(
        name=str(team.get("displayName") or team.get("name") or team.get("location") or ""),
        abbreviation=str(team.get("abbreviation") or ""),
        home_away=str(raw.get("homeAway") or ""),
        score=_display_number(raw.get("score")),
        shootout_score=_display_number(raw.get("shootoutScore"), fallback="") if raw.get("shootoutScore") is not None else "",
    )


def _player_lookup_keys(athlete_id: str, name: str, short_name: str) -> list[str]:
    return [
        value.casefold()
        for value in (athlete_id, name, short_name)
        if value
    ]


def _match_players(payload: dict[str, Any]) -> dict[str, MatchPlayer]:
    players: dict[str, MatchPlayer] = {}
    for group in payload.get("rosters") or []:
        if not isinstance(group, dict):
            continue
        team = group.get("team") or {}
        team_name = str(team.get("displayName") or team.get("name") or "")
        team_abbreviation = str(team.get("abbreviation") or "")
        for entry in group.get("roster") or []:
            if not isinstance(entry, dict):
                continue
            athlete = entry.get("athlete") or {}
            athlete_id = str(athlete.get("id") or "")
            name = str(athlete.get("displayName") or athlete.get("fullName") or "")
            short_name = str(athlete.get("shortName") or name)
            if not athlete_id and not name:
                continue
            player = MatchPlayer(
                athlete_id=athlete_id,
                name=name,
                short_name=short_name,
                jersey=str(entry.get("jersey") or athlete.get("jersey") or ""),
                team_name=team_name,
                team_abbreviation=team_abbreviation,
                position=str((entry.get("position") or {}).get("abbreviation") or ""),
            )
            for key in _player_lookup_keys(athlete_id, name, short_name):
                players[key] = player
    return players


def fetch_espn_match(config: ESPNConfig) -> MatchSnapshot:
    url = f"{config.base_url}/{urllib.parse.quote(config.league)}/summary?event={urllib.parse.quote(config.event_id)}"
    payload = http_json(url)
    header = payload.get("header") or {}
    competitions = header.get("competitions") or []
    if not competitions:
        raise ValueError(f"ESPN event {config.event_id} has no competition data")
    competition = competitions[0]
    competitors = competition.get("competitors") or []
    by_side = {str(item.get("homeAway") or ""): item for item in competitors}
    if "home" not in by_side or "away" not in by_side:
        raise ValueError(f"ESPN event {config.event_id} is missing home/away teams")
    status_type = ((competition.get("status") or {}).get("type") or {})
    commentary = payload.get("commentary") or []
    return MatchSnapshot(
        event_id=str(header.get("id") or config.event_id),
        status_state=str(status_type.get("state") or ""),
        status_name=str(status_type.get("name") or ""),
        status_detail=str(status_type.get("detail") or status_type.get("description") or ""),
        home=_match_team(by_side["home"]),
        away=_match_team(by_side["away"]),
        commentary=[item for item in commentary if isinstance(item, dict)],
        starts_at=parse_datetime(competition.get("date")),
        players=_match_players(payload),
    )


def localized_team_name(config: ESPNConfig, team: MatchTeam | str) -> str:
    if isinstance(team, MatchTeam):
        candidates = [team.name, team.abbreviation]
        fallback = team.name or team.abbreviation
    else:
        candidates = [team]
        fallback = team
    names = {key.casefold(): value for key, value in config.team_names.items()}
    for candidate in candidates:
        localized = names.get(candidate.casefold())
        if localized:
            return localized
    return fallback


def is_configured_team(config: ESPNConfig, configured_name: str, team: MatchTeam | str) -> bool:
    configured = configured_name.casefold()
    if not configured:
        return False
    if isinstance(team, MatchTeam):
        candidates = [team.name, team.abbreviation, localized_team_name(config, team)]
    else:
        candidates = [team, localized_team_name(config, team)]
    return any(candidate.casefold() == configured for candidate in candidates if candidate)


def is_favorite_team(config: ESPNConfig, team: MatchTeam | str) -> bool:
    return is_configured_team(config, config.favorite_team, team)


def fan_clip_for_team(
    config: ESPNConfig,
    team: MatchTeam | None,
    favorite_clip: str,
    opponent_clip: str,
) -> str | None:
    if team is None or not config.favorite_team:
        return None
    return favorite_clip if is_favorite_team(config, team) else opponent_clip


def team_light_rgb(config: ESPNConfig, team: MatchTeam | None) -> tuple[int, int, int] | None:
    if team is None:
        return None
    colors = {key.casefold(): value for key, value in config.team_colors.items()}
    candidates = [team.name, team.abbreviation, localized_team_name(config, team)]
    for candidate in candidates:
        color = colors.get(candidate.casefold())
        if color:
            return parse_hex_color(color)
    return None


def _score_number(value: str) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def match_winner(snapshot: MatchSnapshot) -> MatchTeam | None:
    if snapshot.home.shootout_score or snapshot.away.shootout_score:
        home_score = _score_number(snapshot.home.shootout_score or "0")
        away_score = _score_number(snapshot.away.shootout_score or "0")
    else:
        home_score = _score_number(snapshot.home.score)
        away_score = _score_number(snapshot.away.score)
    if home_score is None or away_score is None or home_score == away_score:
        return None
    return snapshot.home if home_score > away_score else snapshot.away


def configured_position_team(snapshot: MatchSnapshot, config: ESPNConfig) -> MatchTeam | None:
    if not config.position_team:
        return None
    for team in (snapshot.home, snapshot.away):
        if is_configured_team(config, config.position_team, team):
            return team
    return None


def final_result_outcome(snapshot: MatchSnapshot, config: ESPNConfig) -> str | None:
    winner = match_winner(snapshot)
    position_team = configured_position_team(snapshot, config)
    if winner is None or position_team is None:
        return None
    return "win" if winner is position_team else "lose"


def final_result_clip(snapshot: MatchSnapshot, config: ESPNConfig) -> str | None:
    outcome = final_result_outcome(snapshot, config)
    if outcome is None:
        return None
    return "favorite-win" if outcome == "win" else "favorite-lose"


def apply_final_result_reaction(
    alert: Alert,
    snapshot: MatchSnapshot,
    config: ESPNConfig,
) -> Alert:
    outcome = final_result_outcome(snapshot, config)
    if outcome is None:
        alert.prefer_dynamic_voice = True
        return alert
    position_team = configured_position_team(snapshot, config)
    alert.clip_id = "favorite-win" if outcome == "win" else "favorite-lose"
    alert.light_rgb = team_light_rgb(config, position_team)
    alert.celebration = f"result-{outcome}"
    alert.face = "happy" if outcome == "win" else "sad"
    alert.prefer_dynamic_voice = False
    return alert


def match_team(snapshot: MatchSnapshot, raw_name: str) -> MatchTeam | None:
    needle = raw_name.casefold()
    for team in (snapshot.home, snapshot.away):
        if needle in {team.name.casefold(), team.abbreviation.casefold()}:
            return team
    return None


def opposing_match_team(snapshot: MatchSnapshot, team: MatchTeam | None) -> MatchTeam | None:
    if team is None:
        return None
    if team.name.casefold() == snapshot.home.name.casefold():
        return snapshot.away
    if team.name.casefold() == snapshot.away.name.casefold():
        return snapshot.home
    return None


def match_score_text(snapshot: MatchSnapshot, config: ESPNConfig) -> str:
    home = localized_team_name(config, snapshot.home)
    away = localized_team_name(config, snapshot.away)
    home_score = snapshot.home.score
    away_score = snapshot.away.score
    if snapshot.home.shootout_score or snapshot.away.shootout_score:
        home_score += f"({snapshot.home.shootout_score or '0'})"
        away_score += f"({snapshot.away.shootout_score or '0'})"
    return f"{home} {home_score}-{away_score} {away}"


def match_label(snapshot: MatchSnapshot, config: ESPNConfig) -> str:
    return config.label or (
        f"{localized_team_name(config, snapshot.home)} vs "
        f"{localized_team_name(config, snapshot.away)}"
    )


def match_score_speech(snapshot: MatchSnapshot, config: ESPNConfig) -> str:
    home = localized_team_name(config, snapshot.home)
    away = localized_team_name(config, snapshot.away)
    if snapshot.home.shootout_score or snapshot.away.shootout_score:
        return pick(
            config.language,
            (
                f"现在{home}和{away}常规比分{snapshot.home.score}比{snapshot.away.score}，"
                f"点球{snapshot.home.shootout_score or '0'}比{snapshot.away.shootout_score or '0'}"
            ),
            (
                f"It is {home} {snapshot.home.score}, {away} {snapshot.away.score}; "
                f"penalties {snapshot.home.shootout_score or '0'} to "
                f"{snapshot.away.shootout_score or '0'}"
            ),
        )
    return pick(
        config.language,
        f"现在{home}{snapshot.home.score}比{snapshot.away.score}{away}",
        f"It is {home} {snapshot.home.score}, {away} {snapshot.away.score}",
    )


def commentary_key(item: dict[str, Any]) -> str:
    if item.get("sequence") is not None:
        return f"sequence:{item['sequence']}"
    play = item.get("play") or {}
    if play.get("id") is not None:
        return f"play:{play['id']}"
    clock = (item.get("time") or {}).get("value", "")
    return f"fallback:{clock}:{item.get('text', '')}"


def _event_team(item: dict[str, Any], snapshot: MatchSnapshot) -> MatchTeam | None:
    play = item.get("play") or {}
    raw_team = str((play.get("team") or {}).get("displayName") or "")
    return match_team(snapshot, raw_team) if raw_team else None


def _event_player_at(
    item: dict[str, Any],
    snapshot: MatchSnapshot,
    participant_index: int,
) -> MatchPlayer | None:
    play = item.get("play") or {}
    participants = play.get("participants") or []
    if participant_index >= len(participants) or not isinstance(participants[participant_index], dict):
        return None
    athlete = participants[participant_index].get("athlete") or {}
    athlete_id = str(athlete.get("id") or "")
    name = str(athlete.get("displayName") or athlete.get("fullName") or "")
    short_name = str(athlete.get("shortName") or name)
    for key in _player_lookup_keys(athlete_id, name, short_name):
        player = snapshot.players.get(key)
        if player:
            return player
    if not athlete_id and not name:
        return None
    team = _event_team(item, snapshot)
    if participant_index > 0 and str((play.get("type") or {}).get("type") or "").lower() == "foul":
        team = opposing_match_team(snapshot, team)
    return MatchPlayer(
        athlete_id=athlete_id,
        name=name,
        short_name=short_name,
        jersey="",
        team_name=team.name if team else "",
        team_abbreviation=team.abbreviation if team else "",
    )


def _event_player(item: dict[str, Any], snapshot: MatchSnapshot) -> MatchPlayer | None:
    return _event_player_at(item, snapshot, 0)


def _event_goalkeeper(
    item: dict[str, Any],
    snapshot: MatchSnapshot,
    attacking_team: MatchTeam | None,
) -> MatchPlayer | None:
    play = item.get("play") or {}
    text = str(item.get("text") or play.get("text") or "").casefold()
    seen: set[tuple[str, str]] = set()
    for player in snapshot.players.values():
        key = (player.athlete_id, player.name)
        if key in seen:
            continue
        seen.add(key)
        if player.position.casefold() not in {"g", "gk"}:
            continue
        if attacking_team and player.team_name.casefold() == attacking_team.name.casefold():
            continue
        if player.name and player.name.casefold() in text:
            return player
    return None


def _shot_assistant(item: dict[str, Any], snapshot: MatchSnapshot) -> MatchPlayer | None:
    play = item.get("play") or {}
    text = str(item.get("text") or play.get("text") or "").casefold()
    if "assisted by" not in text:
        return None
    return _event_player_at(item, snapshot, 1)


def _ends_drinks_break(item: dict[str, Any], snapshot: MatchSnapshot) -> bool:
    item_key = commentary_key(item)
    item_index = next(
        (
            index
            for index, candidate in enumerate(snapshot.commentary)
            if candidate is item or commentary_key(candidate) == item_key
        ),
        len(snapshot.commentary),
    )
    for previous in reversed(snapshot.commentary[:item_index]):
        play = previous.get("play") or {}
        previous_type = str((play.get("type") or {}).get("type") or "").lower()
        previous_text = str(previous.get("text") or play.get("text") or "").lower()
        if previous_type == "end-delay":
            return False
        if previous_type == "start-delay":
            return "drinks break" in previous_text
    return False


def _configured_player_text(mapping: dict[str, str], player: MatchPlayer | None) -> str:
    if player is None:
        return ""
    normalized = {str(key).casefold(): str(value) for key, value in mapping.items()}
    for key in _player_lookup_keys(player.athlete_id, player.name, player.short_name):
        value = normalized.get(key)
        if value:
            return value
    return ""


def localized_player_name(config: ESPNConfig, player: MatchPlayer | None) -> str:
    if player is None:
        return ""
    return _configured_player_text(config.player_names, player) or player.name or player.short_name


def player_announcement(config: ESPNConfig, player: MatchPlayer | None) -> str:
    if player is None:
        return ""
    name = localized_player_name(config, player)
    if _configured_player_text(config.star_chants, player):
        return name
    if player.jersey:
        return pick(
            config.language,
            f"{player.jersey}号球员{name}",
            f"number {player.jersey} {name}",
        )
    return name


def render_star_chant(
    config: ESPNConfig,
    player: MatchPlayer | None,
    team_name: str,
) -> str:
    chant = _configured_player_text(config.star_chants, player)
    if not chant or player is None:
        return ""
    values = {
        "name": localized_player_name(config, player),
        "number": player.jersey,
        "team": team_name,
    }
    for key, value in values.items():
        chant = chant.replace("{" + key + "}", value)
    return chant.strip()


def _event_clock(item: dict[str, Any]) -> str:
    play = item.get("play") or {}
    value = str((item.get("time") or {}).get("displayValue") or (play.get("clock") or {}).get("displayValue") or "")
    return f"{value} " if value else ""


def _team_event_face(config: ESPNConfig, team: MatchTeam | None, positive: bool) -> str:
    if team is None or not config.favorite_team:
        return "surprise"
    favorite = is_favorite_team(config, team)
    return "happy" if favorite == positive else "sad"


def alert_for_espn_commentary(
    item: dict[str, Any], snapshot: MatchSnapshot, config: ESPNConfig
) -> Alert | None:
    play = item.get("play") or {}
    play_type = str((play.get("type") or {}).get("type") or "").lower()
    text = str(item.get("text") or play.get("text") or "")
    lower_text = text.lower()
    team = _event_team(item, snapshot)
    player = _event_player(item, snapshot) if config.announce_player_names else None
    if team is None and player and player.team_name:
        team = match_team(snapshot, player.team_name)
    team_name = localized_team_name(config, team) if team else ""
    player_subject = player_announcement(config, player)
    clock = _event_clock(item)
    score = match_score_text(snapshot, config)
    score_speech = match_score_speech(snapshot, config)
    ticker = f"ESPN:{snapshot.event_id}"

    if config.announce_status and play_type == "start-delay" and "drinks break" in lower_text:
        return Alert(
            ticker=ticker,
            label=match_label(snapshot, config),
            kind="espn_drinks_break",
            priority=520,
            face="neutral",
            balloon=pick(config.language, f"进入补水时间 | {score}", f"Drinks break | {score}"),
            speech=pick(
                config.language,
                "比赛进入补水时间，先喘口气，马上回来。",
                "Drinks break. Time to catch our breath; the match will resume shortly.",
            ),
            detail=text,
            prefer_dynamic_voice=True,
        )

    if config.announce_status and play_type == "end-delay" and _ends_drinks_break(item, snapshot):
        return Alert(
            ticker=ticker,
            label=match_label(snapshot, config),
            kind="espn_drinks_break_end",
            priority=520,
            face="happy",
            balloon=pick(config.language, f"补水结束，比赛继续 | {score}", f"Play resumes | {score}"),
            speech=pick(config.language, "补水结束，比赛继续！", "The drinks break is over. Play resumes!"),
            detail=text,
            prefer_dynamic_voice=True,
        )

    if play_type in {"penalty---scored", "penalty---missed", "penalty---saved"}:
        goalkeeper = _event_goalkeeper(item, snapshot, team)
        goalkeeper_name = localized_player_name(config, goalkeeper)
        subject = player_subject or team_name or pick(config.language, "一方", "one side")
        if play_type == "penalty---scored":
            positive = True
            clip_id = fan_clip_for_team(
                config,
                team,
                "favorite-penalty-scored",
                "opponent-penalty-scored",
            )
            light_rgb = team_light_rgb(config, team)
            balloon = pick(
                config.language,
                f"{clock}{subject}点球命中! | {score}",
                f"{clock}{subject} scores a penalty! | {score}",
            )
            speech = pick(
                config.language,
                f"{subject}点球命中。{score_speech}。",
                f"{subject} scores from the spot. {score_speech}.",
            )
        elif play_type == "penalty---saved":
            saver = goalkeeper_name or pick(config.language, "门将", "the goalkeeper")
            positive = False
            clip_id = fan_clip_for_team(
                config,
                team,
                "favorite-penalty-missed",
                "opponent-penalty-missed",
            )
            light_rgb = None
            balloon = pick(
                config.language,
                f"{clock}{subject}点球被{saver}扑出! | {score}",
                f"{clock}{saver} saves {subject}'s penalty! | {score}",
            )
            speech = pick(
                config.language,
                f"{subject}的点球被{saver}扑出来了。{score_speech}。",
                f"{saver} saves {subject}'s penalty. {score_speech}.",
            )
        else:
            positive = False
            clip_id = fan_clip_for_team(
                config,
                team,
                "favorite-penalty-missed",
                "opponent-penalty-missed",
            )
            light_rgb = None
            balloon = pick(
                config.language,
                f"{clock}{subject}点球罚失! | {score}",
                f"{clock}{subject} misses the penalty! | {score}",
            )
            speech = pick(
                config.language,
                f"{subject}点球罚失。{score_speech}。",
                f"{subject} misses the penalty. {score_speech}.",
            )
        return Alert(
            ticker=ticker,
            label=match_label(snapshot, config),
            kind="espn_penalty",
            priority=950,
            face=_team_event_face(config, team, positive),
            balloon=balloon,
            speech=speech,
            detail=text,
            clip_id=clip_id,
            light_rgb=light_rgb,
            prefer_dynamic_voice=bool((player or goalkeeper) and clip_id),
        )

    if play_type == "foul" and lower_text.startswith("penalty ") and "draws a foul" in lower_text:
        beneficiary = _event_player_at(item, snapshot, 1)
        awarded_team = (
            match_team(snapshot, beneficiary.team_name)
            if beneficiary and beneficiary.team_name
            else opposing_match_team(snapshot, team)
        )
        awarded_team_name = (
            localized_team_name(config, awarded_team)
            if awarded_team
            else pick(config.language, "一方", "one side")
        )
        beneficiary_name = (
            player_announcement(config, beneficiary)
            if config.announce_player_names
            else pick(config.language, "场上球员", "the player")
        )
        return Alert(
            ticker=ticker,
            label=match_label(snapshot, config),
            kind="espn_penalty_awarded",
            priority=960,
            face=_team_event_face(config, awarded_team, True),
            balloon=pick(
                config.language,
                f"{clock}{awarded_team_name}获得点球! | {score}",
                f"{clock}Penalty to {awarded_team_name}! | {score}",
            ),
            speech=pick(
                config.language,
                f"点球！{beneficiary_name}在禁区里制造犯规，{awarded_team_name}获得点球！",
                f"Penalty! {beneficiary_name} draws the foul in the box. Penalty to {awarded_team_name}!",
            ),
            detail=text,
            clip_id="penalty-awarded",
            prefer_dynamic_voice=bool(beneficiary),
        )

    if play_type in {"penalty", "penalty-awarded", "penalty---awarded"}:
        subject = team_name or pick(config.language, "场上", "the attacking side")
        return Alert(
            ticker=ticker,
            label=match_label(snapshot, config),
            kind="espn_penalty_awarded",
            priority=960,
            face="surprise",
            balloon=pick(
                config.language,
                f"{clock}点球! {subject} | {score}",
                f"{clock}Penalty! {subject} | {score}",
            ),
            speech=pick(
                config.language,
                f"点球！裁判指向点球点。{score_speech}。",
                f"Penalty! The referee points to the spot. {score_speech}.",
            ),
            detail=text,
            clip_id="penalty-awarded",
        )

    if lower_text.startswith("goal!") or play_type == "goal" or play_type.startswith("goal---"):
        subject = player_subject or team_name or pick(config.language, "场上", "one side")
        favorite_goal = bool(team and is_favorite_team(config, team))
        chant = render_star_chant(config, player, team_name) if favorite_goal else ""
        if chant:
            speech = join_sentences(config.language, chant, score_speech)
        elif favorite_goal:
            speech = pick(
                config.language,
                f"{subject}！球进啦！{score_speech}。",
                f"{subject}! Goal! {score_speech}.",
            )
        else:
            speech = pick(
                config.language,
                f"{subject}进球了。{score_speech}。",
                f"{subject} scores. {score_speech}.",
            )
        return Alert(
            ticker=ticker,
            label=match_label(snapshot, config),
            kind="espn_goal",
            priority=1000,
            face=_team_event_face(config, team, True),
            balloon=pick(
                config.language,
                f"{clock}{subject}进球! | {score}",
                f"{clock}{subject} scores! | {score}",
            ),
            speech=speech,
            detail=text,
            clip_id=fan_clip_for_team(config, team, "favorite-goal", "opponent-goal"),
            light_rgb=team_light_rgb(config, team),
            celebration=(
                "goal" if favorite_goal and config.favorite_goal_celebration else None
            ),
            prefer_dynamic_voice=bool(player),
        )

    if config.announce_substitutions and play_type == "substitution":
        incoming = _event_player_at(item, snapshot, 0)
        outgoing = _event_player_at(item, snapshot, 1)
        incoming_name = (
            player_announcement(config, incoming)
            if config.announce_player_names
            else pick(config.language, "一名球员", "a player")
        )
        outgoing_name = (
            player_announcement(config, outgoing)
            if config.announce_player_names
            else pick(config.language, "一名球员", "a player")
        )
        subject = team_name or pick(config.language, "场上球队", "the team")
        injury = "because of an injury" in lower_text
        speech = pick(
            config.language,
            f"{subject}换人，{incoming_name}上场，{outgoing_name}被换下。",
            f"Substitution for {subject}: {incoming_name} comes on for {outgoing_name}.",
        )
        if injury:
            speech += pick(
                config.language,
                f"{outgoing_name}可能有伤，先休息一下。",
                f" {outgoing_name} may be injured.",
            )
        return Alert(
            ticker=ticker,
            label=match_label(snapshot, config),
            kind="espn_substitution",
            priority=620 if injury else 560,
            face=(
                "sad"
                if injury and team and is_favorite_team(config, team)
                else "neutral"
            ),
            balloon=pick(
                config.language,
                f"{clock}{subject}换人 | {incoming_name} ↑ {outgoing_name} ↓",
                f"{clock}{subject} sub | {incoming_name} IN - {outgoing_name} OUT",
            ),
            speech=speech,
            detail=text,
            prefer_dynamic_voice=True,
        )

    shooter = player_subject or team_name or pick(config.language, "场上球员", "the player")
    assistant = _shot_assistant(item, snapshot) if config.announce_player_names else None
    assistant_subject = player_announcement(config, assistant)
    if assistant_subject:
        assist_intro = (
            pick(
                config.language,
                f"{assistant_subject}送出传中，",
                f"{assistant_subject} sends in the cross, ",
            )
            if "with a cross" in lower_text
            else pick(
                config.language,
                f"{assistant_subject}送出传球，",
                f"{assistant_subject} provides the pass, ",
            )
        )
    else:
        assist_intro = ""
    favorite_attack = bool(team and is_favorite_team(config, team))
    very_close = "very close range" in lower_text or "six yard box" in lower_text
    in_box = very_close or "centre of the box" in lower_text or "center of the box" in lower_text
    is_header = "header" in lower_text
    close_miss = any(
        phrase in lower_text
        for phrase in (" is close", "just wide", "just over", "narrowly wide", "narrowly over")
    )
    hit_woodwork = play_type in {"hit-woodwork", "shot-hit-woodwork", "woodwork"} or any(
        phrase in lower_text
        for phrase in ("hits the post", "hits the left post", "hits the right post", "hits the bar", "hits the crossbar")
    )

    if config.announce_close_misses and hit_woodwork:
        if favorite_attack:
            speech = pick(
                config.language,
                f"哎呀门框！{assist_intro}{shooter}这脚差一点就进了！",
                f"Off the woodwork! {assist_intro}{shooter} was inches away from scoring!",
            )
            face = "surprise"
        else:
            speech = pick(
                config.language,
                f"吓一跳，打中门框！{assist_intro}{shooter}差一点破门。",
                f"That was close! {assist_intro}{shooter} hits the woodwork.",
            )
            face = "sad"
        return Alert(
            ticker=ticker,
            label=match_label(snapshot, config),
            kind="espn_woodwork",
            priority=840,
            face=face,
            balloon=pick(
                config.language,
                f"{clock}{shooter}击中门框! | {score}",
                f"{clock}{shooter} hits the woodwork! | {score}",
            ),
            speech=speech,
            detail=text,
            prefer_dynamic_voice=True,
        )

    if config.announce_shots_on_target and play_type == "shot-on-target":
        goalkeeper = _event_goalkeeper(item, snapshot, team)
        goalkeeper_name = localized_player_name(config, goalkeeper) or pick(
            config.language, "门将", "the goalkeeper"
        )
        goalkeeper_team = match_team(snapshot, goalkeeper.team_name) if goalkeeper else None
        favorite_save = bool(goalkeeper_team and is_favorite_team(config, goalkeeper_team))
        if very_close and is_header:
            attempt = pick(config.language, "近距离头球攻门", "close-range header")
        elif very_close:
            attempt = pick(config.language, "近距离攻门", "close-range shot")
        elif is_header:
            attempt = pick(config.language, "头球攻门", "header")
        else:
            attempt = pick(config.language, "射门", "shot")
        if favorite_save:
            speech = pick(
                config.language,
                f"{goalkeeper_name}立功了！{assist_intro}{shooter}{attempt}，被他扑了出来！",
                f"Great save by {goalkeeper_name}! {assist_intro}{goalkeeper_name} keeps out {shooter}'s {attempt}.",
            )
            face = "happy"
        elif favorite_attack and very_close:
            speech = pick(
                config.language,
                f"好机会！{assist_intro}{shooter}{attempt}，{goalkeeper_name}神扑救险！",
                f"What a chance! {assist_intro}{goalkeeper_name} makes a huge save from {shooter}'s {attempt}.",
            )
            face = "surprise"
        elif favorite_attack:
            speech = pick(
                config.language,
                f"{assist_intro}{shooter}射正了！{goalkeeper_name}把球扑了出来。",
                f"{assist_intro}{shooter} is on target, but {goalkeeper_name} makes the save.",
            )
            face = "surprise"
        else:
            speech = pick(
                config.language,
                f"危险！{assist_intro}{shooter}{attempt}，被{goalkeeper_name}扑出。",
                f"Danger! {assist_intro}{goalkeeper_name} saves {shooter}'s {attempt}.",
            )
            face = "sad"
        return Alert(
            ticker=ticker,
            label=match_label(snapshot, config),
            kind="espn_shot_saved",
            priority=820 if very_close else 800,
            face=face,
            balloon=pick(
                config.language,
                f"{clock}{shooter}{attempt}，{goalkeeper_name}扑出! | {score}",
                f"{clock}{goalkeeper_name} saves {shooter}'s {attempt}! | {score}",
            ),
            speech=speech,
            detail=text,
            prefer_dynamic_voice=True,
        )

    if config.announce_close_misses and play_type == "shot-off-target" and (close_miss or in_box):
        opening = pick(
            config.language,
            "差一点！" if favorite_attack else "这球有威胁！",
            "So close! " if favorite_attack else "Dangerous chance! ",
        )
        if is_header:
            speech = pick(
                config.language,
                f"{opening}{assist_intro}{shooter}头球攻门，可惜顶偏了。",
                f"{opening}{assist_intro}{shooter}'s header goes wide.",
            )
        elif in_box:
            speech = pick(
                config.language,
                f"{opening}{assist_intro}{shooter}在禁区里起脚，可惜打偏了。",
                f"{opening}{assist_intro}{shooter} shoots from inside the box but misses.",
            )
        else:
            speech = pick(
                config.language,
                f"{opening}{assist_intro}{shooter}这脚擦着门边出去了。",
                f"{opening}{assist_intro}{shooter}'s shot flashes just wide.",
            )
        return Alert(
            ticker=ticker,
            label=match_label(snapshot, config),
            kind="espn_close_miss",
            priority=760,
            face="surprise" if favorite_attack else "sad",
            balloon=pick(
                config.language,
                f"{clock}{shooter}攻门差一点! | {score}",
                f"{clock}{shooter} goes close! | {score}",
            ),
            speech=speech,
            detail=text,
            prefer_dynamic_voice=True,
        )

    if config.announce_dangerous_blocks and play_type == "shot-blocked" and in_box:
        opening = pick(
            config.language,
            f"{team_name}这波有威胁！" if favorite_attack and team_name else "危险！",
            f"Dangerous attack by {team_name}! " if favorite_attack and team_name else "Danger! ",
        )
        return Alert(
            ticker=ticker,
            label=match_label(snapshot, config),
            kind="espn_shot_blocked",
            priority=730,
            face="surprise" if favorite_attack else "sad",
            balloon=pick(
                config.language,
                f"{clock}{shooter}禁区攻门被封堵! | {score}",
                f"{clock}{shooter}'s shot is blocked! | {score}",
            ),
            speech=pick(
                config.language,
                f"{opening}{assist_intro}{shooter}在禁区里起脚，被防守球员封堵了。",
                f"{opening}{assist_intro}{shooter} shoots inside the box, but the defense blocks it.",
            ),
            detail=text,
            prefer_dynamic_voice=True,
        )

    if play_type in {"red-card", "second-yellow-card"} or bool(play.get("redCard")) or "red card" in lower_text:
        subject = player_subject or team_name or pick(config.language, "场上球员", "the player")
        clip_id = fan_clip_for_team(
            config,
            team,
            "favorite-red-card",
            "opponent-red-card",
        )
        return Alert(
            ticker=ticker,
            label=match_label(snapshot, config),
            kind="espn_red_card",
            priority=900,
            face=_team_event_face(config, team, False),
            balloon=pick(
                config.language,
                f"{clock}{subject}红牌! | {score}",
                f"{clock}Red card: {subject}! | {score}",
            ),
            speech=pick(
                config.language,
                f"红牌！{subject}被罚下。{score_speech}。",
                f"Red card! {subject} is sent off. {score_speech}.",
            ),
            detail=text,
            clip_id=clip_id,
            prefer_dynamic_voice=bool(player and clip_id),
        )

    if config.announce_yellow_cards and play_type == "yellow-card":
        subject = player_subject or team_name or pick(config.language, "场上球员", "the player")
        return Alert(
            ticker=ticker,
            label=match_label(snapshot, config),
            kind="espn_yellow_card",
            priority=850,
            face=_team_event_face(config, team, False),
            balloon=pick(
                config.language,
                f"{clock}{subject}黄牌! | {score}",
                f"{clock}Yellow card: {subject} | {score}",
            ),
            speech=pick(
                config.language,
                f"黄牌！{subject}被警告。{score_speech}。",
                f"Yellow card for {subject}. {score_speech}.",
            ),
            detail=text,
            clip_id="yellow-card",
            prefer_dynamic_voice=bool(player),
        )

    if config.announce_corners and play_type == "corner-awarded":
        subject = team_name or pick(config.language, "一方", "one side")
        return Alert(
            ticker=ticker,
            label=match_label(snapshot, config),
            kind="espn_corner",
            priority=700,
            face=_team_event_face(config, team, True),
            balloon=pick(
                config.language,
                f"{clock}{subject}角球! | {score}",
                f"{clock}Corner to {subject}! | {score}",
            ),
            speech=pick(
                config.language,
                f"{subject}获得角球。{score_speech}。",
                f"Corner to {subject}. {score_speech}.",
            ),
            detail=text,
            clip_id="corner",
        )

    if (
        config.announce_opponent_free_kicks
        and play_type == "foul"
        and "wins a free kick" in lower_text
    ):
        beneficiary = _event_player_at(item, snapshot, 1)
        awarded_team = (
            match_team(snapshot, beneficiary.team_name)
            if beneficiary and beneficiary.team_name
            else opposing_match_team(snapshot, team)
        )
        if awarded_team and config.favorite_team and is_favorite_team(config, awarded_team):
            return None
        if awarded_team is None:
            return None
        awarded_team_name = localized_team_name(config, awarded_team)
        beneficiary_name = (
            player_announcement(config, beneficiary)
            if config.announce_player_names
            else pick(config.language, "场上球员", "the player")
        )
        english_beneficiary = (
            beneficiary_name[:1].upper() + beneficiary_name[1:]
            if beneficiary_name
            else "The player"
        )
        location_code = ""
        if "attacking half" in lower_text:
            location_code = "attacking_half"
        elif "left wing" in lower_text:
            location_code = "left_wing"
        elif "right wing" in lower_text:
            location_code = "right_wing"
        elif "defensive half" in lower_text:
            location_code = "defensive_half"
        locations = {
            "attacking_half": pick(config.language, "前场", "in the attacking half"),
            "left_wing": pick(config.language, "左路", "on the left wing"),
            "right_wing": pick(config.language, "右路", "on the right wing"),
            "defensive_half": pick(config.language, "后场", "in the defensive half"),
        }
        location = locations.get(location_code, "")
        english_location = f" {location}" if location else ""
        dangerous = location_code in {"attacking_half", "left_wing", "right_wing"}
        return Alert(
            ticker=ticker,
            label=match_label(snapshot, config),
            kind="espn_opponent_free_kick",
            priority=740 if dangerous else 680,
            face="sad",
            balloon=pick(
                config.language,
                f"{clock}{awarded_team_name}获得{location}任意球 | {score}",
                f"{clock}Free kick to {awarded_team_name}{english_location} | {score}",
            ),
            speech=pick(
                config.language,
                f"{awarded_team_name}获得{location}任意球，{beneficiary_name}制造了这次犯规。",
                f"Free kick to {awarded_team_name}{english_location}. {english_beneficiary} draws the foul.",
            ),
            detail=text,
            prefer_dynamic_voice=True,
        )

    if config.announce_fouls and play_type == "foul" and lower_text.startswith("foul by"):
        subject = player_subject or team_name or pick(config.language, "场上球员", "the player")
        return Alert(
            ticker=ticker,
            label=match_label(snapshot, config),
            kind="espn_foul",
            priority=650,
            face=_team_event_face(config, team, False),
            balloon=pick(
                config.language,
                f"{clock}{subject}犯规 | {score}",
                f"{clock}Foul by {subject} | {score}",
            ),
            speech=pick(
                config.language,
                f"{subject}犯规，裁判哨响。{score_speech}。",
                f"Foul by {subject}. The referee blows the whistle. {score_speech}.",
            ),
            detail=text,
            clip_id="foul",
            prefer_dynamic_voice=bool(player),
        )

    if not config.announce_status:
        return None
    status_codes = {
        "halftime": "halftime",
        "end-regular-time": "end_regular",
        "end-extra-time": "end_extra",
        "start-shootout": "shootout_start",
    }
    status_code = status_codes.get(play_type)
    if lower_text.startswith("first half begins"):
        status_code = "kickoff"
    elif lower_text.startswith("second half begins"):
        status_code = "second_half"
    elif lower_text.startswith("penalty shootout ends"):
        status_code = "shootout_end"
    elif lower_text.startswith("match ends"):
        status_code = "full_time"
    if not status_code:
        return None
    status_labels = {
        "kickoff": pick(config.language, "比赛开始", "Kickoff"),
        "halftime": pick(config.language, "半场结束", "Half-time"),
        "second_half": pick(config.language, "下半场开始", "Second half"),
        "end_regular": pick(config.language, "常规时间结束", "End of regulation"),
        "end_extra": pick(config.language, "加时结束", "End of extra time"),
        "shootout_start": pick(config.language, "点球大战开始", "Penalty shootout"),
        "shootout_end": pick(config.language, "点球大战结束", "Penalty shootout over"),
        "full_time": pick(config.language, "比赛结束", "Full time"),
    }
    message = status_labels[status_code]
    is_final = status_code in {"full_time", "shootout_end"}
    clip_id = "match-start" if status_code == "kickoff" else None
    speech = join_sentences(config.language, message, score_speech)
    if status_code == "second_half":
        speech = pick(
            config.language,
            f"下半场开始，双方回到场上，继续看球！{score_speech}。",
            f"The second half is underway. Both teams are back on the pitch. {score_speech}.",
        )
    alert = Alert(
        ticker=ticker,
        label=match_label(snapshot, config),
        kind="espn_status",
        priority=500,
        face="surprise" if status_code in {"kickoff", "second_half", "shootout_start"} else "neutral",
        balloon=f"{message} | {score}",
        speech=speech,
        detail=text,
        clip_id=clip_id,
        prefer_dynamic_voice=clip_id is None,
        is_final=is_final,
    )
    return apply_final_result_reaction(alert, snapshot, config) if is_final else alert


def _status_change_alert(snapshot: MatchSnapshot, config: ESPNConfig) -> Alert | None:
    if not config.announce_status:
        return None
    if snapshot.status_state == "in":
        message = pick(config.language, "比赛开始", "Kickoff")
        face = "surprise"
        status_code = "kickoff"
    elif snapshot.status_state == "post":
        message = pick(config.language, "比赛结束", "Full time")
        face = "neutral"
        status_code = "full_time"
    else:
        return None
    score = match_score_text(snapshot, config)
    alert = Alert(
        ticker=f"ESPN:{snapshot.event_id}",
        label=match_label(snapshot, config),
        kind="espn_status",
        priority=500,
        face=face,
        balloon=f"{message} | {score}",
        speech=join_sentences(config.language, message, match_score_speech(snapshot, config)),
        detail=f"ESPN status -> {snapshot.status_state} ({snapshot.status_detail})",
        clip_id="match-start" if snapshot.status_state == "in" else None,
        prefer_dynamic_voice=snapshot.status_state == "post",
        is_final=status_code == "full_time",
    )
    return apply_final_result_reaction(alert, snapshot, config) if snapshot.status_state == "post" else alert


def _alert_with_source(
    item: dict[str, Any],
    snapshot: MatchSnapshot,
    config: ESPNConfig,
) -> Alert | None:
    alert = alert_for_espn_commentary(item, snapshot, config)
    if alert is None:
        return None
    play = item.get("play") or {}
    alert.source_event_at = parse_datetime(play.get("wallclock"))
    return alert


def is_final_status_alert(alert: Alert) -> bool:
    return alert.kind == "espn_status" and alert.is_final


def evaluate_espn_match(snapshot: MatchSnapshot, config: ESPNConfig, state: ESPNState) -> list[Alert]:
    keys = {commentary_key(item) for item in snapshot.commentary}
    if not state.initialized:
        state.seen_commentary.update(keys)
        state.last_status_state = snapshot.status_state
        state.final_result_announced = snapshot.status_state == "post"
        state.initialized = True
        if config.startup_replay_critical_seconds <= 0:
            return []
        cutoff = datetime.now(timezone.utc).timestamp() - config.startup_replay_critical_seconds
        critical_kinds = {
            "espn_goal",
            "espn_penalty",
            "espn_penalty_awarded",
            "espn_red_card",
        }
        recent_critical_alerts: list[Alert] = []
        for item in snapshot.commentary:
            play = item.get("play") or {}
            event_at = parse_datetime(play.get("wallclock"))
            if event_at is None or event_at.timestamp() < cutoff:
                continue
            alert = _alert_with_source(item, snapshot, config)
            if alert and alert.kind in critical_kinds:
                recent_critical_alerts.append(alert)
        return recent_critical_alerts

    new_items = [item for item in snapshot.commentary if commentary_key(item) not in state.seen_commentary]
    state.seen_commentary.update(keys)
    alerts: list[Alert] = []
    for item in new_items:
        alert = _alert_with_source(item, snapshot, config)
        if alert is not None:
            if is_final_status_alert(alert):
                if state.final_result_announced:
                    continue
                state.final_result_announced = True
            alerts.append(alert)
    status_changed = bool(state.last_status_state) and snapshot.status_state != state.last_status_state
    if status_changed and not any(alert.kind == "espn_status" for alert in alerts):
        status_alert = _status_change_alert(snapshot, config)
        if status_alert and not (
            is_final_status_alert(status_alert) and state.final_result_announced
        ):
            alerts.append(status_alert)
            if is_final_status_alert(status_alert):
                state.final_result_announced = True
    state.last_status_state = snapshot.status_state
    return alerts


def fmt_price(cents: int | None) -> str:
    return "--" if cents is None else str(cents)


def english_quantity(value: Any, singular: str) -> str:
    try:
        is_singular = abs(float(value)) == 1
    except (TypeError, ValueError):
        is_singular = False
    unit = singular if is_singular else f"{singular}s"
    return f"{value} {unit}"


def fmt_delta(delta: int) -> str:
    if delta > 0:
        return f"+{delta}c"
    return f"{delta}c"


def side_text(side: str) -> str:
    return "YES" if side == "yes" else "NO"


def balloon_for_snapshot(snapshot: MarketSnapshot, delta: int | None = None) -> str:
    lines = [
        snapshot.label,
        f"YES {fmt_price(snapshot.yes_bid_cents)} / {fmt_price(snapshot.yes_ask_cents)}",
        f"NO {fmt_price(snapshot.no_bid_cents)} / {fmt_price(snapshot.no_ask_cents)}",
    ]
    if delta is not None:
        lines.append(fmt_delta(delta))
    return " | ".join(lines)


def speech_for_price_move(snapshot: MarketSnapshot, config: MarketConfig, delta: int, mid: int) -> str:
    if config.language == "en":
        direction = "up" if delta > 0 else "down"
        intensity = " sharply" if abs(delta) >= config.speak_move_cents else ""
        return (
            f"{snapshot.label} {side_text(config.side_i_care)} midpoint is "
            f"{english_quantity(mid, 'cent')}, {direction}{intensity} by "
            f"{english_quantity(abs(delta), 'cent')} since the last alert."
        )
    direction = "涨了" if delta > 0 else "跌了"
    intensity = "大幅" if abs(delta) >= config.speak_move_cents else ""
    return f"{snapshot.label} {side_text(config.side_i_care)} 中间价{mid}分，比上次{intensity}{direction}{abs(delta)}分。"


def can_alert(state: MarketState, config: MarketConfig, now_ts: float) -> bool:
    return now_ts - state.last_alert_at >= config.min_seconds_between_alerts


def evaluate_market(
    snapshot: MarketSnapshot,
    config: MarketConfig,
    state: MarketState,
    now: datetime,
) -> list[Alert]:
    alerts: list[Alert] = []
    now_ts = now.timestamp()
    mid = snapshot.implied_probability(config.side_i_care)
    previous_alert_mid = state.last_alert_mid_cents
    previous_observed_mid = state.last_observed_mid_cents
    previous_spread = state.last_yes_spread_cents
    current_spread = snapshot.yes_spread()
    market_is_active = snapshot.status.lower() not in CLOSED_STATUSES

    if state.last_status is not None and snapshot.status != state.last_status:
        face = "sad" if snapshot.status.lower() in CLOSED_STATUSES else "surprise"
        alerts.append(
            Alert(
                ticker=snapshot.ticker,
                label=snapshot.label,
                kind="status_change",
                priority=90,
                face=face,
                balloon=pick(
                    config.language,
                    f"{snapshot.label} | 状态 {snapshot.status}",
                    f"{snapshot.label} | Status: {snapshot.status}",
                ),
                speech=pick(
                    config.language,
                    f"{snapshot.label} 状态变为 {snapshot.status}。",
                    f"{snapshot.label} status changed to {snapshot.status}.",
                ),
                detail=f"status {state.last_status} -> {snapshot.status}",
            )
        )

    if market_is_active and snapshot.close_time and not state.near_close_alerted:
        seconds_left = (snapshot.close_time - now).total_seconds()
        if 0 < seconds_left <= config.near_close_minutes * 60:
            minutes_left = max(1, math.ceil(seconds_left / 60))
            alerts.append(
                Alert(
                    ticker=snapshot.ticker,
                    label=snapshot.label,
                    kind="near_close",
                    priority=70,
                    face="surprise",
                    balloon=pick(
                        config.language,
                        f"{snapshot.label} | 还有约 {minutes_left} 分钟收盘",
                        f"{snapshot.label} | Closes in about {minutes_left} min",
                    ),
                    speech=pick(
                        config.language,
                        f"{snapshot.label} 还有约{minutes_left}分钟收盘。",
                        f"{snapshot.label} closes in about {english_quantity(minutes_left, 'minute')}.",
                    ),
                    detail=f"close_time={snapshot.close_time.isoformat()}",
                )
            )
            state.near_close_alerted = True

    if market_is_active and previous_spread is not None and current_spread is not None:
        spread_delta = current_spread - previous_spread
        if abs(spread_delta) >= config.spread_move_cents and can_alert(state, config, now_ts):
            kind = "spread_widen" if spread_delta > 0 else "spread_tighten"
            word = pick(
                config.language,
                "变宽" if spread_delta > 0 else "收窄",
                "widens" if spread_delta > 0 else "tightens",
            )
            alerts.append(
                Alert(
                    ticker=snapshot.ticker,
                    label=snapshot.label,
                    kind=kind,
                    priority=40,
                    face="surprise" if spread_delta > 0 else "neutral",
                    balloon=pick(
                        config.language,
                        f"{snapshot.label} | YES 价差{word} | {previous_spread}c -> {current_spread}c",
                        f"{snapshot.label} | YES spread {word} | {previous_spread}c -> {current_spread}c",
                    ),
                    speech=pick(
                        config.language,
                        f"{snapshot.label} YES 盘口价差{word}到{current_spread}分。",
                        f"{snapshot.label} YES spread {word} to {english_quantity(current_spread, 'cent')}.",
                    ),
                    detail=f"yes_spread {previous_spread}c -> {current_spread}c",
                )
            )

    goal_signal_triggered = False
    if (
        market_is_active
        and mid is not None
        and previous_observed_mid is not None
        and config.goal_signal_enabled
    ):
        rapid_delta = mid - previous_observed_mid
        goal_signal_ready = (
            now_ts - state.last_goal_signal_at >= config.goal_signal_cooldown_seconds
        )
        if abs(rapid_delta) >= config.goal_signal_move_cents and goal_signal_ready:
            rising = rapid_delta > 0
            speech = (
                config.goal_signal_up_speech
                if rising
                else config.goal_signal_down_speech
            )
            if not speech:
                speech = pick(
                    config.language,
                    (
                        f"盘口突然{'拉升' if rising else '跳水'}，"
                        "场上可能出现关键事件，等文字直播确认。"
                    ),
                    (
                        f"The market just {'jumped' if rising else 'dropped'} sharply. "
                        "There may be a major event on the pitch; waiting for commentary confirmation."
                    ),
                )
            alerts.append(
                Alert(
                    ticker=snapshot.ticker,
                    label=snapshot.label,
                    kind="market_goal_signal",
                    priority=930,
                    face="happy" if rising else "sad",
                    balloon=pick(
                        config.language,
                        f"盘口突变 {fmt_delta(rapid_delta)} | 疑似进球，等待确认",
                        f"Market move {fmt_delta(rapid_delta)} | Possible goal; awaiting confirmation",
                    ),
                    speech=speech,
                    detail=(
                        f"rapid {side_text(config.side_i_care)} move "
                        f"{previous_observed_mid}c -> {mid}c"
                    ),
                    clip_id="odds-up" if rising else "odds-down",
                    prefer_dynamic_voice=True,
                )
            )
            state.last_goal_signal_at = now_ts
            goal_signal_triggered = True

    if market_is_active and mid is not None:
        baseline = previous_alert_mid if previous_alert_mid is not None else previous_observed_mid
        if baseline is not None:
            delta = mid - baseline
            if (
                not goal_signal_triggered
                and abs(delta) >= config.alert_move_cents
                and can_alert(state, config, now_ts)
            ):
                face = "happy" if delta > 0 else "sad"
                if abs(delta) >= config.alert_move_cents * 2:
                    face = "surprise"
                speech = None
                clip_id = None
                if abs(delta) >= config.speak_move_cents:
                    speech = speech_for_price_move(snapshot, config, delta, mid)
                    clip_id = "odds-up" if delta > 0 else "odds-down"
                alerts.append(
                    Alert(
                        ticker=snapshot.ticker,
                        label=snapshot.label,
                        kind="price_move",
                        priority=100 + abs(delta),
                        face=face,
                        balloon=balloon_for_snapshot(snapshot, delta),
                        speech=speech,
                        detail=f"{side_text(config.side_i_care)} mid {baseline}c -> {mid}c",
                        clip_id=clip_id,
                    )
                )

    state.last_observed_mid_cents = mid
    state.last_yes_spread_cents = current_spread
    state.last_status = snapshot.status
    return alerts


def mark_alert_sent(alert: Alert, snapshot: MarketSnapshot, config: MarketConfig, state: MarketState, now: datetime) -> None:
    state.last_alert_at = now.timestamp()
    mid = snapshot.implied_probability(config.side_i_care)
    if mid is not None:
        state.last_alert_mid_cents = mid


def alert_queue_ttl_seconds(alert: Alert) -> int:
    return {
        "espn_goal": 120,
        "espn_penalty": 120,
        "espn_penalty_awarded": 120,
        "espn_red_card": 120,
        "espn_yellow_card": 60,
        "espn_woodwork": 120,
        "espn_shot_saved": 90,
        "espn_close_miss": 75,
        "espn_shot_blocked": 60,
        "espn_opponent_free_kick": 60,
        "espn_drinks_break": 120,
        "espn_drinks_break_end": 120,
        "espn_substitution": 120,
        "espn_corner": 25,
        "espn_foul": 20,
        "espn_status": 90,
        "market_goal_signal": 30,
    }.get(alert.kind, 300)


def alert_queue_key(alert: Alert) -> tuple[str, ...]:
    coalesced_live_kinds = {
        "espn_corner",
        "espn_foul",
        "espn_shot_saved",
        "espn_close_miss",
        "espn_shot_blocked",
        "espn_opponent_free_kick",
    }
    if not alert.ticker.startswith("ESPN:") or alert.kind in coalesced_live_kinds:
        return (alert.ticker, alert.kind)
    return (alert.ticker, alert.kind, alert.detail)


def merge_alert_queue(
    existing: list[QueuedAlert],
    current: list[PendingAlertContext],
    now_monotonic: float,
) -> list[QueuedAlert]:
    candidates = [
        item
        for item in existing
        if now_monotonic - item.queued_at <= alert_queue_ttl_seconds(item.alert)
    ]
    candidates.extend(
        QueuedAlert(alert, snapshot, market, state, now_monotonic)
        for alert, snapshot, market, state in current
    )
    if any(item.alert.kind == "espn_goal" for item in candidates):
        candidates = [
            item for item in candidates if item.alert.kind != "market_goal_signal"
        ]

    by_key: dict[tuple[str, ...], QueuedAlert] = {}
    for item in candidates:
        key = alert_queue_key(item.alert)
        previous = by_key.get(key)
        if previous is None or item.queued_at >= previous.queued_at:
            by_key[key] = item

    queued = sorted(
        by_key.values(),
        key=lambda item: (-item.alert.priority, item.queued_at),
    )
    return queued[:MAX_QUEUED_ALERTS]


def _version_tuple(value: str) -> tuple[int, int, int]:
    numbers: list[int] = []
    for part in value.lstrip("vV").split(".")[:3]:
        digits = "".join(character for character in part if character.isdigit())
        numbers.append(int(digits) if digits else 0)
    padded = (numbers + [0, 0, 0])[:3]
    return padded[0], padded[1], padded[2]


def detect_dynamic_voice_commands(config: WatchConfig) -> tuple[bool, str, str]:
    if config.stackchan_transport != "http":
        return False, "serial transport cannot query MOD version", ""
    with STACKCHAN_DEVICE_HTTP_LOCK:
        payload = http_json(f"http://{config.stackchan_host}/api/status")
    version = str(payload.get("version") or "")
    mod_name = str(payload.get("mod") or "")
    supported = mod_name == "stackchan_matchday" or (
        mod_name == "stackchan_control" and _version_tuple(version) >= (0, 10, 0)
    )
    return supported, version or "unknown", mod_name


def advertised_setup_url(config: WatchConfig) -> str:
    if config.setup_server.public_base_url:
        return f"{config.setup_server.public_base_url}/setup"
    host = ""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect((config.stackchan_host, 80))
            host = str(probe.getsockname()[0])
    except OSError:
        host = socket.gethostname()
    return f"http://{host}:{config.setup_server.port}/setup"


def scheduled_setup_alert(match: dict[str, Any], setup_url: str, language: str = "zh") -> Alert:
    starts_at = parse_datetime(match.get("starts_at")) or datetime.now(timezone.utc)
    local_start = starts_at.astimezone()
    if language == "en":
        month = (
            "Jan", "Feb", "Mar", "Apr", "May", "Jun",
            "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
        )[local_start.month - 1]
        time_text = f"{month} {local_start.day}, {local_start:%H:%M}"
        speech_time = f"on {month} {local_start.day} at {local_start:%H:%M}"
    else:
        time_text = f"{local_start.month}月{local_start.day}日 {local_start:%H:%M}"
        speech_time = time_text
    label = str(match.get("label") or pick(language, "下一场比赛", "Next match"))
    return Alert(
        ticker=f"ESPN-SCHEDULE:{match.get('event_id', '')}",
        label=label,
        kind="schedule_setup",
        priority=700,
        face="surprise",
        balloon=f"{label} {time_text} | {setup_url}",
        speech=pick(
            language,
            f"下一场，{label}，将在{time_text}开球。你支持哪队，有没有买球？用手机扫码告诉我。",
            f"Next up: {label}, kicking off {speech_time}. Which team do you support, and do you have a position? Scan the code to tell me.",
        ),
        detail=f"pregame setup prompt for ESPN {match.get('event_id', '')}",
        prefer_dynamic_voice=True,
        setup_url=setup_url,
        source_event_at=starts_at,
    )


def _local_match_time_text(starts_at: datetime, language: str) -> str:
    local_start = starts_at.astimezone()
    if language == "en":
        month = (
            "Jan", "Feb", "Mar", "Apr", "May", "Jun",
            "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
        )[local_start.month - 1]
        return f"{month} {local_start.day}, {local_start:%H:%M}"
    return f"{local_start.month}月{local_start.day}日 {local_start:%H:%M}"


def daily_setup_reminder_alert(
    match: dict[str, Any],
    match_count: int,
    setup_url: str,
    language: str = "zh",
) -> Alert:
    starts_at = parse_datetime(match.get("starts_at")) or datetime.now(timezone.utc)
    time_text = _local_match_time_text(starts_at, language)
    label = str(match.get("label") or pick(language, "今天的比赛", "today's match"))
    if language == "en":
        count_text = "a match" if match_count == 1 else f"{match_count} matches"
        speech = (
            f"There is {count_text} today. {label} kicks off at {time_text}, "
            "and nothing is set up yet. Scan the code to pick a match."
        )
    else:
        count_text = "一场比赛" if match_count == 1 else f"{match_count}场比赛"
        speech = f"今天有{count_text}。{label}{time_text}开球，还没设置哦。用手机扫码选一场吧。"
    return Alert(
        ticker=f"DAILY-SETUP:{match.get('event_id', '')}",
        label=label,
        kind="daily_setup",
        priority=650,
        face="happy",
        balloon=f"{label} {time_text} | {setup_url}",
        speech=speech,
        detail=f"daily setup reminder for ESPN {match.get('event_id', '')}",
        prefer_dynamic_voice=True,
        setup_url=setup_url,
        source_event_at=starts_at,
    )


def no_fixture_prompt_alert(setup_url: str, lookahead_days: int, language: str = "zh") -> Alert:
    if language == "en":
        speech = (
            f"No matches are scheduled in the next {lookahead_days} days. "
            "Want to watch something else? Scan the code and paste any Kalshi market link."
        )
        balloon = f"No matches — paste a Kalshi link | {setup_url}"
        label = "No upcoming matches"
    else:
        speech = f"未来{lookahead_days}天没有比赛安排。想看点别的吗？手机扫码，把 Kalshi 盘口链接贴给我就行。"
        balloon = f"没有比赛 · 可贴 Kalshi 链接 | {setup_url}"
        label = "近期没有比赛"
    return Alert(
        ticker="DAILY-DISCOVER",
        label=label,
        kind="daily_discover",
        priority=600,
        face="neutral",
        balloon=balloon,
        speech=speech,
        detail="daily discovery prompt (no upcoming fixtures)",
        prefer_dynamic_voice=True,
        setup_url=setup_url,
    )


def choose_daily_prompt(
    upcoming: list[dict[str, Any]],
    config: WatchConfig,
    setup_url: str,
    now_local: datetime | None = None,
) -> Alert | None:
    """Pick today's proactive prompt, or None when nothing needs asking.

    - Fixtures kick off today and none of them is the configured match:
      remind the user to set one up over the phone page.
    - No fixtures at all within the lookahead window: ask whether to watch
      some other Kalshi market instead (pasted on the phone page).
    - Otherwise (match already configured, or fixtures only on later days):
      stay quiet.
    """
    local_now = now_local or datetime.now().astimezone()
    todays: list[dict[str, Any]] = []
    for match in upcoming:
        starts_at = parse_datetime(match.get("starts_at"))
        if starts_at is None:
            continue
        if starts_at.astimezone(local_now.tzinfo).date() == local_now.date():
            todays.append(match)
    if todays:
        configured_ids = {str(match.get("event_id") or "") for match in todays}
        if config.espn.enabled and config.espn.event_id in configured_ids:
            return None
        return daily_setup_reminder_alert(todays[0], len(todays), setup_url, config.language)
    if not upcoming:
        return no_fixture_prompt_alert(setup_url, config.setup_server.lookahead_days, config.language)
    return None


def setup_confirmation_alert(config: WatchConfig) -> Alert:
    favorite = (
        localized_team_name(config.espn, config.espn.favorite_team)
        if config.espn.favorite_team
        else pick(config.language, "中立", "neutral")
    )
    position = (
        localized_team_name(config.espn, config.espn.position_team)
        if config.espn.position_team
        else pick(config.language, "没有持仓", "no position")
    )
    if config.language == "en":
        favorite_sentence = (
            f"Supporting {favorite}."
            if config.espn.favorite_team
            else "Watching as a neutral."
        )
        position_sentence = (
            f"Position: {position}."
            if config.espn.position_team
            else "No position."
        )
        speech = (
            f"Now watching {config.espn.label}. {favorite_sentence} "
            f"{position_sentence} Monitoring is active."
        )
    else:
        speech = f"已切换到{config.espn.label}。支持{favorite}，{position}，开始盯盘。"
    return Alert(
        ticker=f"SETUP:{config.espn.event_id}",
        label=config.espn.label,
        kind="setup_applied",
        priority=900,
        face="happy",
        balloon=pick(
            config.language,
            f"已切换 | {config.espn.label}",
            f"Now watching | {config.espn.label}",
        ),
        speech=speech,
        detail=f"setup applied ESPN {config.espn.event_id}",
        prefer_dynamic_voice=True,
    )


def post_stackchan_http_command(host: str, command: str) -> None:
    url = f"http://{host}/api/command"
    req = urllib.request.Request(url, data=command.encode("utf-8"), method="POST")
    with STACKCHAN_DEVICE_HTTP_LOCK:
        with urllib.request.urlopen(req, timeout=STACKCHAN_COMMAND_TIMEOUT_SECONDS) as res:
            res.read()


def post_json(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": "stackchan-matchday-watch/0.1",
        },
    )
    with STACKCHAN_DEVICE_HTTP_LOCK:
        with urllib.request.urlopen(request, timeout=STACKCHAN_COMMAND_TIMEOUT_SECONDS) as response:
            raw = response.read().decode("utf-8")
    return json.loads(raw) if raw else {}


def sync_device_match_setup(
    config: WatchConfig,
    options: list[dict[str, Any]],
    current: dict[str, Any],
) -> None:
    if config.stackchan_transport != "http":
        return
    post_json(
        f"http://{config.stackchan_host}/api/match-setup/options",
        {
            "language": str(current.get("language") or config.language),
            "options": options,
            "current": current,
        },
    )


def fetch_device_match_setup_pending(config: WatchConfig) -> dict[str, Any] | None:
    if config.stackchan_transport != "http":
        return None
    with STACKCHAN_DEVICE_HTTP_LOCK:
        payload = http_json(f"http://{config.stackchan_host}/api/match-setup/pending")
    pending = payload.get("pending")
    return pending if isinstance(pending, dict) else None


def acknowledge_device_match_setup(config: WatchConfig, payload: dict[str, Any]) -> None:
    if config.stackchan_transport != "http":
        return
    post_json(f"http://{config.stackchan_host}/api/match-setup/ack", payload)


def post_stackchan_serial_commands(port: str, baud: int, commands: list[str]) -> None:
    if serial is None:
        raise RuntimeError("pyserial is not available")
    with serial.Serial(port=port, baudrate=baud, timeout=0.25, write_timeout=1) as ser:
        try:
            ser.dtr = False
            ser.rts = False
        except (AttributeError, OSError):
            pass
        time.sleep(0.25)
        if ser.in_waiting:
            ser.read(ser.in_waiting)
        for command in commands:
            ser.write((command.rstrip() + "\n").encode("utf-8"))
            ser.flush()
            time.sleep(0.08)


def post_stackchan_command(config: WatchConfig, command: str, dry_run: bool = False) -> None:
    send_stackchan_commands(config, [command], dry_run=dry_run)


def send_stackchan_commands(config: WatchConfig, commands: list[str], dry_run: bool = False) -> None:
    commands = [command.replace("\r", " ").replace("\n", " | ") for command in commands]
    if dry_run:
        for command in commands:
            print(f"dry-run stackchan: {command}")
        return
    if config.stackchan_transport == "serial":
        post_stackchan_serial_commands(config.stackchan_serial_port, config.stackchan_serial_baud, commands)
        return
    for command in commands:
        post_stackchan_http_command(config.stackchan_host, command)


def speak_text(config: WatchConfig, text: str, dry_run: bool = False) -> None:
    if not text or config.voice_transport in {"clip", "none"}:
        return
    if config.voice_transport in {"stackchan", "both"}:
        send_stackchan_commands(config, [f"say {text}"], dry_run=dry_run)
    if config.voice_transport in {"mac", "both"}:
        command = ["/usr/bin/say", "-r", str(config.mac_say_rate)]
        if config.mac_voice:
            command.extend(["-v", config.mac_voice])
        command.append(text)
        if dry_run:
            print("dry-run mac voice: " + " ".join(command))
            return
        subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def wait_for_stackchan_feedback_idle(
    config: WatchConfig,
    timeout: float = 30,
    *,
    include_light: bool = False,
    report_last_error: bool = False,
) -> bool:
    """Wait until motion, TTS, and celebration lighting have all settled."""
    if config.stackchan_transport != "http":
        time.sleep(min(timeout, 6))
        return True
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with STACKCHAN_DEVICE_HTTP_LOCK:
                status = http_json(f"http://{config.stackchan_host}/api/status")
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as error:
            print(f"warning: could not check Stack-chan celebration state: {error}", file=sys.stderr)
            return False
        tts = status.get("tts") or {}
        light = status.get("light") or {}
        light_busy = include_light and light.get("on")
        if not status.get("celebrating") and not tts.get("busy") and not light_busy:
            if report_last_error and status.get("lastError"):
                print(
                    f"warning: Stack-chan asynchronous feedback failed: {status['lastError']}",
                    file=sys.stderr,
                )
            return True
        time.sleep(0.25)
    print(f"warning: Stack-chan feedback still busy after {timeout:g}s", file=sys.stderr)
    return False


def send_alert(config: WatchConfig, alert: Alert, quiet: bool, dry_run: bool, no_say: bool) -> bool:
    timeout_ms = config.alert_balloon_seconds * 1000
    commands = [f"face {alert.face}"]
    if alert.setup_url and config.setup_qr_commands:
        commands.append(f"setup show {alert.setup_url}")
    else:
        commands.append(f"balloon temp {timeout_ms} {alert.balloon}")
    should_play_voice = not quiet and not no_say
    use_dynamic_voice = bool(
        alert.prefer_dynamic_voice
        and alert.speech
        and should_play_voice
        and config.voice_transport == "clip"
    )
    use_plain_say = use_dynamic_voice and (
        not config.dynamic_voice_commands or not alert.clip_id
    )
    use_goal_celebration = bool(
        alert.celebration == "goal"
        and alert.clip_id == "favorite-goal"
        and should_play_voice
        and config.voice_transport == "clip"
        and not use_plain_say
    )
    use_result_celebration = bool(
        alert.celebration in {"result-win", "result-lose"}
        and alert.clip_id in {"favorite-win", "favorite-lose"}
        and should_play_voice
        and config.voice_transport == "clip"
        and config.result_celebration_commands
    )
    if use_goal_celebration:
        red, green, blue = alert.light_rgb or (255, 255, 255)
        if use_dynamic_voice:
            commands.append(f"celebrate say {red} {green} {blue} {alert.speech}")
        else:
            commands.append(f"celebrate goal {red} {green} {blue}")
    elif use_result_celebration:
        red, green, blue = alert.light_rgb or (255, 255, 255)
        outcome = alert.celebration.removeprefix("result-")
        speech = f" {alert.speech}" if alert.speech and config.result_speech_commands else ""
        commands.append(f"celebrate result {outcome} {red} {green} {blue}{speech}")
    elif alert.light_rgb:
        red, green, blue = alert.light_rgb
        commands.append(f"light flash {red} {green} {blue} 1800 110")
    if (
        not use_goal_celebration
        and not use_result_celebration
        and should_play_voice
        and config.voice_transport == "clip"
        and (alert.clip_id or use_dynamic_voice)
    ):
        if use_plain_say:
            commands.append(f"say {alert.speech}")
        elif use_dynamic_voice:
            commands.append(f"voice {alert.clip_id} {alert.speech}")
        else:
            commands.append(f"clip {alert.clip_id}")

    uses_celebration = use_goal_celebration or use_result_celebration
    if uses_celebration and not dry_run and config.stackchan_transport == "http":
        if not wait_for_stackchan_feedback_idle(config):
            return False
    try:
        send_stackchan_commands(config, commands, dry_run=dry_run)
    except (urllib.error.URLError, OSError, RuntimeError) as error:
        print(f"warning: stackchan commands failed: {error}", file=sys.stderr)
        return False
    if uses_celebration and not dry_run:
        # The MOD acknowledges celebration commands before their asynchronous
        # motion/TTS finishes; serialize subsequent alerts against real state.
        wait_for_stackchan_feedback_idle(
            config,
            include_light=True,
            report_last_error=True,
        )
    if alert.speech and should_play_voice:
        speak_text(config, alert.speech, dry_run=dry_run)
    return True


def ticker_text(config: WatchConfig, snapshots: dict[str, MarketSnapshot]) -> str:
    displayed: list[tuple[MarketConfig, MarketSnapshot]] = []
    for market in config.markets:
        snapshot = snapshots.get(market.ticker)
        if market.show_in_ticker and snapshot:
            displayed.append((market, snapshot))
    if not displayed:
        return ""
    if len(displayed) == 1:
        market, snapshot = displayed[0]
        return (
            f"{market.label}  YES {fmt_price(snapshot.mid('yes'))}%  "
            f"NO {fmt_price(snapshot.mid('no'))}%"
        )
    return " | ".join(
        f"{market.label} Y{fmt_price(snapshot.mid('yes'))} N{fmt_price(snapshot.mid('no'))}"
        for market, snapshot in displayed[:2]
    )


def probability_bar_command(config: WatchConfig, snapshots: dict[str, MarketSnapshot]) -> str:
    bar = config.probability_bar
    if not bar.enabled:
        return ""
    snapshot = snapshots.get(bar.market_ticker)
    if snapshot is None:
        return ""
    if bar.mode == "normalized_outcomes":
        right_snapshot = snapshots.get(bar.right_market_ticker)
        left_midpoint = snapshot.implied_probability("yes")
        right_midpoint = (
            right_snapshot.implied_probability("yes") if right_snapshot else None
        )
        if left_midpoint is None or right_midpoint is None:
            return ""
        total = left_midpoint + right_midpoint
        if total <= 0:
            return ""
        left_percent = int(math.floor((left_midpoint * 100 / total) + 0.5))
    else:
        midpoint = snapshot.implied_probability(bar.side)
        if midpoint is None:
            return ""
        left_percent = midpoint
    left_percent = max(0, min(100, left_percent))
    right_percent = 100 - left_percent
    return " ".join(
        [
            "pkbar",
            bar.left_flag,
            str(left_percent),
            command_hex_color(bar.left_color),
            bar.right_flag,
            str(right_percent),
            command_hex_color(bar.right_color),
        ]
    )


def persistent_display_command(config: WatchConfig, snapshots: dict[str, MarketSnapshot]) -> str:
    probability_command = probability_bar_command(config, snapshots)
    if probability_command:
        return probability_command
    if not config.ticker_enabled:
        return ""
    message = ticker_text(config, snapshots)
    return f"ticker {message}" if message else ""


def send_ticker(config: WatchConfig, snapshots: dict[str, MarketSnapshot], dry_run: bool) -> str:
    command = persistent_display_command(config, snapshots)
    if not command:
        return ""
    send_stackchan_commands(config, [command], dry_run=dry_run)
    return command


def send_summary(
    config: WatchConfig,
    snapshots: dict[str, MarketSnapshot],
    dry_run: bool,
    no_say: bool,
    quiet: bool = False,
) -> None:
    items = [
        pick(
            config.language,
            f"{market.label} YES {fmt_price(snapshots[market.ticker].mid('yes'))}分",
            (
                f"{market.label} YES "
                f"{english_quantity(fmt_price(snapshots[market.ticker].mid('yes')), 'cent')}"
            ),
        )
        for market in config.markets[:3]
        if market.ticker in snapshots
    ]
    speech = join_sentences(config.language, *items)
    try:
        send_ticker(config, snapshots, dry_run=dry_run)
        if not no_say and not quiet and speech:
            speak_text(config, speech, dry_run=dry_run)
    except (urllib.error.URLError, OSError, RuntimeError) as error:
        print(f"warning: stackchan summary failed: {error}", file=sys.stderr)


def print_snapshot_table(config: WatchConfig, snapshots: dict[str, MarketSnapshot], missing: list[str]) -> None:
    for market in config.markets:
        snapshot = snapshots.get(market.ticker)
        if snapshot is None:
            print(f"{market.ticker}\tmissing")
            continue
        mid = snapshot.mid(market.side_i_care)
        print(
            "\t".join(
                [
                    snapshot.ticker,
                    snapshot.label,
                    f"status={snapshot.status}",
                    f"YES={fmt_price(snapshot.yes_bid_cents)}/{fmt_price(snapshot.yes_ask_cents)}",
                    f"NO={fmt_price(snapshot.no_bid_cents)}/{fmt_price(snapshot.no_ask_cents)}",
                    f"{side_text(market.side_i_care)}_mid={fmt_price(mid)}",
                    f"vol24={snapshot.volume_24h or '-'}",
                ]
            )
        )
    if missing:
        print(f"warning: missing tickers: {', '.join(missing)}", file=sys.stderr)


def run_once(args: argparse.Namespace) -> int:
    config = load_config(Path(args.config), args.language or None)
    validate_config(config, dry_run=args.dry_run)
    snapshots, missing = fetch_markets(config)
    print_snapshot_table(config, snapshots, missing)
    if snapshots:
        send_summary(config, snapshots, dry_run=args.dry_run, no_say=args.no_say)
    return 0 if snapshots else 1


def run_watch(args: argparse.Namespace) -> int:
    config_path = Path(args.config)
    config = load_config(config_path, args.language or None)
    validate_config(config, dry_run=args.dry_run)
    if not args.dry_run and config.voice_transport == "clip":
        try:
            config.dynamic_voice_commands, mod_version, mod_name = detect_dynamic_voice_commands(config)
            config.result_celebration_commands = _version_tuple(mod_version) >= (0, 11, 0)
            config.result_speech_commands = (
                mod_name == "stackchan_matchday" and _version_tuple(mod_version) >= (1, 1, 0)
            )
            config.setup_qr_commands = _version_tuple(mod_version) >= (0, 12, 0)
            voice_mode = "dynamic-with-local-fallback" if config.dynamic_voice_commands else "legacy-say"
            result_mode = "dance" if config.result_celebration_commands else "light-and-clip"
            setup_mode = "qr" if config.setup_qr_commands else "balloon-url"
            print(
                f"Stack-chan MOD {mod_version}; player voice mode={voice_mode}; "
                f"result mode={result_mode}; setup mode={setup_mode}",
                flush=True,
            )
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
            config.dynamic_voice_commands = False
            config.result_celebration_commands = False
            config.result_speech_commands = False
            config.setup_qr_commands = False
            print("warning: could not detect Stack-chan MOD; player voice mode=legacy-say", file=sys.stderr)

    setup_service: MatchSetupService | None = None
    setup_http_server = None
    setup_thread = None
    setup_url = ""
    if config.setup_server.enabled:
        local_setup_url = advertised_setup_url(config)
        setup_url = f"http://{config.stackchan_host}/setup"
        setup_service = MatchSetupService(
            config_path=config_path,
            kalshi_base_url=config.kalshi_base_url,
            espn_base_url=config.espn.base_url,
            league=config.espn.league,
            kalshi_series_ticker=config.setup_server.kalshi_series_ticker,
            lookahead_days=config.setup_server.lookahead_days,
            cache_seconds=min(300, config.setup_server.refresh_seconds),
            language=config.language,
        )
        setup_service.setup_url = setup_url
        try:
            setup_http_server, setup_thread = start_setup_server(
                setup_service,
                config.setup_server.host,
                config.setup_server.port,
            )
            print(
                f"match setup: device={setup_url}; local-admin={local_setup_url}",
                flush=True,
            )
        except OSError as error:
            print(f"warning: match setup server failed: {error}", file=sys.stderr)
            setup_service = None
    states = {market.ticker: MarketState() for market in config.markets}
    espn_state = ESPNState()
    latest_match: MatchSnapshot | None = None
    kalshi_failures = 0
    espn_failures = 0
    startup_summary_sent = False
    last_display_command = ""
    last_display_sent_at = 0.0
    alert_queue: list[QueuedAlert] = []
    snapshots: dict[str, MarketSnapshot] = {}
    next_kalshi_poll_at = 0.0
    next_espn_poll_at = 0.0
    next_schedule_refresh_at = 0.0
    next_setup_pending_poll_at = 0.0
    last_poll_tier = ""
    prompted_schedule_events: set[str] = set()
    daily_prompt_done_for = setup_service.last_daily_prompt() if setup_service else ""
    setup_acknowledgements: dict[str, dict[str, Any]] = {}
    delivery_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="stackchan-delivery")
    delivery_future: Future[bool] | None = None
    delivery_item: QueuedAlert | None = None
    espn_description = f"; ESPN event={config.espn.event_id}" if config.espn.enabled else ""
    print(
        f"watching {len(config.markets)} Kalshi markets; Kalshi poll={config.poll_seconds}s"
        f"; ESPN poll={config.espn.poll_seconds}s{espn_description}",
        flush=True,
    )
    try:
        while True:
            now = datetime.now(timezone.utc)
            cycle_monotonic = time.monotonic()
            quiet = in_quiet_hours(config.quiet_hours)

            if setup_service and setup_service.take_reload_requested():
                previous_dynamic_voice = config.dynamic_voice_commands
                previous_result_commands = config.result_celebration_commands
                previous_result_speech_commands = config.result_speech_commands
                previous_setup_commands = config.setup_qr_commands
                config = load_config(config_path)
                validate_config(config, dry_run=args.dry_run)
                config.dynamic_voice_commands = previous_dynamic_voice
                config.result_celebration_commands = previous_result_commands
                config.result_speech_commands = previous_result_speech_commands
                config.setup_qr_commands = previous_setup_commands
                setup_service.language = config.language
                states = {market.ticker: MarketState() for market in config.markets}
                espn_state = ESPNState()
                latest_match = None
                kalshi_failures = 0
                espn_failures = 0
                startup_summary_sent = False
                last_display_command = ""
                last_display_sent_at = 0.0
                alert_queue = []
                snapshots = {}
                next_kalshi_poll_at = 0.0
                next_espn_poll_at = 0.0
                next_schedule_refresh_at = 0.0
                next_setup_pending_poll_at = 0.0
                last_poll_tier = ""
                quiet = in_quiet_hours(config.quiet_hours)
                try:
                    send_stackchan_commands(config, ["setup hide"], dry_run=args.dry_run)
                except (urllib.error.URLError, OSError, RuntimeError):
                    pass
                alert_queue = merge_alert_queue(
                    alert_queue,
                    [(setup_confirmation_alert(config), None, None, None)],
                    cycle_monotonic,
                )
                print(
                    f"match setup applied: ESPN {config.espn.event_id}; "
                    f"markets={','.join(market.ticker for market in config.markets)}",
                    flush=True,
                )

            poll_plan = adaptive_polling_plan(config, latest_match, now)
            if poll_plan.tier != last_poll_tier:
                print(
                    f"polling tier={poll_plan.tier}; Kalshi={poll_plan.kalshi_seconds}s; "
                    f"ESPN={poll_plan.espn_seconds}s",
                    flush=True,
                )
                last_poll_tier = poll_plan.tier

            if delivery_future is not None and delivery_future.done():
                assert delivery_item is not None
                try:
                    delivered = delivery_future.result()
                except Exception as error:
                    delivered = False
                    print(f"warning: delivery worker failed: {error}", file=sys.stderr)
                if delivered:
                    if (
                        delivery_item.snapshot is not None
                        and delivery_item.market is not None
                        and delivery_item.state is not None
                    ):
                        mark_alert_sent(
                            delivery_item.alert,
                            delivery_item.snapshot,
                            delivery_item.market,
                            delivery_item.state,
                            now,
                        )
                else:
                    alert_queue.append(delivery_item)
                    print(f"delivery retry queued: {delivery_item.alert.kind}", flush=True)
                delivery_future = None
                delivery_item = None

            pending: list[PendingAlertContext] = []

            if setup_service and cycle_monotonic >= next_setup_pending_poll_at:
                next_setup_pending_poll_at = cycle_monotonic + MATCH_SETUP_PENDING_POLL_SECONDS
                try:
                    setup_request = fetch_device_match_setup_pending(config)
                    if setup_request:
                        request_id = str(setup_request.get("request_id") or "")
                        acknowledgement = setup_acknowledgements.get(request_id)
                        if acknowledgement is None:
                            try:
                                standalone_request = bool(setup_request.get("standalone")) or (
                                    bool(setup_request.get("kalshi_url"))
                                    and not setup_request.get("espn_event_id")
                                )
                                if standalone_request:
                                    result = setup_service.apply_market_selection(setup_request)
                                else:
                                    result = setup_service.apply_selection(setup_request)
                                acknowledgement = {
                                    "request_id": request_id,
                                    "ok": True,
                                    "label": result["label"],
                                    "language": result["language"],
                                }
                            except (OSError, ValueError, json.JSONDecodeError, urllib.error.URLError) as error:
                                acknowledgement = {
                                    "request_id": request_id,
                                    "ok": False,
                                    "error": str(error),
                                }
                            setup_acknowledgements[request_id] = acknowledgement
                        acknowledge_device_match_setup(config, acknowledgement)
                        setup_acknowledgements.pop(request_id, None)
                except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
                    pass

            if setup_service and cycle_monotonic >= next_schedule_refresh_at:
                try:
                    upcoming = setup_service.setup_options(force=True)
                    next_schedule_refresh_at = (
                        cycle_monotonic + config.setup_server.refresh_seconds
                    )
                    try:
                        sync_device_match_setup(
                            config,
                            upcoming,
                            setup_service.current_status(),
                        )
                    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as error:
                        next_schedule_refresh_at = cycle_monotonic + 10
                        print(
                            f"warning: device match setup sync failed ({error}); retry in 10s",
                            file=sys.stderr,
                        )
                    for match in upcoming:
                        event_id = str(match.get("event_id") or "")
                        starts_at = parse_datetime(match.get("starts_at"))
                        if not event_id or starts_at is None:
                            continue
                        seconds_until_start = (starts_at - now).total_seconds()
                        if seconds_until_start <= 0:
                            continue
                        if seconds_until_start > config.setup_server.prompt_minutes_before * 60:
                            break
                        if event_id == config.espn.event_id or event_id in prompted_schedule_events:
                            continue
                        pending.append(
                            (
                                scheduled_setup_alert(
                                    match,
                                    f"http://{config.stackchan_host}/setup",
                                    config.language,
                                ),
                                None,
                                None,
                                None,
                            )
                        )
                        prompted_schedule_events.add(event_id)
                        break
                    if (
                        config.setup_server.daily_prompt_hour >= 0
                        and not in_quiet_hours(config.quiet_hours)
                    ):
                        local_now = datetime.now().astimezone()
                        today = local_now.strftime("%Y-%m-%d")
                        if (
                            local_now.hour >= config.setup_server.daily_prompt_hour
                            and daily_prompt_done_for != today
                        ):
                            # One decision per local day, prompted or not, so a
                            # configured match day stays quiet all day.
                            daily_prompt_done_for = today
                            try:
                                setup_service.record_daily_prompt(today)
                            except (OSError, json.JSONDecodeError):
                                pass
                            prompt_alert = choose_daily_prompt(
                                upcoming,
                                config,
                                f"http://{config.stackchan_host}/setup",
                                local_now,
                            )
                            if prompt_alert is not None:
                                pending.append((prompt_alert, None, None, None))
                except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError, ValueError) as error:
                    next_schedule_refresh_at = cycle_monotonic + 120
                    print(f"warning: ESPN schedule refresh failed: {error}", file=sys.stderr)

            if cycle_monotonic >= next_kalshi_poll_at:
                poll_started_at = cycle_monotonic
                try:
                    snapshots, missing = fetch_markets(config)
                    kalshi_failures = 0
                    next_kalshi_poll_at = max(
                        poll_started_at + poll_plan.kalshi_seconds,
                        time.monotonic() + 0.1,
                    )
                    if missing:
                        print(f"warning: missing tickers: {', '.join(missing)}", file=sys.stderr)
                    for market in config.markets:
                        snapshot = snapshots.get(market.ticker)
                        if snapshot is None or not market.alerts_enabled:
                            continue
                        state = states[market.ticker]
                        alerts = evaluate_market(snapshot, market, state, now)
                        for alert in alerts:
                            pending.append((alert, snapshot, market, state))
                except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as error:
                    kalshi_failures += 1
                    retry_cap = 300 if poll_plan.tier in {"full-speed", "live"} else 900
                    retry_seconds = min(
                        retry_cap,
                        poll_plan.kalshi_seconds * (2 ** min(5, kalshi_failures)),
                    )
                    next_kalshi_poll_at = time.monotonic() + retry_seconds
                    print(
                        f"warning: Kalshi fetch failed ({error}); retry in {retry_seconds}s",
                        file=sys.stderr,
                    )

            if config.espn.enabled and cycle_monotonic >= next_espn_poll_at:
                poll_started_at = cycle_monotonic
                espn_state.last_polled_at = cycle_monotonic
                try:
                    match = fetch_espn_match(config.espn)
                    previous_poll_tier = poll_plan.tier
                    latest_match = match
                    poll_plan = adaptive_polling_plan(config, latest_match, now)
                    if poll_plan.tier != previous_poll_tier:
                        next_kalshi_poll_at = cycle_monotonic + poll_plan.kalshi_seconds
                    espn_failures = 0
                    next_espn_poll_at = max(
                        poll_started_at + poll_plan.espn_seconds,
                        time.monotonic() + 0.1,
                    )
                    espn_alerts = evaluate_espn_match(match, config.espn, espn_state)
                    pending.extend((alert, None, None, None) for alert in espn_alerts)
                except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError, ValueError) as error:
                    espn_failures += 1
                    retry_cap = 30 if poll_plan.tier in {"full-speed", "live"} else 900
                    retry_seconds = min(
                        retry_cap,
                        poll_plan.espn_seconds * (2 ** min(4, espn_failures)),
                    )
                    next_espn_poll_at = time.monotonic() + retry_seconds
                    print(
                        f"warning: ESPN fetch failed ({error}); retry in {retry_seconds}s",
                        file=sys.stderr,
                    )

            alert_queue = merge_alert_queue(alert_queue, pending, cycle_monotonic)

            current_display_command = persistent_display_command(config, snapshots)
            display_stale = (
                cycle_monotonic - last_display_sent_at >= config.display_refresh_seconds
            )
            if (
                delivery_future is None
                and current_display_command
                and (current_display_command != last_display_command or display_stale)
            ):
                try:
                    last_display_command = send_ticker(config, snapshots, dry_run=args.dry_run)
                    last_display_sent_at = cycle_monotonic
                except (urllib.error.URLError, OSError, RuntimeError) as error:
                    print(f"warning: stackchan display failed: {error}", file=sys.stderr)

            if config.startup_summary_on_watch and not startup_summary_sent and snapshots:
                print("startup summary", flush=True)
                if config.speak_startup_summary:
                    send_summary(
                        config,
                        snapshots,
                        dry_run=args.dry_run,
                        no_say=args.no_say,
                        quiet=quiet,
                    )
                startup_summary_sent = True

            if delivery_future is None and alert_queue:
                delivery_item = alert_queue.pop(0)
                alert = delivery_item.alert
                detected_at = datetime.now(timezone.utc)
                source_age = ""
                if alert.source_event_at is not None:
                    age_seconds = max(0.0, (detected_at - alert.source_event_at).total_seconds())
                    source_age = f" source_age={age_seconds:.1f}s"
                print(
                    f"{detected_at.isoformat()} alert {alert.kind}: {alert.ticker}:{source_age} "
                    f"{alert.detail}",
                    flush=True,
                )
                delivery_future = delivery_executor.submit(
                    send_alert,
                    config,
                    alert,
                    quiet,
                    args.dry_run,
                    args.no_say,
                )

            time.sleep(0.2)
    except KeyboardInterrupt:
        print("\nwatch stopped")
        if args.clear_balloon_on_exit:
            try:
                post_stackchan_command(config, "balloon off", dry_run=args.dry_run)
            except (urllib.error.URLError, OSError, RuntimeError) as error:
                print(f"warning: could not clear balloon: {error}", file=sys.stderr)
        if config.probability_bar.enabled or config.ticker_enabled:
            try:
                command = "pkbar off" if config.probability_bar.enabled else "ticker off"
                post_stackchan_command(config, command, dry_run=args.dry_run)
            except (urllib.error.URLError, OSError, RuntimeError) as error:
                print(f"warning: could not clear persistent display: {error}", file=sys.stderr)
        return 0
    finally:
        delivery_executor.shutdown(wait=False, cancel_futures=True)
        if setup_http_server is not None:
            setup_http_server.shutdown()
            setup_http_server.server_close()
        if setup_thread is not None:
            setup_thread.join(timeout=1)


def discover_events(args: argparse.Namespace) -> int:
    base_url = args.base_url.rstrip("/")
    search_terms = [args.query or "", args.sport or "", args.competition or ""]
    needle = " ".join(term for term in search_terms if term).strip().lower()
    if not needle:
        print("error: discover needs --query, --sport, or --competition", file=sys.stderr)
        return 2

    cursor = ""
    printed = 0
    for _page in range(args.max_pages):
        query = {
            "status": "open",
            "limit": "200",
            "with_nested_markets": "true",
            "with_milestones": "true",
        }
        if cursor:
            query["cursor"] = cursor
        url = f"{base_url}/events?{urllib.parse.urlencode(query)}"
        payload = http_json(url)
        for event in payload.get("events", []):
            event_blob = json.dumps(event, ensure_ascii=False).lower()
            if not all(term.lower() in event_blob for term in search_terms if term):
                if needle not in event_blob:
                    continue
            title = event.get("title") or event.get("sub_title") or event.get("event_ticker")
            for market in event.get("markets", [])[: args.markets_per_event]:
                print(
                    "\t".join(
                        [
                            str(market.get("ticker", "")),
                            str(title),
                            str(market.get("yes_sub_title") or market.get("title") or ""),
                            f"YES={fmt_price(dollars_to_cents(market.get('yes_bid_dollars')))}/"
                            f"{fmt_price(dollars_to_cents(market.get('yes_ask_dollars')))}",
                            f"NO={fmt_price(dollars_to_cents(market.get('no_bid_dollars')))}/"
                            f"{fmt_price(dollars_to_cents(market.get('no_ask_dollars')))}",
                            f"close={market.get('close_time', '')}",
                            f"vol24={market.get('volume_24h_fp', '')}",
                        ]
                    )
                )
                printed += 1
        cursor = str(payload.get("cursor") or "")
        if not cursor:
            break

    if printed == 0:
        print("no matching open events found")
        return 1
    return 0


def websocket_placeholder(_args: argparse.Namespace) -> int:
    print(
        "WebSocket mode is intentionally not implemented yet.\n"
        "When you add API keys, implement RSA signing locally using:\n"
        "  KALSHI_ACCESS_KEY\n"
        "  KALSHI_PRIVATE_KEY_PATH\n"
        "Do not put these values in firmware or watchlist JSON.",
        file=sys.stderr,
    )
    return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Kalshi YES/NO market watcher for Stack-chan.")
    parser.add_argument("--config", default="config/kalshi_watchlist.json")
    parser.add_argument(
        "--language",
        default="",
        help="Override config language (zh or en; aliases such as zh-CN/en-US are accepted)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print Stack-chan commands instead of sending them")
    parser.add_argument("--no-say", action="store_true", help="Update face/balloon but do not speak")
    parser.add_argument("--once", action="store_true", help="Fetch one snapshot and announce a summary")
    parser.add_argument("--watch", action="store_true", help="Run continuously and alert on changes")
    parser.add_argument("--clear-balloon-on-exit", action="store_true")

    subparsers = parser.add_subparsers(dest="command")
    discover = subparsers.add_parser("discover", help="Search open Kalshi events for candidate tickers")
    discover.add_argument("--query", default="")
    discover.add_argument("--sport", default="")
    discover.add_argument("--competition", default="")
    discover.add_argument("--base-url", default=DEFAULT_BASE_URL)
    discover.add_argument("--max-pages", type=int, default=5)
    discover.add_argument("--markets-per-event", type=int, default=8)

    subparsers.add_parser("websocket", help="Placeholder for future API-key WebSocket mode")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        if args.command == "discover":
            return discover_events(args)
        if args.command == "websocket":
            return websocket_placeholder(args)
        if args.watch:
            return run_watch(args)
        if args.once:
            return run_once(args)
        parser.error("choose --once, --watch, discover, or websocket")
    except ConfigError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as error:
        print(f"error: request failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
