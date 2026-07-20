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
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field, replace
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
    from stackchan_player_catalog import (
        PlayerCatalog,
        PlayerCatalogError,
        ResolvedPlayerProfile,
        load_default_player_catalog,
        resolve_player_profile,
    )
    from stackchan_venues import (
        POLYMARKET_BASE_URL,
        KalshiVenueAdapter,
        PolymarketMarketRef,
        PolymarketVenueAdapter,
        VenueDivergence,
        VenueQuote,
        aggregate_probability,
        max_divergence,
        same_direction_jump,
    )
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
    from stackchan_player_catalog import (
        PlayerCatalog,
        PlayerCatalogError,
        ResolvedPlayerProfile,
        load_default_player_catalog,
        resolve_player_profile,
    )
    from stackchan_venues import (
        POLYMARKET_BASE_URL,
        KalshiVenueAdapter,
        PolymarketMarketRef,
        PolymarketVenueAdapter,
        VenueDivergence,
        VenueQuote,
        aggregate_probability,
        max_divergence,
        same_direction_jump,
    )


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
DEFAULT_STARTUP_CRITICAL_REPLAY_SECONDS = 0
DEFAULT_DISPLAY_REFRESH_SECONDS = 30
DEFAULT_SETUP_PORT = 8788
DEFAULT_COMMENTARY_STYLE = "balanced"
COMMENTARY_STYLES = frozenset({"casual", "balanced", "professional"})
REQUEST_TIMEOUT_SECONDS = 12
STACKCHAN_COMMAND_TIMEOUT_SECONDS = 20
STACKCHAN_SPEECH_TIMEOUT_SECONDS = 45
STACKCHAN_FEEDBACK_POLL_SECONDS = 1.0
# Every poll is a fresh TCP connection to the device. Connection churn is the
# trigger for a use-after-free race in the Moddable lwIP socket glue
# (tcpReceive on the tcpip thread vs. close on the XS thread), so poll no
# faster than the setup-page UX truly needs.
MATCH_SETUP_PENDING_POLL_SECONDS = 5.0
MAX_QUEUED_ALERTS = 24
# A failed delivery must never retry immediately: rapid resends pile balloon
# redraws and audio setups onto the device (the tone fallback of each retry
# collides with the still-streaming TTS of the first attempt and fails again),
# which has crashed CoreS3 mid-match. Back off instead, and give up on
# ordinary commentary after a couple of attempts.
ALERT_RETRY_BACKOFF_SECONDS = 10.0
DEFAULT_ALERT_RETRY_LIMIT = 2
CRITICAL_ALERT_RETRY_LIMITS = {
    "espn_goal": 5,
    "espn_penalty": 5,
    "espn_red_card": 5,
    "market_goal_signal": 3,
    "schedule_setup": 3,
}
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
    goal_signal_up_team: str = ""
    goal_signal_down_team: str = ""
    language: str = "zh"
    commentary_style: str = DEFAULT_COMMENTARY_STYLE
    favorite_team: str = ""
    position_team: str = ""
    tracks_position: bool = False


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
    # Optional Polymarket pairing for the same two outcomes (standalone mode,
    # PRD P2): one Gamma market whose outcome labels map onto the bar's sides.
    polymarket_market_id: str = ""
    polymarket_left_outcome: str = ""
    polymarket_right_outcome: str = ""


@dataclass
class PolymarketConfig:
    enabled: bool = True
    base_url: str = POLYMARKET_BASE_URL
    poll_seconds: int = 30


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
    player_catalog: PlayerCatalog = field(
        default_factory=load_default_player_catalog,
        repr=False,
        compare=False,
    )
    language: str = "zh"
    commentary_style: str = DEFAULT_COMMENTARY_STYLE


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
    spoiler_free_mode: bool = False
    quiet_hours: QuietHours = field(default_factory=QuietHours)
    probability_bar: ProbabilityBarConfig = field(default_factory=ProbabilityBarConfig)
    polymarket: PolymarketConfig = field(default_factory=PolymarketConfig)
    setup_server: SetupServerConfig = field(default_factory=SetupServerConfig)
    adaptive_polling: AdaptivePollingConfig = field(default_factory=AdaptivePollingConfig)
    espn: ESPNConfig = field(default_factory=ESPNConfig)
    markets: list[MarketConfig] = field(default_factory=list)
    # P3: watch a confirmed pairing-registry entry instead of hand-written
    # markets. Empty string keeps the legacy hand-configured path.
    active_canonical_event: str = ""
    pairing_registry_path: str = ""
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
    liquidity_usd: float | None = None

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
    spoiler_sensitive: bool = False


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


@dataclass(frozen=True)
class ESPNEventFacts:
    """Language-neutral facts parsed from one ESPN commentary item.

    The raw ESPN sentence is kept only as alert diagnostics.  User-facing
    commentary is rendered from these fields so unsupported or ambiguous
    English details never leak into Chinese speech.
    """

    play_type: str
    event_type: str
    clock: str
    team: MatchTeam | None
    team_name: str
    primary_player: MatchPlayer | None
    primary_player_name: str
    participants: tuple[MatchPlayer, ...]
    assistant: MatchPlayer | None
    assistant_name: str
    goalkeeper: MatchPlayer | None
    goalkeeper_name: str
    incoming: MatchPlayer | None
    incoming_name: str
    outgoing: MatchPlayer | None
    outgoing_name: str
    beneficiary: MatchPlayer | None
    beneficiary_name: str
    awarded_team: MatchTeam | None
    awarded_team_name: str
    result: str
    score_text: str
    compact_score_text: str
    score_speech: str
    shot_body_part: str = ""
    shot_area: str = ""
    shot_direction: str = ""
    shot_technique: str = ""
    delivery: str = ""
    set_piece_location: str = ""
    injury_reason: str = ""
    status_code: str = ""
    close_miss: bool = False
    is_equalizer: bool = False


@dataclass(frozen=True)
class EventPerspective:
    """How one match event lands for the fan and the configured position.

    The values intentionally describe impact instead of wording.  Voice styles
    can therefore sound different without quietly changing which side of an
    event the user is on.
    """

    support_outcome: str = "neutral"
    position_outcome: str = "none"
    alignment: str = "neutral"


@dataclass
class ESPNState:
    initialized: bool = False
    seen_commentary: set[str] = field(default_factory=set)
    last_status_state: str = ""
    last_polled_at: float = 0
    final_result_announced: bool = False
    player_coverage_signature: tuple[str, ...] = field(default_factory=tuple)


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
    retries: int = 0
    not_before: float = 0.0


class ConfigError(ValueError):
    pass


def normalize_commentary_style(
    value: Any,
    *,
    path: str = "espn.commentary_style",
) -> str:
    style = str(DEFAULT_COMMENTARY_STYLE if value is None else value).strip().lower()
    if style not in COMMENTARY_STYLES:
        choices = ", ".join(sorted(COMMENTARY_STYLES))
        raise ConfigError(f"{path} must be one of: {choices}")
    return style


def apply_live_commentary_style(config: WatchConfig, value: Any) -> str:
    """Update only the global rendering preference on the live config object."""

    style = normalize_commentary_style(value)
    config.espn.commentary_style = style
    for market in config.markets:
        market.commentary_style = style
    return style


def normalize_spoiler_free_mode(value: Any) -> bool:
    if not isinstance(value, bool):
        raise ConfigError("spoiler_free_mode must be a boolean")
    return value


def apply_live_spoiler_free_mode(config: WatchConfig, value: Any) -> bool:
    """Hot-apply only the anti-spoiler preference on the live config object."""

    enabled = normalize_spoiler_free_mode(value)
    config.spoiler_free_mode = enabled
    return enabled


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


def _goal_signal_team_fact(
    explicit: str,
    speech: str,
    team_names: dict[str, str],
) -> str:
    """Migrate old configs by exact configured-name matching, never guessing."""

    if explicit:
        return explicit
    unique_names = {name.casefold(): name for name in team_names.values() if name}
    speech_key = speech.casefold()
    matches = [name for key, name in unique_names.items() if key in speech_key]
    return matches[0] if len(matches) == 1 else ""


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
    bar_poly_raw = bar_raw.get("polymarket") or {}
    if not isinstance(bar_poly_raw, dict):
        raise ConfigError("probability_bar.polymarket must be an object")
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
        polymarket_market_id=str(bar_poly_raw.get("market_id", "")).strip(),
        polymarket_left_outcome=str(bar_poly_raw.get("left_outcome", "")).strip(),
        polymarket_right_outcome=str(bar_poly_raw.get("right_outcome", "")).strip(),
    )

    polymarket_raw = raw.get("polymarket") or {}
    if not isinstance(polymarket_raw, dict):
        raise ConfigError("polymarket must be an object")
    polymarket = PolymarketConfig(
        enabled=bool(polymarket_raw.get("enabled", True)),
        base_url=str(polymarket_raw.get("base_url", POLYMARKET_BASE_URL)).strip().rstrip("/"),
        # Gamma allows ~60 req/min shared across everything we do; one quote
        # request each 15s+ keeps plenty of headroom for discovery scans.
        poll_seconds=max(15, int(polymarket_raw.get("poll_seconds", 30))),
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
    if not isinstance(espn_raw, dict):
        raise ConfigError("espn must be an object")
    commentary_style = normalize_commentary_style(
        espn_raw.get("commentary_style", DEFAULT_COMMENTARY_STYLE)
    )
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
    try:
        player_catalog = load_default_player_catalog()
    except PlayerCatalogError as error:
        raise ConfigError(f"cannot load ESPN player catalog: {error}") from error
    espn = ESPNConfig(
        language=language,
        commentary_style=commentary_style,
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
        player_catalog=player_catalog,
    )

    markets: list[MarketConfig] = []
    for idx, item in enumerate(raw.get("markets", []), start=1):
        ticker = str(item.get("ticker", "")).strip().upper()
        if not ticker:
            raise ConfigError(f"markets[{idx}] is missing ticker")
        side = str(item.get("side_i_care", "yes")).strip().lower()
        if side not in {"yes", "no"}:
            raise ConfigError(f"{ticker}: side_i_care must be yes or no")
        goal_signal_up_speech = _config_text(
            item.get("goal_signal_up_speech"),
            language,
            path=f"markets[{idx}].goal_signal_up_speech",
        )
        goal_signal_down_speech = _config_text(
            item.get("goal_signal_down_speech"),
            language,
            path=f"markets[{idx}].goal_signal_down_speech",
        )
        goal_signal_up_team = _config_text(
            item.get("goal_signal_up_team"),
            language,
            path=f"markets[{idx}].goal_signal_up_team",
        )
        goal_signal_down_team = _config_text(
            item.get("goal_signal_down_team"),
            language,
            path=f"markets[{idx}].goal_signal_down_team",
        )
        goal_signal_up_team = _goal_signal_team_fact(
            goal_signal_up_team,
            goal_signal_up_speech,
            espn.team_names,
        )
        goal_signal_down_team = _goal_signal_team_fact(
            goal_signal_down_team,
            goal_signal_down_speech,
            espn.team_names,
        )
        favorite_team = (
            localized_team_name(espn, espn.favorite_team) if espn.favorite_team else ""
        )
        position_team = (
            localized_team_name(espn, espn.position_team) if espn.position_team else ""
        )
        inferred_position_market = bool(
            position_team
            and goal_signal_up_team
            and position_team.casefold() == goal_signal_up_team.casefold()
        )
        tracks_position = bool(
            position_team
            and item.get("tracks_position", inferred_position_market)
        )
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
                commentary_style=commentary_style,
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
                goal_signal_up_speech=goal_signal_up_speech,
                goal_signal_down_speech=goal_signal_down_speech,
                goal_signal_up_team=goal_signal_up_team,
                goal_signal_down_team=goal_signal_down_team,
                favorite_team=favorite_team,
                position_team=position_team,
                tracks_position=tracks_position,
            )
        )

    if not markets and not str(raw.get("active_canonical_event", "")).strip():
        raise ConfigError("config must include at least one market")

    config = WatchConfig(
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
        spoiler_free_mode=normalize_spoiler_free_mode(
            raw.get("spoiler_free_mode", False)
        ),
        quiet_hours=quiet,
        probability_bar=probability_bar,
        polymarket=polymarket,
        setup_server=setup_server,
        adaptive_polling=adaptive_polling,
        espn=espn,
        markets=markets,
        active_canonical_event=str(raw.get("active_canonical_event", "")).strip(),
        pairing_registry_path=str(raw.get("pairing_registry_path", "")).strip(),
    )
    if config.active_canonical_event:
        registry_path = Path(config.pairing_registry_path or "pairing_registry.json")
        if not registry_path.is_absolute():
            registry_path = path.parent / registry_path
        apply_pairing_registry(
            config,
            registry_path,
            bar_explicitly_disabled=bar_raw.get("enabled") is False,
        )
    return config


def load_pairing_registry(path: Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ConfigError(f"pairing registry not found: {path}") from error
    except json.JSONDecodeError as error:
        raise ConfigError(f"invalid JSON pairing registry {path}: {error}") from error
    entries = payload.get("canonical_events")
    if not isinstance(entries, list):
        raise ConfigError(f"{path}: canonical_events must be an array")
    return entries


def apply_pairing_registry(
    config: WatchConfig,
    registry_path: Path,
    *,
    bar_explicitly_disabled: bool = False,
) -> None:
    """Derive markets and the probability bar from one confirmed registry entry.

    The registry is written by the market-pairing skill and confirmed by a
    human; the watcher never invents pairings (PRD section 4.3). Derivation
    assumes a two-outcome winner market pair — top_n multi-outcome display
    arrives with P5.
    """
    event_id = config.active_canonical_event
    entries = load_pairing_registry(registry_path)
    entry = next(
        (item for item in entries if str(item.get("id", "")) == event_id),
        None,
    )
    if entry is None:
        raise ConfigError(
            f"active_canonical_event {event_id!r} not found in {registry_path}"
        )
    pairing = entry.get("pairing") or {}
    if pairing.get("confirmed") is not True:
        raise ConfigError(
            f"registry entry {event_id!r} is not confirmed yet; review the pairing "
            "and set pairing.confirmed=true before watching it"
        )
    outcomes = entry.get("outcomes")
    if not isinstance(outcomes, list) or len(outcomes) != 2:
        raise ConfigError(
            f"registry entry {event_id!r}: watcher derivation supports exactly two "
            "outcomes until multi-outcome display lands (P5)"
        )
    left_outcome, right_outcome = (str(name) for name in outcomes)

    venue_markets = entry.get("venue_markets") or []
    kalshi_market = next(
        (item for item in venue_markets if item.get("venue") == "kalshi"), None
    )
    if kalshi_market is None:
        raise ConfigError(f"registry entry {event_id!r} has no kalshi venue market")
    kalshi_map = kalshi_market.get("outcome_map") or {}
    if set(kalshi_map) != {left_outcome, right_outcome}:
        raise ConfigError(
            f"registry entry {event_id!r}: kalshi outcome_map keys must match outcomes"
        )
    polymarket_market = next(
        (item for item in venue_markets if item.get("venue") == "polymarket"), None
    )
    polymarket_map: dict[str, str] = {}
    if polymarket_market is not None:
        polymarket_map = polymarket_market.get("outcome_map") or {}
        if set(polymarket_map) != {left_outcome, right_outcome}:
            raise ConfigError(
                f"registry entry {event_id!r}: polymarket outcome_map keys must "
                "match outcomes"
            )
        if not str(polymarket_market.get("market_id", "")).strip():
            raise ConfigError(
                f"registry entry {event_id!r}: polymarket venue market needs market_id"
            )

    language = config.language
    outcome_labels = entry.get("outcome_labels") or {}
    display = entry.get("display") or {}

    def outcome_name(outcome: str) -> str:
        return _config_text(
            outcome_labels.get(outcome),
            language,
            path=f"registry[{event_id}].outcome_labels[{outcome}]",
            fallback=outcome,
        )

    def market_for(outcome: str, other: str, primary: bool) -> MarketConfig:
        name = outcome_name(outcome)
        return MarketConfig(
            ticker=str(kalshi_map[outcome]).strip().upper(),
            label=pick(language, f"{name}获胜", f"{name} to win"),
            side_i_care="yes",
            spread_move_cents=99,
            alerts_enabled=primary,
            show_in_ticker=primary,
            goal_signal_enabled=primary,
            goal_signal_up_team=name,
            goal_signal_down_team=outcome_name(other),
            language=language,
            commentary_style=config.espn.commentary_style,
        )

    if config.markets:
        print(
            f"note: active_canonical_event {event_id!r} overrides the configured "
            "markets list",
            file=sys.stderr,
        )
    config.markets = [
        market_for(left_outcome, right_outcome, primary=True),
        market_for(right_outcome, left_outcome, primary=False),
    ]

    bar = config.probability_bar
    # The bar is the whole point of a paired watch; only an explicit
    # "enabled": false in the watchlist keeps it off.
    bar.enabled = not bar_explicitly_disabled
    bar.mode = "normalized_outcomes"
    bar.market_ticker = config.markets[0].ticker
    bar.right_market_ticker = config.markets[1].ticker
    bar.side = "yes"
    left_display = display.get(left_outcome) or {}
    right_display = display.get(right_outcome) or {}
    bar.left_flag = str(left_display.get("flag", bar.left_flag)).strip().lower()
    bar.left_color = str(left_display.get("color", bar.left_color)).strip()
    bar.right_flag = str(right_display.get("flag", bar.right_flag)).strip().lower()
    bar.right_color = str(right_display.get("color", bar.right_color)).strip()
    if polymarket_market is not None:
        bar.polymarket_market_id = str(polymarket_market.get("market_id", "")).strip()
        bar.polymarket_left_outcome = str(polymarket_map[left_outcome])
        bar.polymarket_right_outcome = str(polymarket_map[right_outcome])
    else:
        bar.polymarket_market_id = ""
        bar.polymarket_left_outcome = ""
        bar.polymarket_right_outcome = ""

    entry_label = _config_text(
        entry.get("label"),
        language,
        path=f"registry[{event_id}].label",
        fallback=event_id,
    )
    starts_at = str(entry.get("starts_at", "")).strip()
    event_source = entry.get("event_source") or {}
    league = str(event_source.get("league", ""))
    if (
        str(event_source.get("provider", "")) == "espn"
        and league.startswith("soccer/")
        and str(event_source.get("event_id", "")).strip()
    ):
        config.espn.enabled = True
        config.espn.event_id = str(event_source["event_id"]).strip()
        config.espn.league = league.removeprefix("soccer/")
        config.espn.label = entry_label
        config.espn.starts_at = starts_at
    else:
        # Non-soccer categories run market-only until their category adapter
        # lands (P4); the start time still feeds adaptive polling.
        config.espn.starts_at = starts_at
        if not config.espn.enabled:
            config.espn.label = entry_label


def validate_registry_venues(config: WatchConfig) -> list[str]:
    """Best-effort startup check of a registry-derived setup against venues.

    Returns human-readable warnings. Failures degrade instead of crashing:
    an unusable polymarket mapping is dropped so the bar keeps running on
    Kalshi alone; missing or settled Kalshi markets only warn because the
    watch loop already reports missing tickers every poll.
    """
    warnings: list[str] = []
    if not config.active_canonical_event:
        return warnings
    kalshi = KalshiVenueAdapter(config.kalshi_base_url, fetch=http_json)
    for market in config.markets:
        try:
            meta = kalshi.metadata(market.ticker)
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError, ValueError):
            warnings.append(
                f"registry check: could not fetch kalshi market {market.ticker}; "
                "continuing without startup validation"
            )
            continue
        if meta is None:
            warnings.append(
                f"registry check: kalshi market {market.ticker} not found"
            )
        elif meta.status == "settled":
            warnings.append(
                f"registry check: kalshi market {market.ticker} is already settled"
            )
    bar = config.probability_bar
    if bar.polymarket_market_id:
        polymarket = PolymarketVenueAdapter(config.polymarket.base_url, fetch=http_json)
        try:
            meta = polymarket.metadata(bar.polymarket_market_id)
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError, ValueError):
            warnings.append(
                "registry check: could not fetch polymarket market "
                f"{bar.polymarket_market_id}; keeping the mapping"
            )
            meta = None
        else:
            if meta is None:
                warnings.append(
                    f"registry check: polymarket market {bar.polymarket_market_id} "
                    "not found; dropping the polymarket mapping"
                )
                bar.polymarket_market_id = ""
            else:
                gamma_outcomes = {name.casefold() for name in meta.outcomes}
                mapped = {
                    bar.polymarket_left_outcome.casefold(),
                    bar.polymarket_right_outcome.casefold(),
                }
                if not mapped.issubset(gamma_outcomes):
                    warnings.append(
                        "registry check: polymarket outcome labels do not match "
                        f"market {bar.polymarket_market_id} ({sorted(meta.outcomes)}); "
                        "dropping the polymarket mapping"
                    )
                    bar.polymarket_market_id = ""
    return warnings


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
    normalize_commentary_style(config.espn.commentary_style)
    for market in config.markets:
        normalize_commentary_style(
            market.commentary_style,
            path=f"markets[{market.ticker!r}].commentary_style",
        )
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
        bar = config.probability_bar
        if bar.polymarket_market_id:
            if bar.mode != "normalized_outcomes":
                raise ConfigError(
                    "probability_bar.polymarket needs normalized_outcomes mode"
                )
            if not bar.polymarket_left_outcome or not bar.polymarket_right_outcome:
                raise ConfigError(
                    "probability_bar.polymarket needs both left_outcome and right_outcome"
                )
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


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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
    adapter = KalshiVenueAdapter(config.kalshi_base_url, fetch=http_json)
    by_label = {market.ticker: market.label for market in config.markets}
    snapshots: dict[str, MarketSnapshot] = {}

    for market in adapter.raw_markets(tickers):
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
            liquidity_usd=_optional_float(
                market.get("liquidity_dollars") or market.get("liquidity")
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


def compact_match_score_text(snapshot: MatchSnapshot, config: ESPNConfig) -> str:
    home = snapshot.home.abbreviation or localized_team_name(config, snapshot.home)
    away = snapshot.away.abbreviation or localized_team_name(config, snapshot.away)
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
                f"{home}和{away}比赛战成{snapshot.home.score}比{snapshot.away.score}，"
                f"点球比分{snapshot.home.shootout_score or '0'}比{snapshot.away.shootout_score or '0'}"
            ),
            (
                f"It is {home} {snapshot.home.score}, {away} {snapshot.away.score}; "
                f"penalties {snapshot.home.shootout_score or '0'} to "
                f"{snapshot.away.shootout_score or '0'}"
            ),
        )
    if config.language == "en":
        return f"It is {home} {snapshot.home.score}, {away} {snapshot.away.score}"
    home_score = _score_number(snapshot.home.score)
    away_score = _score_number(snapshot.away.score)
    if home_score is not None and away_score is not None:
        if home_score == away_score:
            return f"{home}和{away}打成{snapshot.home.score}比{snapshot.away.score}"
        if home_score > away_score:
            return f"{home}{snapshot.home.score}比{snapshot.away.score}领先{away}"
        return f"{away}{snapshot.away.score}比{snapshot.home.score}领先{home}"
    return f"比分为{home}{snapshot.home.score}比{snapshot.away.score}{away}"


def commentary_score(
    text: str,
    snapshot: MatchSnapshot,
) -> tuple[int, int] | None:
    """Return the score explicitly embedded in one ESPN commentary row.

    A poll can deliver several missed events at once while the scoreboard on
    ``snapshot`` already reflects the newest one.  Using the row's own score
    prevents an earlier goal from being narrated with the later score.
    """

    details = _commentary_score_details(text, snapshot)
    return (details[0], details[1]) if details is not None else None


def _commentary_score_details(
    text: str,
    snapshot: MatchSnapshot,
) -> tuple[int, int, int | None, int | None] | None:
    """Parse regular and optional parenthesized shootout scores."""

    home_aliases = tuple(
        value for value in (snapshot.home.name, snapshot.home.abbreviation) if value
    )
    away_aliases = tuple(
        value for value in (snapshot.away.name, snapshot.away.abbreviation) if value
    )
    score_token = r"(\d+)(?:\((\d+)\))?"
    ending = r"(?=$|[\s.,;:!?])"
    for home_name in home_aliases:
        for away_name in away_aliases:
            home_first = re.search(
                rf"{re.escape(home_name)}\s+{score_token}\s*,\s*"
                rf"{re.escape(away_name)}\s+{score_token}{ending}",
                text,
                re.IGNORECASE,
            )
            if home_first:
                return (
                    int(home_first.group(1)),
                    int(home_first.group(3)),
                    int(home_first.group(2)) if home_first.group(2) is not None else None,
                    int(home_first.group(4)) if home_first.group(4) is not None else None,
                )
            away_first = re.search(
                rf"{re.escape(away_name)}\s+{score_token}\s*,\s*"
                rf"{re.escape(home_name)}\s+{score_token}{ending}",
                text,
                re.IGNORECASE,
            )
            if away_first:
                return (
                    int(away_first.group(3)),
                    int(away_first.group(1)),
                    int(away_first.group(4)) if away_first.group(4) is not None else None,
                    int(away_first.group(2)) if away_first.group(2) is not None else None,
                )
    return None


def snapshot_at_commentary_score(
    item: dict[str, Any],
    snapshot: MatchSnapshot,
) -> MatchSnapshot:
    play = item.get("play") or {}
    text = str(item.get("text") or play.get("text") or "")
    score = _commentary_score_details(text, snapshot)
    if score is None:
        return snapshot
    home_score, away_score, home_shootout, away_shootout = score
    has_shootout_score = home_shootout is not None or away_shootout is not None
    preserve_shootout = "penalty shootout" in text.casefold()
    return replace(
        snapshot,
        home=replace(
            snapshot.home,
            score=str(home_score),
            shootout_score=(
                str(home_shootout or 0)
                if has_shootout_score
                else snapshot.home.shootout_score if preserve_shootout else ""
            ),
        ),
        away=replace(
            snapshot.away,
            score=str(away_score),
            shootout_score=(
                str(away_shootout or 0)
                if has_shootout_score
                else snapshot.away.shootout_score if preserve_shootout else ""
            ),
        ),
    )


def commentary_key(item: dict[str, Any]) -> str:
    play = item.get("play") or {}
    if play.get("id") is not None:
        return f"play:{play['id']}"
    if item.get("sequence") is not None:
        return f"sequence:{item['sequence']}"
    clock = (item.get("time") or {}).get("value", "")
    return f"fallback:{clock}:{item.get('text', '')}"


def canonical_commentary_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse ESPN's paired/resequenced rows to one stable play description.

    ESPN can publish both sides of one foul as separate commentary rows, then
    renumber those rows on a later poll.  Both rows share the stable play id.
    Prefer the top-level text that matches ``play.text`` because that is ESPN's
    canonical description, otherwise retain the last non-empty description.
    """

    order: list[str] = []
    selected: dict[str, dict[str, Any]] = {}
    selected_score: dict[str, int] = {}
    for item in items:
        key = commentary_key(item)
        text = str(item.get("text") or "").strip()
        play_text = str((item.get("play") or {}).get("text") or "").strip()
        score = 2 if text and play_text and text == play_text else (1 if text else 0)
        if key not in selected:
            order.append(key)
            selected[key] = item
            selected_score[key] = score
            continue
        if score >= selected_score[key]:
            selected[key] = item
            selected_score[key] = score
    return [selected[key] for key in order]


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


def _resolved_player_profile(
    config: ESPNConfig,
    player: MatchPlayer | None,
) -> ResolvedPlayerProfile | None:
    if player is None:
        return None
    return resolve_player_profile(
        config.player_catalog,
        athlete_id=player.athlete_id,
        name=player.name,
        short_name=player.short_name,
        language=config.language,
        player_names=config.player_names,
        star_chants=config.star_chants,
    )


def localized_player_name(config: ESPNConfig, player: MatchPlayer | None) -> str:
    if player is None:
        return ""
    profile = _resolved_player_profile(config, player)
    return (profile.display_name if profile else "") or player.name or player.short_name


def spoken_player_name(config: ESPNConfig, player: MatchPlayer | None) -> str:
    """Return a nickname only for casual speech; facts and balloons stay formal."""

    if player is None:
        return ""
    profile = _resolved_player_profile(config, player)
    if (
        profile is not None
        and config.commentary_style == "casual"
        and profile.casual_name
    ):
        return profile.casual_name
    return (profile.display_name if profile else "") or player.name or player.short_name


def player_announcement(config: ESPNConfig, player: MatchPlayer | None) -> str:
    if player is None:
        return ""
    name = spoken_player_name(config, player)
    profile = _resolved_player_profile(config, player)
    # ESPN already gives us a stable player identity.  Repeating "number N
    # player" before every unfamiliar name sounds like a roster export rather
    # than live commentary, and the number adds no event fact that is missing
    # from the name itself.
    if config.language == "en" and player.jersey and not (
        profile and profile.featured
    ):
        return f"number {player.jersey} {name}"
    return name


def render_star_chant(
    config: ESPNConfig,
    player: MatchPlayer | None,
    team_name: str,
) -> str:
    profile = _resolved_player_profile(config, player)
    chant = profile.goal_chant if profile else ""
    if not chant or player is None:
        return ""
    values = {
        "name": spoken_player_name(config, player),
        "formal_name": localized_player_name(config, player),
        "number": player.jersey,
        "team": team_name,
    }
    for key, value in values.items():
        chant = chant.replace("{" + key + "}", value)
    return chant.strip()


def player_catalog_coverage(
    snapshot: MatchSnapshot,
    config: ESPNConfig,
) -> tuple[int, int, int, tuple[str, ...], tuple[str, ...]]:
    """Summarize roster coverage without guessing unknown Chinese names."""

    unique: dict[str, MatchPlayer] = {}
    for player in snapshot.players.values():
        identity = player.athlete_id or player.name.casefold() or player.short_name.casefold()
        if identity:
            unique.setdefault(identity, player)
    matched = 0
    featured = 0
    fallback: list[str] = []
    for identity, player in unique.items():
        profile = _resolved_player_profile(config, player)
        if profile:
            featured += int(profile.featured)
        if profile and profile.display_name:
            matched += 1
        else:
            fallback.append(player.name or player.short_name or identity)
    signature = tuple(sorted(unique))
    return len(unique), matched, featured, tuple(sorted(fallback)), signature


def report_player_catalog_coverage(
    snapshot: MatchSnapshot,
    config: ESPNConfig,
    state: ESPNState,
) -> None:
    total, matched, featured, fallback, signature = player_catalog_coverage(snapshot, config)
    if not signature or signature == state.player_coverage_signature:
        return
    state.player_coverage_signature = signature
    message = (
        f"ESPN player catalog: {matched}/{total} named, {featured} featured"
    )
    if fallback:
        preview = ", ".join(fallback[:8])
        remainder = len(fallback) - min(len(fallback), 8)
        message += f"; raw-name fallback: {preview}"
        if remainder:
            message += f" (+{remainder} more)"
    print(message, file=sys.stderr)


def _event_clock(item: dict[str, Any]) -> str:
    play = item.get("play") or {}
    value = str((item.get("time") or {}).get("displayValue") or (play.get("clock") or {}).get("displayValue") or "")
    return f"{value} " if value else ""


def _team_event_face(config: ESPNConfig, team: MatchTeam | None, positive: bool) -> str:
    if team is None or not config.favorite_team:
        return "surprise"
    favorite = is_favorite_team(config, team)
    return "happy" if favorite == positive else "sad"


def _espn_status_code(play_type: str, lower_text: str) -> str:
    status_code = {
        "halftime": "halftime",
        "end-regular-time": "end_regular",
        "end-extra-time": "end_extra",
        "start-shootout": "shootout_start",
        "full-time": "full_time",
    }.get(play_type, "")
    if lower_text.startswith("first half begins"):
        return "kickoff"
    if lower_text.startswith("second half begins"):
        return "second_half"
    if lower_text.startswith("penalty shootout ends"):
        return "shootout_end"
    if lower_text.startswith("match ends"):
        return "full_time"
    return status_code


def _first_matching_phrase(text: str, choices: tuple[tuple[str, str], ...]) -> str:
    for phrase, value in choices:
        if phrase in text:
            return value
    return ""


def parse_espn_event_facts(
    item: dict[str, Any],
    snapshot: MatchSnapshot,
    config: ESPNConfig,
) -> ESPNEventFacts:
    """Parse only deterministic facts made explicit by ESPN metadata/text."""

    play = item.get("play") or {}
    play_type = str((play.get("type") or {}).get("type") or "").lower()
    text = str(item.get("text") or play.get("text") or "")
    lower_text = text.casefold()
    score_snapshot = snapshot_at_commentary_score(item, snapshot)
    team = _event_team(item, snapshot)
    primary = _event_player(item, snapshot)
    if team is None and primary and primary.team_name:
        team = match_team(snapshot, primary.team_name)

    participants: list[MatchPlayer] = []
    seen_players: set[tuple[str, str]] = set()
    for index, _participant in enumerate(play.get("participants") or []):
        candidate = _event_player_at(item, snapshot, index)
        if candidate is None:
            continue
        key = (candidate.athlete_id, candidate.name.casefold())
        if key in seen_players:
            continue
        seen_players.add(key)
        participants.append(candidate)

    assistant = _shot_assistant(item, snapshot)
    goalkeeper = _event_goalkeeper(item, snapshot, team)
    incoming = _event_player_at(item, snapshot, 0) if play_type == "substitution" else None
    outgoing = _event_player_at(item, snapshot, 1) if play_type == "substitution" else None
    beneficiary = _event_player_at(item, snapshot, 1) if play_type == "foul" else None
    awarded_team: MatchTeam | None = None
    if play_type in {"penalty", "penalty-awarded", "penalty---awarded"}:
        awarded_team = team
    elif play_type == "foul" and (
        "wins a free kick" in lower_text
        or (lower_text.startswith("penalty ") and "draws a foul" in lower_text)
    ):
        awarded_team = (
            match_team(snapshot, beneficiary.team_name)
            if beneficiary and beneficiary.team_name
            else opposing_match_team(snapshot, team)
        )

    status_code = _espn_status_code(play_type, lower_text)
    in_box = any(
        phrase in lower_text
        for phrase in (
            "very close range",
            "six yard box",
            "centre of the box",
            "center of the box",
            "left side of the box",
            "right side of the box",
        )
    )
    close_miss = any(
        phrase in lower_text
        for phrase in (
            " is close",
            "just wide",
            "just over",
            "narrowly wide",
            "narrowly over",
        )
    )
    hit_woodwork = play_type in {"hit-woodwork", "shot-hit-woodwork", "woodwork"} or any(
        phrase in lower_text
        for phrase in (
            "hits the post",
            "hits the left post",
            "hits the right post",
            "hits the bar",
            "hits the crossbar",
        )
    )

    event_type = play_type
    result = ""
    if play_type == "start-delay" and "drinks break" in lower_text:
        event_type, result = "drinks_break", "started"
    elif play_type == "end-delay" and _ends_drinks_break(item, snapshot):
        event_type, result = "drinks_break", "ended"
    elif play_type == "penalty---scored":
        event_type, result = "penalty", "scored"
    elif play_type == "penalty---saved":
        event_type, result = "penalty", "saved"
    elif play_type == "penalty---missed":
        event_type, result = "penalty", "missed"
    elif (
        play_type == "foul"
        and lower_text.startswith("penalty ")
        and "draws a foul" in lower_text
    ) or play_type in {"penalty", "penalty-awarded", "penalty---awarded"}:
        event_type, result = "penalty_awarded", "awarded"
    elif lower_text.startswith("goal!") or play_type == "goal" or play_type.startswith("goal---"):
        event_type, result = "goal", "scored"
    elif play_type == "substitution":
        event_type, result = "substitution", "completed"
    elif hit_woodwork:
        event_type, result = "woodwork", "hit_woodwork"
    elif play_type == "shot-on-target":
        event_type, result = "shot_saved", "saved"
    elif play_type == "shot-off-target" and (close_miss or in_box):
        event_type, result = "close_miss", "missed"
    elif play_type == "shot-blocked" and in_box:
        event_type, result = "shot_blocked", "blocked"
    elif play_type in {"red-card", "second-yellow-card"} or bool(play.get("redCard")) or "red card" in lower_text:
        event_type = "red_card"
        result = "second_yellow" if play_type == "second-yellow-card" else "sent_off"
    elif play_type == "yellow-card":
        event_type, result = "yellow_card", "booked"
    elif play_type == "corner-awarded":
        event_type, result = "corner", "awarded"
    elif play_type == "foul" and "wins a free kick" in lower_text:
        event_type, result = "free_kick", "awarded"
    elif play_type == "foul" and lower_text.startswith("foul by"):
        event_type, result = "foul", "called"
    elif status_code:
        event_type, result = "status", status_code

    home_score_number = _score_number(score_snapshot.home.score)
    away_score_number = _score_number(score_snapshot.away.score)
    is_equalizer = bool(
        event_type == "goal"
        and home_score_number is not None
        and away_score_number is not None
        and home_score_number == away_score_number
        and home_score_number > 0
    )

    shot_area = _first_matching_phrase(
        lower_text,
        (
            ("at the far post", "far_post"),
            ("far post", "far_post"),
            ("very close range", "very_close"),
            ("six yard box", "six_yard_box"),
            ("centre of the box", "centre_of_box"),
            ("center of the box", "centre_of_box"),
            ("left side of the box", "left_of_box"),
            ("right side of the box", "right_of_box"),
            ("outside the box", "outside_box"),
        ),
    )
    shot_body_part = _first_matching_phrase(
        lower_text,
        (
            ("header", "header"),
            ("right footed low shot", "right_foot"),
            ("right-footed low shot", "right_foot"),
            ("right footed shot", "right_foot"),
            ("right-footed shot", "right_foot"),
            ("left footed low shot", "left_foot"),
            ("left-footed low shot", "left_foot"),
            ("left footed shot", "left_foot"),
            ("left-footed shot", "left_foot"),
        ),
    )
    shot_direction = _first_matching_phrase(
        lower_text,
        (
            ("bottom left corner", "bottom_left"),
            ("bottom right corner", "bottom_right"),
            ("top left corner", "top_left"),
            ("top right corner", "top_right"),
            ("high centre of the goal", "high_centre"),
            ("high center of the goal", "high_centre"),
            ("centre of the goal", "centre"),
            ("center of the goal", "centre"),
        ),
    )
    shot_technique = _first_matching_phrase(
        lower_text,
        (
            ("half volley", "half_volley"),
            ("volley", "volley"),
            ("low shot", "low_shot"),
            ("lobbed shot", "lob"),
            ("curling shot", "curling_shot"),
        ),
    )
    delivery = ""
    if assistant is not None:
        delivery = _first_matching_phrase(
            lower_text,
            (
                ("with a cross", "cross"),
                ("with a through ball", "through_ball"),
                ("assisted by", "pass"),
            ),
        )
    set_piece_location = _first_matching_phrase(
        lower_text,
        (
            ("attacking half", "attacking_half"),
            ("left wing", "left_wing"),
            ("right wing", "right_wing"),
            ("defensive half", "defensive_half"),
            ("following a corner", "following_corner"),
            ("from a direct free kick", "direct_free_kick"),
        ),
    )
    injury_reason = (
        "injury"
        if "because of an injury" in lower_text or "due to an injury" in lower_text
        else ""
    )

    return ESPNEventFacts(
        play_type=play_type,
        event_type=event_type,
        clock=_event_clock(item).strip(),
        team=team,
        team_name=localized_team_name(config, team) if team else "",
        primary_player=primary,
        primary_player_name=(
            player_announcement(config, primary) if config.announce_player_names else ""
        ),
        participants=tuple(participants),
        assistant=assistant,
        assistant_name=(
            player_announcement(config, assistant) if config.announce_player_names else ""
        ),
        goalkeeper=goalkeeper,
        goalkeeper_name=(
            localized_player_name(config, goalkeeper) if config.announce_player_names else ""
        ),
        incoming=incoming,
        incoming_name=(
            player_announcement(config, incoming) if config.announce_player_names else ""
        ),
        outgoing=outgoing,
        outgoing_name=(
            player_announcement(config, outgoing) if config.announce_player_names else ""
        ),
        beneficiary=beneficiary,
        beneficiary_name=(
            player_announcement(config, beneficiary) if config.announce_player_names else ""
        ),
        awarded_team=awarded_team,
        awarded_team_name=(localized_team_name(config, awarded_team) if awarded_team else ""),
        result=result,
        score_text=match_score_text(score_snapshot, config),
        compact_score_text=compact_match_score_text(score_snapshot, config),
        score_speech=match_score_speech(score_snapshot, config),
        shot_body_part=shot_body_part,
        shot_area=shot_area,
        shot_direction=shot_direction,
        shot_technique=shot_technique,
        delivery=delivery,
        set_piece_location=set_piece_location,
        injury_reason=injury_reason,
        status_code=status_code,
        close_miss=close_miss,
        is_equalizer=is_equalizer,
    )


MAJOR_PERSPECTIVE_EVENTS = frozenset(
    {
        "espn_goal",
        "espn_penalty",
        "espn_penalty_awarded",
        "espn_red_card",
        "espn_status",
    }
)


def _configured_match_team(
    snapshot: MatchSnapshot,
    config: ESPNConfig,
    configured_name: str,
) -> MatchTeam | None:
    if not configured_name:
        return None
    return next(
        (
            team
            for team in (snapshot.home, snapshot.away)
            if is_configured_team(config, configured_name, team)
        ),
        None,
    )


def _configured_event_outcome(
    snapshot: MatchSnapshot,
    config: ESPNConfig,
    configured_name: str,
    event_team: MatchTeam | None,
    event_team_outcome: int,
    *,
    missing: str,
) -> str:
    configured_team = _configured_match_team(snapshot, config, configured_name)
    if configured_team is None or event_team is None or event_team_outcome == 0:
        return missing
    same_team = is_configured_team(config, configured_team.name, event_team)
    benefits = event_team_outcome > 0 if same_team else event_team_outcome < 0
    return "benefit" if benefits else "harm"


def event_perspective(
    snapshot: MatchSnapshot,
    config: ESPNConfig,
    facts: ESPNEventFacts,
    alert_kind: str,
) -> EventPerspective:
    """Return deterministic fan/position impact for one rendered event."""

    is_final = alert_kind == "espn_status" and facts.status_code in {
        "full_time",
        "shootout_end",
    }
    if is_final:
        winner = match_winner(snapshot)

        def final_outcome(configured_name: str, missing: str) -> str:
            configured_team = _configured_match_team(snapshot, config, configured_name)
            if configured_team is None or winner is None:
                return missing
            return (
                "benefit"
                if is_configured_team(config, configured_team.name, winner)
                else "harm"
            )

        support_outcome = final_outcome(config.favorite_team, "neutral")
        position_outcome = final_outcome(config.position_team, "none")
    else:
        event_team = facts.awarded_team or facts.team
        if alert_kind in {"espn_goal", "espn_penalty_awarded", "espn_corner"}:
            team_outcome = 1
        elif alert_kind == "espn_penalty":
            team_outcome = 1 if facts.result == "scored" else -1
        elif alert_kind in {
            "espn_woodwork",
            "espn_shot_saved",
            "espn_close_miss",
            "espn_shot_blocked",
            "espn_red_card",
            "espn_yellow_card",
            "espn_foul",
        }:
            team_outcome = -1
        elif alert_kind == "espn_opponent_free_kick":
            team_outcome = 1
        else:
            team_outcome = 0
        support_outcome = _configured_event_outcome(
            snapshot,
            config,
            config.favorite_team,
            event_team,
            team_outcome,
            missing="neutral",
        )
        # Routine chances are not strong enough to imply a meaningful position
        # change.  Position language is reserved for genuinely consequential
        # events so the commentary still sounds like football commentary.
        if alert_kind in MAJOR_PERSPECTIVE_EVENTS:
            position_outcome = _configured_event_outcome(
                snapshot,
                config,
                config.position_team,
                event_team,
                team_outcome,
                missing="none",
            )
        else:
            position_outcome = "none"

    if position_outcome == "none":
        alignment = "support_only" if support_outcome != "neutral" else "neutral"
    elif support_outcome == "neutral":
        alignment = "position_only"
    elif support_outcome == position_outcome:
        alignment = "aligned"
    else:
        alignment = "conflict"
    return EventPerspective(support_outcome, position_outcome, alignment)


def _configured_team_label(config: ESPNConfig, value: str, fallback: str) -> str:
    return localized_team_name(config, value) if value else fallback


def _major_perspective_reaction(
    perspective: EventPerspective,
    config: ESPNConfig,
    style: str,
) -> str:
    support = _configured_team_label(
        config,
        config.favorite_team,
        pick(config.language, "支持的球队", "the supported team"),
    )
    position = _configured_team_label(
        config,
        config.position_team,
        pick(config.language, "当前持仓", "the current position"),
    )
    good = perspective.support_outcome == "benefit"
    position_good = perspective.position_outcome == "benefit"

    if config.language == "en":
        if perspective.alignment == "aligned":
            return (
                f"That helps both {support} and the {position} position"
                if good
                else f"That hurts both {support} and the {position} position"
            )
        if perspective.alignment == "conflict":
            return (
                f"Good news for {support}, but the {position} position comes under pressure"
                if good
                else f"Tough for {support}, although the {position} position benefits"
            )
        if perspective.alignment == "position_only":
            return (
                f"The {position} position benefits"
                if position_good
                else f"The {position} position comes under pressure"
            )
        if perspective.alignment == "support_only":
            return f"That suits {support}" if good else f"That is a setback for {support}"
        return ""

    if perspective.alignment == "aligned":
        if style == "casual":
            return "这一下看着舒服，仓位也跟着受益" if good else "场面难受，仓位也跟着承压"
        if style == "professional":
            return (
                f"这一变化有利于{support}，也利好{position}持仓"
                if good
                else f"这一变化打击{support}，{position}持仓也随之承压"
            )
        return (
            f"支持的{support}占到便宜，{position}仓位也随之受益"
            if good
            else f"支持的{support}受到打击，{position}仓位也跟着承压"
        )
    if perspective.alignment == "conflict":
        if good:
            return (
                "球迷这边开心，仓位却要承压"
                if style == "casual"
                else f"{support}占优，但{position}仓位因此承压"
            )
        return (
            "感情上不好受，不过仓位倒是受益"
            if style == "casual"
            else f"{support}受到打击，不过{position}仓位从中受益"
        )
    if perspective.alignment == "position_only":
        if style == "casual":
            return "这波对仓位有利" if position_good else "这波对仓位不利"
        return f"这对{position}持仓有利" if position_good else f"这对{position}持仓不利"
    if perspective.alignment == "support_only":
        if style == "casual":
            return "这下舒服了" if good else "这下有点难受"
        return f"这对支持的{support}是好消息" if good else f"这对支持的{support}不是好消息"
    return ""


def _routine_support_reaction(
    alert_kind: str,
    perspective: EventPerspective,
    config: ESPNConfig,
    style: str,
) -> str:
    outcome = perspective.support_outcome
    if outcome == "neutral":
        return ""
    support = _configured_team_label(config, config.favorite_team, "支持的球队")
    failed_attack = alert_kind in {
        "espn_woodwork",
        "espn_shot_saved",
        "espn_close_miss",
        "espn_shot_blocked",
    }
    set_piece = alert_kind in {"espn_corner", "espn_opponent_free_kick"}
    yellow_card = alert_kind == "espn_yellow_card"
    foul = alert_kind == "espn_foul"
    good = outcome == "benefit"
    if config.language == "en":
        if failed_attack:
            return f"{support} survives the danger" if good else f"A chance goes begging for {support}"
        if set_piece:
            return f"A useful opening for {support}" if good else f"{support} must defend this carefully"
        if yellow_card or foul:
            return f"That helps {support}" if good else f"That is costly for {support}"
        return ""
    if failed_attack:
        if style == "casual":
            return "还好，这次守住了" if good else "可惜，这次机会没能兑现"
        if style == "professional":
            return f"{support}暂时化解险情" if good else f"{support}这次进攻未能转化为进球"
        return f"{support}躲过一次威胁" if good else f"{support}错过一次机会"
    if set_piece:
        if style == "casual":
            return "机会来了" if good else "这下要小心"
        if style == "professional":
            return f"{support}获得继续施压的机会" if good else f"{support}需要应对这次定位球"
        return f"{support}迎来进攻机会" if good else f"{support}接下来要注意防守"
    if yellow_card:
        if style == "casual":
            return "对面吃牌，对我们有利" if good else "这张牌不太划算"
        return f"这张牌对{support}有利" if good else f"这张牌对{support}不利"
    if foul:
        if style == "casual":
            return "对面这次犯规对我们有利" if good else "这次犯规不太划算"
        return f"这次判罚有利于{support}" if good else f"这次判罚对{support}不利"
    return ""


def _fact_actor(facts: ESPNEventFacts, config: ESPNConfig) -> str:
    player = facts.primary_player_name
    team = facts.team_name
    if player and team:
        return pick(config.language, f"{team}的{player}", f"{player} for {team}")
    return player or team or pick(config.language, "场上球员", "the player")


def _compact_player_name(config: ESPNConfig, player: MatchPlayer | None) -> str:
    if player is None or not config.announce_player_names:
        return ""
    profile = _resolved_player_profile(config, player)
    configured = profile.display_name if profile else ""
    if config.language == "en":
        candidates = [
            value.split()[-1]
            for value in (configured, player.short_name, player.name)
            if value and value.split()
        ]
        return min(candidates, key=len) if candidates else ""
    if configured:
        return configured
    candidates = [
        value.split()[-1]
        for value in (player.short_name, player.name)
        if value and value.split()
    ]
    return min(candidates, key=len) if candidates else ""


def _compact_espn_balloon(
    alert: Alert,
    facts: ESPNEventFacts,
    config: ESPNConfig,
) -> str:
    """Render a screen-sized fact summary independently of verbose speech."""

    clock = f"{facts.clock} " if facts.clock else ""
    player = _compact_player_name(config, facts.primary_player)
    full_team = facts.awarded_team_name or facts.team_name or pick(
        config.language, "一方", "Team"
    )
    event_team = full_team
    event_team_object = facts.awarded_team or facts.team
    if config.language == "en" and event_team_object and event_team_object.abbreviation:
        event_team = event_team_object.abbreviation
    subject = player or event_team or pick(config.language, "场上", "Play")
    actor = f"{event_team} {player}" if player and event_team else subject
    keeper = _compact_player_name(config, facts.goalkeeper) or pick(
        config.language, "门将", "GK"
    )
    incoming = _compact_player_name(config, facts.incoming) or pick(
        config.language, "球员", "player"
    )
    outgoing = _compact_player_name(config, facts.outgoing) or pick(
        config.language, "球员", "player"
    )
    if config.language == "en":
        events = {
            "espn_goal": f"{actor} goal",
            "espn_woodwork": f"{actor} hits woodwork",
            "espn_shot_saved": f"{actor} shot | {keeper} save",
            "espn_close_miss": f"{actor} just wide",
            "espn_shot_blocked": f"{actor} shot blocked",
            "espn_red_card": f"{actor} red card",
            "espn_yellow_card": f"{actor} yellow card",
            "espn_corner": f"{event_team} corner",
            "espn_opponent_free_kick": f"{event_team} free kick",
            "espn_foul": f"{actor} foul",
            "espn_drinks_break": "Drinks break",
            "espn_drinks_break_end": "Play resumes",
        }
        penalty_results = {
            "scored": f"{actor} penalty scored",
            "saved": f"{actor} penalty | {keeper} save",
            "missed": f"{actor} penalty missed",
        }
        status_labels = {
            "kickoff": "Kickoff",
            "halftime": "Half-time",
            "second_half": "Second half",
            "end_regular": "End regulation",
            "end_extra": "End extra time",
            "shootout_start": "Penalty shootout",
            "shootout_end": "Shootout over",
            "full_time": "Full time",
        }
        substitution = f"{event_team} sub {incoming}↑ {outgoing}↓"
        penalty_awarded = f"Penalty to {event_team}"
    else:
        events = {
            "espn_goal": f"{actor}进球",
            "espn_woodwork": f"{actor}击中门框",
            "espn_shot_saved": f"{actor}攻门，{keeper}扑出",
            "espn_close_miss": f"{actor}攻门偏出",
            "espn_shot_blocked": f"{actor}攻门被封堵",
            "espn_red_card": f"{actor}红牌",
            "espn_yellow_card": f"{actor}黄牌",
            "espn_corner": f"{event_team}角球",
            "espn_opponent_free_kick": f"{event_team}任意球",
            "espn_foul": f"{actor}犯规",
            "espn_drinks_break": "补水时间",
            "espn_drinks_break_end": "比赛继续",
        }
        penalty_results = {
            "scored": f"{actor}点球命中",
            "saved": f"{actor}点球被{keeper}扑出",
            "missed": f"{actor}点球罚失",
        }
        status_labels = {
            "kickoff": "比赛开始",
            "halftime": "半场结束",
            "second_half": "下半场开始",
            "end_regular": "常规时间结束",
            "end_extra": "加时结束",
            "shootout_start": "点球大战开始",
            "shootout_end": "点球大战结束",
            "full_time": "比赛结束",
        }
        substitution = f"{event_team}换人 {incoming}↑ {outgoing}↓"
        penalty_awarded = f"{event_team}获得点球"

    if alert.kind == "espn_penalty":
        event = penalty_results.get(facts.result, pick(config.language, "点球", "Penalty"))
    elif alert.kind == "espn_penalty_awarded":
        event = penalty_awarded
    elif alert.kind == "espn_substitution":
        event = substitution
    elif alert.kind == "espn_status":
        event = status_labels.get(facts.status_code, alert.balloon.split(" | ", 1)[0])
    else:
        event = events.get(alert.kind, alert.balloon.split(" | ", 1)[0])
    return f"{clock}{event} | {facts.compact_score_text}"


def _spoken_clock(facts: ESPNEventFacts, config: ESPNConfig) -> str:
    if not facts.clock:
        return ""
    if config.language == "en":
        return f"At {facts.clock}, "
    value = facts.clock.removesuffix("'").replace("' + ", "+").replace("'+", "+")
    return f"第{value}分钟，"


def _shot_attempt_text(facts: ESPNEventFacts, config: ESPNConfig) -> str:
    areas = {
        "very_close": pick(config.language, "近距离", "from very close range"),
        "six_yard_box": pick(config.language, "小禁区内", "from inside the six-yard box"),
        "centre_of_box": pick(config.language, "禁区中路", "from the centre of the box"),
        "left_of_box": pick(config.language, "禁区左侧", "from the left side of the box"),
        "right_of_box": pick(config.language, "禁区右侧", "from the right side of the box"),
        "outside_box": pick(config.language, "禁区外", "from outside the box"),
        "far_post": pick(config.language, "后点包抄", "at the far post"),
    }
    bodies = {
        "header": pick(config.language, "头球攻门", "a header"),
        "right_foot": pick(config.language, "右脚射门", "a right-footed shot"),
        "left_foot": pick(config.language, "左脚射门", "a left-footed shot"),
    }
    techniques = {
        "half_volley": pick(config.language, "半凌空抽射", "a half-volley"),
        "volley": pick(config.language, "凌空抽射", "a volley"),
        "low_shot": pick(config.language, "低射", "a low shot"),
        "lob": pick(config.language, "挑射", "a lob"),
        "curling_shot": pick(config.language, "兜射", "a curling shot"),
    }
    area = areas.get(facts.shot_area, "")
    attempt = techniques.get(facts.shot_technique) or bodies.get(facts.shot_body_part, "")
    if facts.shot_technique and facts.shot_body_part in {"right_foot", "left_foot"}:
        if config.language == "en":
            foot = "right-footed" if facts.shot_body_part == "right_foot" else "left-footed"
            technique_nouns = {
                "half_volley": "half-volley",
                "volley": "volley",
                "low_shot": "shot",
                "lob": "lob",
                "curling_shot": "curling shot",
            }
            adjective = "low " if facts.shot_technique == "low_shot" else ""
            noun = technique_nouns.get(facts.shot_technique, "shot")
            attempt = f"a {adjective}{foot} {noun}"
        else:
            foot = "右脚" if facts.shot_body_part == "right_foot" else "左脚"
            attempt = f"{foot}{techniques[facts.shot_technique]}"
    if config.language == "en":
        if attempt and area:
            return f"{attempt} {area}"
        return attempt or area
    return f"{area}{attempt}" if area or attempt else ""


def _delivery_text(
    facts: ESPNEventFacts,
    config: ESPNConfig,
    *,
    assisted_goal: bool = True,
) -> str:
    if not facts.assistant_name:
        return ""
    assistant_name = facts.assistant_name
    if config.language == "en" and facts.assistant is not None:
        assistant_name = localized_player_name(config, facts.assistant)
    if assisted_goal:
        descriptions = {
            "cross": pick(config.language, "传中助攻", "supplies the assist with a cross"),
            "through_ball": pick(config.language, "直塞助攻", "supplies the assist with a through ball"),
            "pass": pick(config.language, "送出助攻", "supplies the assist"),
        }
    else:
        descriptions = {
            "cross": pick(config.language, "送出传中", "supplies the cross"),
            "through_ball": pick(config.language, "送出直塞", "supplies the through ball"),
            "pass": pick(config.language, "送出传球", "supplies the pass"),
        }
    detail = descriptions.get(facts.delivery)
    if not detail:
        return ""
    separator = " " if config.language == "en" else ""
    return f"{assistant_name}{separator}{detail}"


def _direction_text(facts: ESPNEventFacts, config: ESPNConfig) -> str:
    directions = {
        "bottom_left": pick(config.language, "球门左下角", "the bottom-left corner"),
        "bottom_right": pick(config.language, "球门右下角", "the bottom-right corner"),
        "top_left": pick(config.language, "球门左上角", "the top-left corner"),
        "top_right": pick(config.language, "球门右上角", "the top-right corner"),
        "high_centre": pick(config.language, "球门中路上方", "the high centre of the goal"),
        "centre": pick(config.language, "球门中路", "the centre of the goal"),
    }
    return directions.get(facts.shot_direction, "")


def _set_piece_context_text(facts: ESPNEventFacts, config: ESPNConfig) -> str:
    contexts = {
        "following_corner": pick(
            config.language,
            "此次攻门来自角球进攻",
            "The chance follows a corner",
        ),
        "direct_free_kick": pick(
            config.language,
            "此次攻门来自直接任意球",
            "The attempt comes directly from a free kick",
        ),
    }
    return contexts.get(facts.set_piece_location, "")


def _status_speech(status_code: str, config: ESPNConfig, casual: bool) -> str:
    if config.language == "en":
        professional = {
            "kickoff": "The match is underway",
            "halftime": "The referee signals half-time",
            "second_half": "The second half is underway",
            "end_regular": "Regulation time has ended",
            "end_extra": "Extra time has ended",
            "shootout_start": "The penalty shootout is underway",
            "shootout_end": "The penalty shootout is over",
            "full_time": "The referee signals full time",
        }
        friendly = {
            "kickoff": "Here we go, the match is underway",
            "halftime": "That's half-time; time to catch our breath",
            "second_half": "We're back—the second half is underway",
            "end_regular": "That's the end of regulation",
            "end_extra": "Extra time is done",
            "shootout_start": "Here comes the penalty shootout",
            "shootout_end": "The shootout is over",
            "full_time": "That's it—the match is over",
        }
    else:
        professional = {
            "kickoff": "比赛正式开球",
            "halftime": "裁判吹响半场结束哨",
            "second_half": "下半场开始",
            "end_regular": "常规时间结束",
            "end_extra": "加时赛结束",
            "shootout_start": "点球大战开始",
            "shootout_end": "点球大战结束",
            "full_time": "裁判吹响全场结束哨",
        }
        friendly = {
            "kickoff": "开踢啦，一起看球",
            "halftime": "半场结束，先喘口气",
            "second_half": "回来啦，下半场开踢",
            "end_regular": "常规时间踢完了",
            "end_extra": "加时赛也踢完了",
            "shootout_start": "刺激了，点球大战开始",
            "shootout_end": "点球大战结束了",
            "full_time": "比赛结束，最终结果出炉",
        }
    return (friendly if casual else professional).get(status_code, "")


def _contextual_score_speech(base: Alert, facts: ESPNEventFacts) -> str:
    score = facts.score_speech
    if not score:
        return ""
    if base.kind == "espn_status":
        if facts.status_code in {"full_time", "shootout_end"}:
            if "点球比分" in score:
                return f"最终，{score}"
            if "领先" in score:
                return score.replace("领先", "战胜", 1)
            if "打成" in score:
                teams, _, scoreline = score.partition("打成")
                return f"{teams.replace('和', '与', 1)}{scoreline}战平"
            return f"最终比分，{score}"
        if facts.status_code in {"halftime", "second_half"}:
            return f"半场比分，{score}"
        return score
    if base.kind == "espn_goal" or (
        base.kind == "espn_penalty" and facts.result == "scored"
    ):
        return score
    if base.kind in {"espn_drinks_break", "espn_drinks_break_end"}:
        return f"当前比分，{score}"
    return f"比分仍是{score}"


def _chinese_espn_speech(
    base: Alert,
    facts: ESPNEventFacts,
    config: ESPNConfig,
    style: str,
    perspective: EventPerspective,
) -> str:
    """Build natural Chinese from parsed facts, never from ESPN prose."""

    clock = _spoken_clock(facts, config)
    team = facts.awarded_team_name or facts.team_name or "场上球队"
    player = facts.primary_player_name
    if base.kind in {
        "espn_red_card",
        "espn_yellow_card",
        "espn_foul",
        "espn_substitution",
        "espn_woodwork",
        "espn_shot_saved",
        "espn_close_miss",
        "espn_shot_blocked",
    } or (base.kind == "espn_penalty" and facts.result != "scored"):
        # Nicknames are for successful/highlight moments.  Failed attacks,
        # disciplinary events, and personnel changes stay on the formal name.
        player = localized_player_name(config, facts.primary_player)
    actor = player or team or "场上球员"
    keeper = facts.goalkeeper_name or "门将"
    incoming = (
        localized_player_name(config, facts.incoming)
        if base.kind == "espn_substitution"
        else facts.incoming_name
    ) or "替补球员"
    outgoing = (
        localized_player_name(config, facts.outgoing)
        if base.kind == "espn_substitution"
        else facts.outgoing_name
    ) or "场上球员"
    attempt = _shot_attempt_text(facts, config) or "攻门"
    located_areas = {
        "six_yard_box",
        "centre_of_box",
        "left_of_box",
        "right_of_box",
        "outside_box",
        "far_post",
    }
    attempt_actor = f"{actor}{'在' if facts.shot_area in located_areas else ''}{attempt}"

    def open_play_delivery() -> str:
        if style == "professional":
            return ""
        delivery = _delivery_text(facts, config, assisted_goal=False)
        if not delivery:
            return ""
        return f"{delivery}，"

    def team_action(action: str) -> str:
        if not player:
            return f"{team}{action}"
        if style == "professional":
            return f"{team}球员{player}{action}"
        return f"{team}这边，{player}{action}"

    core = base.speech or ""
    extra_after_score = ""
    if base.kind == "espn_goal":
        if player:
            if facts.is_equalizer:
                action = "扳平比分"
            elif style == "casual" and perspective.support_outcome != "harm":
                action = "破门啦"
            elif style == "professional":
                action = "完成破门"
            else:
                action = "打进一球"
            core = f"{clock}{player}为{team}{action}"
        else:
            core = f"{clock}{team}{'扳平比分' if facts.is_equalizer else '取得进球'}"
        chant = render_star_chant(config, facts.primary_player, facts.team_name)
        if (
            chant
            and config.announce_player_names
            and style == "casual"
            and perspective.support_outcome == "benefit"
            and player in chant
        ):
            chant = chant if chant.endswith(("。", "！", "？", "!", "?")) else f"{chant}！"
            goal_is_explicit = any(
                phrase in chant for phrase in ("进球", "打进", "破门", "球进", "命中")
            )
            if not goal_is_explicit:
                core = join_sentences("zh", f"{clock}{chant}", f"{team}进球了")
            else:
                core = f"{clock}{chant}"
    elif base.kind == "espn_penalty":
        if facts.result == "scored":
            core = (
                f"{clock}{player}为{team}主罚点球并命中"
                if player
                else f"{clock}{team}点球命中"
            )
        elif facts.result == "saved":
            core = (
                f"{clock}{player}为{team}主罚的点球被{keeper}扑出"
                if player
                else f"{clock}{team}的点球被{keeper}扑出"
            )
        else:
            core = (
                f"{clock}{player}为{team}主罚点球未能命中"
                if player
                else f"{clock}{team}主罚点球未能命中"
            )
    elif base.kind == "espn_penalty_awarded":
        core = f"{clock}裁判判罚点球，{team}获得主罚机会"
        if facts.beneficiary_name:
            core += f"，{facts.beneficiary_name}在禁区内造点"
    elif base.kind == "espn_substitution":
        verb = "换人" if style != "professional" else "完成换人调整"
        core = f"{clock}{team}{verb}，{incoming}登场，换下{outgoing}"
        if facts.injury_reason:
            extra_after_score = f"{outgoing}因伤被换下"
    elif base.kind == "espn_woodwork":
        core = f"{clock}{team}这次进攻，{open_play_delivery()}{attempt_actor}击中门框"
    elif base.kind == "espn_shot_saved":
        core = f"{clock}{team}这次进攻，{open_play_delivery()}{attempt_actor}，被{keeper}扑出"
    elif base.kind == "espn_close_miss":
        result = "擦着门边偏出" if facts.close_miss else "偏出球门"
        core = f"{clock}{team}这次进攻，{open_play_delivery()}{attempt_actor}{result}"
    elif base.kind == "espn_shot_blocked":
        core = f"{clock}{team}这次进攻，{open_play_delivery()}{attempt_actor}被防守球员封堵"
    elif base.kind == "espn_red_card":
        if player:
            result = "两黄变一红" if facts.result == "second_yellow" else "吃到红牌"
            core = f"{clock}{player}{result}，{team}被罚下一人"
        else:
            core = f"{clock}{team}有球员被红牌罚下"
    elif base.kind == "espn_yellow_card":
        if style == "professional":
            core = (
                f"{clock}裁判向{team}球员{player}出示黄牌警告"
                if player
                else f"{clock}裁判向{team}一名球员出示黄牌"
            )
        else:
            core = f"{clock}{team_action('吃到黄牌')}"
    elif base.kind == "espn_corner":
        core = f"{clock}{team}{'赢得' if style == 'casual' else '获得'}一个角球"
    elif base.kind == "espn_opponent_free_kick":
        locations = {
            "attacking_half": "前场",
            "left_wing": "左路",
            "right_wing": "右路",
            "defensive_half": "后场",
        }
        location = locations.get(facts.set_piece_location, "")
        core = f"{clock}{team}赢得{location}任意球"
        if facts.beneficiary_name:
            core += f"，{facts.beneficiary_name}造到犯规"
    elif base.kind == "espn_foul":
        core = f"{clock}{team_action('犯规，裁判鸣哨')}"
    elif base.kind == "espn_drinks_break":
        core = "比赛进入补水时间，先歇口气"
    elif base.kind == "espn_drinks_break_end":
        core = "补水结束，比赛继续"
    elif base.kind == "espn_status":
        core = _status_speech(facts.status_code, config, casual=style == "casual") or core

    parts = [core, _contextual_score_speech(base, facts)]
    if style == "professional":
        assist = _delivery_text(
            facts,
            config,
            assisted_goal=base.kind == "espn_goal",
        )
        direction = _direction_text(facts, config)
        set_piece = _set_piece_context_text(facts, config)
        if base.kind == "espn_goal":
            finish: list[str] = []
            if assist:
                finish.append(assist)
            if facts.primary_player_name and _shot_attempt_text(facts, config):
                finish.append(
                    f"{facts.primary_player_name}{'在' if facts.shot_area else ''}{_shot_attempt_text(facts, config)}"
                )
            if direction:
                finish.append(f"皮球进入{direction}")
            if finish:
                parts.append("，".join(finish))
            if set_piece:
                parts.append(set_piece)
        elif base.kind in {
            "espn_woodwork",
            "espn_shot_saved",
            "espn_close_miss",
            "espn_shot_blocked",
        }:
            detail: list[str] = []
            if assist:
                detail.append(assist)
            if direction:
                detail.append(f"射门攻向{direction}")
            if detail:
                parts.append("，".join(detail))
            if set_piece:
                parts.append(set_piece)
    if extra_after_score:
        parts.append(extra_after_score)

    if base.kind in MAJOR_PERSPECTIVE_EVENTS:
        reaction = _major_perspective_reaction(perspective, config, style)
    else:
        reaction = _routine_support_reaction(base.kind, perspective, config, style)
    if reaction:
        parts.append(reaction)
    return join_sentences(config.language, *parts)


def _casual_espn_speech(base: Alert, facts: ESPNEventFacts, config: ESPNConfig) -> str:
    clock = _spoken_clock(facts, config)
    actor = _fact_actor(facts, config)
    team = facts.awarded_team_name or facts.team_name or pick(config.language, "一方", "one side")
    keeper = facts.goalkeeper_name or pick(config.language, "门将", "the goalkeeper")
    penalty_taker = facts.primary_player_name or facts.team_name or pick(
        config.language, "主罚球员", "the taker"
    )
    if config.language == "en" and facts.primary_player is not None:
        penalty_taker = localized_player_name(config, facts.primary_player)
    if config.language == "en":
        messages = {
            "espn_goal": f"{clock}{actor} puts it away! What a moment",
            "espn_woodwork": f"{clock}{actor} hits the woodwork—so close",
            "espn_shot_saved": f"{clock}{actor} gets the shot away, but {keeper} keeps it out",
            "espn_close_miss": (
                f"{clock}{actor} goes so close, but it stays out"
                if facts.close_miss
                else f"{clock}{actor} sends the shot off target"
            ),
            "espn_shot_blocked": f"{clock}{actor} shoots, and the defense blocks it",
            "espn_red_card": f"{clock}Big moment: {actor} is sent off",
            "espn_yellow_card": f"{clock}{actor} goes into the book",
            "espn_corner": f"{clock}{team} wins a corner",
            "espn_opponent_free_kick": f"{clock}{team} wins a free kick",
            "espn_foul": f"{clock}{actor} commits the foul",
            "espn_drinks_break": "Time for a drinks break; we'll be right back",
            "espn_drinks_break_end": "The drinks break is over; play resumes",
        }
    else:
        messages = {
            "espn_goal": f"{clock}家人们，{actor}把球送进去了！",
            "espn_woodwork": f"{clock}{actor}打中门框，真的就差一点！",
            "espn_shot_saved": f"{clock}{actor}这脚被{keeper}扑出来了！",
            "espn_close_miss": (
                f"{clock}{actor}攻门差一点，球没进！"
                if facts.close_miss
                else f"{clock}{actor}这脚射偏了。"
            ),
            "espn_shot_blocked": f"{clock}{actor}起脚，防守球员把球挡住了！",
            "espn_red_card": f"{clock}出大事了，{actor}被红牌罚下！",
            "espn_yellow_card": f"{clock}{actor}吃到黄牌！",
            "espn_corner": f"{clock}{team}拿到一个角球！",
            "espn_opponent_free_kick": f"{clock}{team}获得任意球！",
            "espn_foul": f"{clock}{actor}犯规，裁判吹哨了。",
            "espn_drinks_break": "比赛进入补水时间，先歇口气，马上回来。",
            "espn_drinks_break_end": "补水结束，继续看球！",
        }
    if base.kind == "espn_penalty":
        if facts.result == "scored":
            return pick(
                config.language,
                f"{clock}家人们，{actor}点球稳稳命中！",
                f"{clock}{actor} buries the penalty!",
            )
        if facts.result == "saved":
            return pick(
                config.language,
                f"{clock}{penalty_taker}的点球被{keeper}拒绝了！",
                f"{clock}{keeper} saves {penalty_taker}'s penalty!",
            )
        return pick(
            config.language,
            f"{clock}{actor}点球没罚进！",
            f"{clock}{actor} misses the penalty!",
        )
    if base.kind == "espn_penalty_awarded":
        beneficiary = facts.beneficiary_name
        extra = pick(
            config.language,
            f"，{beneficiary}制造了犯规" if beneficiary else "",
            f" after {beneficiary} draws the foul" if beneficiary else "",
        )
        return pick(
            config.language,
            f"{clock}裁判指向点球点，{team}获得点球{extra}！",
            f"{clock}The referee points to the spot—penalty to {team}{extra}!",
        )
    if base.kind == "espn_substitution":
        incoming = facts.incoming_name or pick(config.language, "一名球员", "a player")
        outgoing = facts.outgoing_name or pick(config.language, "一名球员", "a player")
        reason = pick(config.language, "，这次是因伤调整", " due to injury") if facts.injury_reason else ""
        return pick(
            config.language,
            f"{clock}{facts.team_name or team}换人，{incoming}上，{outgoing}下{reason}。",
            f"{clock}{facts.team_name or team} makes a change: {incoming} on for {outgoing}{reason}.",
        )
    if base.kind == "espn_status":
        return _status_speech(facts.status_code, config, casual=True) or (base.speech or "")
    return messages.get(base.kind, base.speech or "")


def _professional_espn_speech(base: Alert, facts: ESPNEventFacts, config: ESPNConfig) -> str:
    clock = _spoken_clock(facts, config)
    actor = _fact_actor(facts, config)
    team = facts.awarded_team_name or facts.team_name or pick(config.language, "一方", "one side")
    keeper = facts.goalkeeper_name or pick(config.language, "门将", "the goalkeeper")
    penalty_taker = facts.primary_player_name or facts.team_name or pick(
        config.language, "主罚球员", "the taker"
    )
    beneficiary_name = facts.beneficiary_name
    if config.language == "en" and facts.primary_player is not None:
        penalty_taker = localized_player_name(config, facts.primary_player)
    if config.language == "en" and facts.beneficiary is not None:
        beneficiary_name = localized_player_name(config, facts.beneficiary)
    attempt = _shot_attempt_text(facts, config)
    assist = _delivery_text(
        facts,
        config,
        assisted_goal=base.kind == "espn_goal",
    )
    direction = _direction_text(facts, config)
    set_piece_context = _set_piece_context_text(facts, config)

    def core_with_score(value: str) -> str:
        return join_sentences(config.language, value, facts.score_speech)

    if config.language == "en":
        attempt_with_article = attempt or "a shot"
        messages = {
            "espn_goal": (
                f"{clock}{actor} scores the equalizer"
                if facts.is_equalizer
                else f"{clock}{actor} scores"
            ),
            "espn_woodwork": f"{clock}{actor} hits the woodwork with {attempt_with_article}",
            "espn_shot_saved": f"{clock}{actor} tries {attempt_with_article}, but {keeper} makes the save",
            "espn_close_miss": (
                f"{clock}{actor} sends {attempt_with_article} narrowly wide"
                if facts.close_miss
                else f"{clock}{actor} sends {attempt_with_article} off target"
            ),
            "espn_shot_blocked": f"{clock}{actor} tries {attempt_with_article}, but the defense blocks it",
            "espn_red_card": (
                f"{clock}{actor} receives a second yellow and is sent off"
                if facts.result == "second_yellow"
                else f"{clock}{actor} is shown a red card and sent off"
            ),
            "espn_yellow_card": f"{clock}{actor} is cautioned",
            "espn_corner": f"{clock}{team} is awarded a corner",
            "espn_opponent_free_kick": f"{clock}{team} is awarded a free kick",
            "espn_foul": f"{clock}The referee calls a foul against {actor}",
            "espn_drinks_break": "The referee pauses play for a drinks break",
            "espn_drinks_break_end": "The drinks break concludes and play resumes",
        }
    else:
        messages = {
            "espn_goal": (
                f"{clock}{actor}取得进球，扳平比分"
                if facts.is_equalizer
                else f"{clock}{actor}取得进球"
            ),
            "espn_woodwork": f"{clock}{actor}{attempt or '攻门'}击中门框",
            "espn_shot_saved": f"{clock}{actor}{attempt or '射门'}，被{keeper}扑出",
            "espn_close_miss": (
                f"{clock}{actor}{attempt or '攻门'}稍稍偏出"
                if facts.close_miss
                else f"{clock}{actor}{attempt or '攻门'}射偏"
            ),
            "espn_shot_blocked": f"{clock}{actor}{attempt or '射门'}，被防守球员封堵",
            "espn_red_card": (
                f"{clock}{actor}累计两张黄牌，被红牌罚下"
                if facts.result == "second_yellow"
                else f"{clock}{actor}被红牌罚下"
            ),
            "espn_yellow_card": f"{clock}{actor}领到黄牌警告",
            "espn_corner": f"{clock}{team}获得角球",
            "espn_opponent_free_kick": f"{clock}{team}获得任意球",
            "espn_foul": f"{clock}{actor}犯规，裁判鸣哨判罚",
            "espn_drinks_break": "裁判示意比赛进入补水暂停",
            "espn_drinks_break_end": "补水暂停结束，比赛恢复",
        }
    if base.kind == "espn_goal":
        attempt_detail = ""
        if attempt:
            attempt_detail = pick(
                config.language,
                f"{facts.primary_player_name or actor}{'在' if facts.shot_area else ''}{attempt}",
                f"The finish is {attempt}",
            )
        detail_parts = [
            part for part in (assist, attempt_detail, set_piece_context) if part
        ]
        if direction:
            detail_parts.append(
                pick(config.language, f"皮球进入{direction}", f"The ball finds {direction}")
            )
        detail = join_sentences(config.language, *detail_parts) if detail_parts else ""
        return join_sentences(
            config.language,
            messages[base.kind],
            facts.score_speech,
            detail,
        )
    if base.kind == "espn_penalty":
        outcomes = {
            "scored": pick(
                config.language,
                f"{clock}{actor}主罚点球命中",
                f"{clock}{actor} converts the penalty",
            ),
            "saved": pick(
                config.language,
                f"{clock}{penalty_taker}主罚点球，被{keeper}扑出",
                f"{clock}{keeper} saves {penalty_taker}'s penalty",
            ),
            "missed": pick(
                config.language,
                f"{clock}{actor}主罚点球未能命中",
                f"{clock}{actor} misses the penalty",
            ),
        }
        return core_with_score(outcomes.get(facts.result, base.speech or ""))
    if base.kind == "espn_penalty_awarded":
        core = pick(
            config.language,
            f"{clock}裁判判罚点球，{team}获得主罚机会",
            f"{clock}The referee awards a penalty to {team}",
        )
        speech = core_with_score(core)
        if beneficiary_name:
            speech = join_sentences(
                config.language,
                speech,
                pick(
                    config.language,
                    f"{beneficiary_name}在禁区内制造犯规",
                    f"{beneficiary_name} draws the foul in the box",
                ),
            )
        return speech
    if base.kind == "espn_substitution":
        incoming = facts.incoming_name or pick(config.language, "一名球员", "a player")
        outgoing = facts.outgoing_name or pick(config.language, "一名球员", "a player")
        core = pick(
            config.language,
            f"{clock}{facts.team_name or team}完成换人，{incoming}替补登场，换下{outgoing}",
            f"{clock}{facts.team_name or team} makes a substitution: {incoming} replaces {outgoing}",
        )
        speech = core_with_score(core)
        if facts.injury_reason:
            speech = join_sentences(
                config.language,
                speech,
                pick(
                    config.language,
                    "此次换人原因为球员受伤",
                    "The change is due to injury",
                ),
            )
        return speech
    if base.kind == "espn_status":
        core = _status_speech(facts.status_code, config, casual=False) or (base.speech or "")
        return core_with_score(core)
    speech = core_with_score(messages.get(base.kind, base.speech or ""))
    if assist and base.kind in {
        "espn_woodwork",
        "espn_shot_saved",
        "espn_close_miss",
        "espn_shot_blocked",
    }:
        speech = join_sentences(config.language, speech, assist)
    if direction and base.kind in {
        "espn_shot_saved",
        "espn_close_miss",
        "espn_shot_blocked",
    }:
        speech = join_sentences(
            config.language,
            speech,
            pick(
                config.language,
                f"射门攻向{direction}",
                f"The attempt is directed toward {direction}",
            ),
        )
    if set_piece_context and base.kind in {
        "espn_woodwork",
        "espn_shot_saved",
        "espn_close_miss",
        "espn_shot_blocked",
    }:
        speech = join_sentences(config.language, speech, set_piece_context)
    if base.kind == "espn_opponent_free_kick":
        locations = {
            "attacking_half": pick(config.language, "前场", "in the attacking half"),
            "left_wing": pick(config.language, "左路", "on the left wing"),
            "right_wing": pick(config.language, "右路", "on the right wing"),
            "defensive_half": pick(config.language, "后场", "in the defensive half"),
        }
        location = locations.get(facts.set_piece_location, "")
        if config.language == "en":
            speech = core_with_score(f"{clock}{team} is awarded a free kick")
            if location:
                speech = join_sentences(
                    config.language,
                    speech,
                    f"The free kick is {location}",
                )
            if beneficiary_name:
                speech = join_sentences(
                    config.language,
                    speech,
                    f"{beneficiary_name} draws the foul",
                )
        else:
            speech = core_with_score(f"{clock}{team}获得任意球")
            if location:
                speech = join_sentences(
                    config.language,
                    speech,
                    f"任意球位置在{location}",
                )
            if beneficiary_name:
                speech = join_sentences(
                    config.language,
                    speech,
                    f"{beneficiary_name}制造犯规",
                )
    return speech


def render_espn_alert(
    alert: Alert,
    facts: ESPNEventFacts,
    config: ESPNConfig,
    snapshot: MatchSnapshot,
) -> Alert:
    """Apply the selected voice template while preserving alert behavior."""

    style = normalize_commentary_style(config.commentary_style)
    perspective = event_perspective(snapshot, config, facts, alert.kind)
    alert.balloon = _compact_espn_balloon(alert, facts, config)
    if config.language == "zh":
        alert.speech = _chinese_espn_speech(alert, facts, config, style, perspective)
    elif style == "casual":
        alert.speech = _casual_espn_speech(alert, facts, config)
    elif style == "professional":
        alert.speech = _professional_espn_speech(alert, facts, config)

    # English keeps the legacy templates and gets a score appended when needed.
    # Chinese already renders a context-aware score (lead/still level/final),
    # so appending the raw live-score phrase would duplicate or contradict it.
    speech = alert.speech or ""
    if (
        config.language == "en"
        and
        facts.score_speech
        and facts.score_speech.casefold() not in speech.casefold()
    ):
        alert.speech = join_sentences(config.language, speech, facts.score_speech)
    spoken_clock = _spoken_clock(facts, config)
    if spoken_clock and not (alert.speech or "").startswith(spoken_clock):
        alert.speech = f"{spoken_clock}{alert.speech or ''}"
    if config.language == "en":
        reaction = (
            _major_perspective_reaction(perspective, config, style)
            if alert.kind in MAJOR_PERSPECTIVE_EVENTS
            else _routine_support_reaction(alert.kind, perspective, config, style)
        )
        if reaction:
            alert.speech = join_sentences(config.language, alert.speech or "", reaction)
    return alert


def _balanced_alert_for_espn_commentary(
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
    status_code = _espn_status_code(play_type, lower_text)
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


def alert_for_espn_commentary(
    item: dict[str, Any],
    snapshot: MatchSnapshot,
    config: ESPNConfig,
) -> Alert | None:
    event_snapshot = snapshot_at_commentary_score(item, snapshot)
    facts = parse_espn_event_facts(item, event_snapshot, config)
    alert = _balanced_alert_for_espn_commentary(item, event_snapshot, config)
    return (
        render_espn_alert(alert, facts, config, event_snapshot)
        if alert is not None
        else None
    )


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
    alert = apply_final_result_reaction(alert, snapshot, config) if snapshot.status_state == "post" else alert
    status_clock = snapshot.status_detail.strip()
    normalized_clock = (
        status_clock.replace("'", "").replace("+", "").replace(":", "")
    )
    synthetic_item = {
        "text": "Match ends" if snapshot.status_state == "post" else "First Half begins",
        "time": {
            "displayValue": status_clock if normalized_clock.isdigit() else "",
        },
        "play": {"type": {"type": "full-time" if snapshot.status_state == "post" else "kickoff"}},
    }
    facts = parse_espn_event_facts(synthetic_item, snapshot, config)
    return render_espn_alert(alert, facts, config, snapshot)


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
    commentary = canonical_commentary_items(snapshot.commentary)
    keys = {commentary_key(item) for item in commentary}
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
        for item in commentary:
            play = item.get("play") or {}
            event_at = parse_datetime(play.get("wallclock"))
            if event_at is None or event_at.timestamp() < cutoff:
                continue
            alert = _alert_with_source(item, snapshot, config)
            if alert and alert.kind in critical_kinds:
                recent_critical_alerts.append(alert)
        return recent_critical_alerts

    new_items = [item for item in commentary if commentary_key(item) not in state.seen_commentary]
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
    style = normalize_commentary_style(
        config.commentary_style,
        path=f"markets[{config.ticker!r}].commentary_style",
    )
    if config.language == "en":
        direction = "up" if delta > 0 else "down"
        intensity = " sharply" if abs(delta) >= config.speak_move_cents else ""
        position_sentence = ""
        if config.tracks_position:
            position_sentence = (
                " This move benefits the current position."
                if delta > 0
                else " This move puts the current position under pressure."
            )
        if style == "casual":
            return (
                f"Heads up—{snapshot.label} {side_text(config.side_i_care)} just moved "
                f"{direction}{intensity} by {english_quantity(abs(delta), 'cent')} "
                f"to {english_quantity(mid, 'cent')}.{position_sentence}"
            )
        if style == "professional":
            move_direction = "upward" if delta > 0 else "downward"
            move_intensity = "sharp " if abs(delta) >= config.speak_move_cents else ""
            return (
                f"{snapshot.label} {side_text(config.side_i_care)} midpoint is now "
                f"{english_quantity(mid, 'cent')}, a {move_intensity}{move_direction} move of "
                f"{english_quantity(abs(delta), 'cent')} from the previous alert baseline."
                f"{position_sentence}"
            )
        return (
            f"{snapshot.label} {side_text(config.side_i_care)} midpoint is "
            f"{english_quantity(mid, 'cent')}, {direction}{intensity} by "
            f"{english_quantity(abs(delta), 'cent')} since the last alert."
            f"{position_sentence}"
        )
    direction = "涨了" if delta > 0 else "跌了"
    intensity = "大幅" if abs(delta) >= config.speak_move_cents else ""
    position_impact = ""
    if config.tracks_position:
        position_impact = "这波对持仓有利" if delta > 0 else "这波让持仓承压"
    position_suffix = f"{position_impact}。" if position_impact else ""
    if style == "casual":
        return (
            f"提醒一下，{snapshot.label} {side_text(config.side_i_care)} "
            f"刚刚{intensity}{direction}{abs(delta)}分，来到{mid}分。{position_suffix}"
        )
    if style == "professional":
        position_sentence = ""
        if config.tracks_position:
            position_sentence = "当前持仓受益。" if delta > 0 else "当前持仓承压。"
        return (
            f"{snapshot.label} {side_text(config.side_i_care)} 中间价"
            f"{'升' if delta > 0 else '降'}至{mid}分，较上次变动{abs(delta)}分。"
            f"{position_sentence}"
        )
    return (
        f"{snapshot.label} {side_text(config.side_i_care)} 现为{mid}分，"
        f"较上次{intensity}{direction}{abs(delta)}分。{position_suffix}"
    )


def speech_for_market_goal_signal(
    snapshot: MarketSnapshot,
    config: MarketConfig,
    rising: bool,
    rapid_delta: int,
    mid: int,
) -> str:
    """Render a suspected-goal market jump without upgrading uncertainty."""

    style = normalize_commentary_style(
        config.commentary_style,
        path=f"markets[{config.ticker!r}].commentary_style",
    )
    configured = config.goal_signal_up_speech if rising else config.goal_signal_down_speech
    configured_team = config.goal_signal_up_team if rising else config.goal_signal_down_team
    goal_team = configured_team or (
        snapshot.label if rising else pick(config.language, "对手方", "the opposing side")
    )

    def add_view_context(speech: str) -> str:
        known_goal_teams = {
            name.casefold()
            for name in (config.goal_signal_up_team, config.goal_signal_down_team)
            if name
        }
        support_known = bool(
            config.favorite_team
            and config.favorite_team.casefold() in known_goal_teams
        )
        support_good = bool(
            support_known
            and config.favorite_team.casefold() == goal_team.casefold()
        )
        position_known = bool(config.tracks_position and config.position_team)
        position_good = rising
        if config.language == "en":
            if support_known and position_known:
                if support_good == position_good:
                    note = (
                        "If confirmed, both the supported team and the position benefit"
                        if support_good
                        else "If confirmed, both the supported team and the position suffer"
                    )
                else:
                    note = (
                        "If confirmed, that helps the supported team but hurts the position"
                        if support_good
                        else "If confirmed, that hurts the supported team but helps the position"
                    )
            elif support_known:
                note = (
                    "If confirmed, that helps the supported team"
                    if support_good
                    else "If confirmed, that hurts the supported team"
                )
            elif position_known:
                note = (
                    "If confirmed, the current position benefits"
                    if position_good
                    else "If confirmed, the current position comes under pressure"
                )
            else:
                note = ""
        else:
            if style == "casual":
                if support_known and position_known:
                    if support_good == position_good:
                        note = (
                            "要是真的，咱们支持的球队和仓位都舒服了"
                            if support_good
                            else "要是真的，支持的球队和仓位可都难受了"
                        )
                    else:
                        note = (
                            "要是真的，球迷开心，仓位可要承压"
                            if support_good
                            else "要是真的，心里难受，不过仓位倒是受益"
                        )
                elif support_known:
                    note = "要是真的，咱们支持的球队就舒服了" if support_good else "要是真的，支持方可不好受"
                elif position_known:
                    note = "要是真的，这波对仓位有利" if position_good else "要是真的，这波仓位要承压"
                else:
                    note = ""
            else:
                if support_known and position_known:
                    if support_good == position_good:
                        note = (
                            "如果属实，支持的球队和仓位都会受益"
                            if support_good
                            else "如果属实，支持的球队和仓位都会受损"
                        )
                    else:
                        note = (
                            "如果属实，球迷这边开心，仓位却会承压"
                            if support_good
                            else "如果属实，感情上不好受，不过仓位会受益"
                        )
                elif support_known:
                    note = "如果属实，这是支持方的好消息" if support_good else "如果属实，这对支持方不利"
                elif position_known:
                    note = "如果属实，当前仓位会受益" if position_good else "如果属实，当前仓位会承压"
                else:
                    note = ""
        return join_sentences(config.language, speech, note) if note else speech

    def ensure_uncertainty(speech: str) -> str:
        lower = speech.casefold()

        def has_unqualified_claim() -> bool:
            boundaries = ".?!。！？;；\n"

            def clause_before(source: str, index: int) -> str:
                prefix = source[:index].rstrip()
                boundary = max((prefix.rfind(mark) for mark in boundaries), default=-1)
                return prefix[boundary + 1 :].strip()

            if config.language == "en":
                qualifiers = ("may have", "might have", "possibly")
                for claim in ("scored", "scores"):
                    start = 0
                    while True:
                        index = lower.find(claim, start)
                        if index < 0:
                            break
                        prefix = clause_before(lower, index)
                        if not prefix.endswith(qualifiers):
                            return True
                        start = index + len(claim)
                return "confirmed goal" in lower
            qualifiers = ("可能", "疑似")
            for claim in ("进球了", "进球！", "进球。", "破门了", "球进了"):
                start = 0
                while True:
                    index = speech.find(claim, start)
                    if index < 0:
                        break
                    prefix = clause_before(speech, index)
                    if not prefix.endswith(qualifiers):
                        return True
                    start = index + len(claim)
            return "确认进球" in speech or "已经进球" in speech

        if config.language == "en":
            uncertain = any(word in lower for word in ("possible", "may", "might", "suspected"))
            awaiting = (
                "commentary" in lower
                and "confirm" in lower
                and any(word in lower for word in ("wait", "await"))
            )
            safe = (
                f"Market move: possible goal for {goal_team}; "
                "awaiting commentary confirmation."
            )
            confirmation_suffix = "Still awaiting commentary confirmation."
        else:
            uncertain = "疑似" in speech or "可能" in speech
            awaiting = (
                "文字直播" in speech
                and "确认" in speech
                and ("等" in speech or "待" in speech)
            )
            safe = (
                f"盘口突然{'拉升' if rising else '跳水'}！"
                f"{goal_team}这边很可能进球了！先等文字直播确认。"
            )
            confirmation_suffix = "仍需等待文字直播确认。"
        if not uncertain or has_unqualified_claim():
            return add_view_context(safe)
        if awaiting:
            return add_view_context(speech)
        return add_view_context(join_sentences(config.language, speech, confirmation_suffix))

    if style == "balanced" and configured:
        return ensure_uncertainty(configured)
    if config.language == "en":
        direction = "jumped" if rising else "dropped"
        if style == "casual":
            return add_view_context(
                f"Heads up—{snapshot.label} just {direction} {abs(rapid_delta)} cents "
                f"to {mid}. Possible goal, but hold the celebration; awaiting commentary confirmation."
                f" The signal points to {goal_team}."
            )
        if style == "professional":
            return add_view_context(
                f"{snapshot.label} shows a rapid {'upward' if rising else 'downward'} move of "
                f"{abs(rapid_delta)} cents to {mid}. This is only a possible goal signal; "
                f"it points to {goal_team}, awaiting commentary confirmation."
            )
        return add_view_context(
            f"The market just {direction} sharply. Possible goal for {goal_team}; "
            "awaiting commentary confirmation."
        )
    if style == "casual":
        return add_view_context(
            f"盘口突然{'拉升' if rising else '跳水'}！"
            f"{goal_team}这边很可能进球了！"
            "先别眨眼，等文字直播确认！"
        )
    if style == "professional":
        return add_view_context(
            f"盘口快速{'上行' if rising else '下挫'}！{snapshot.label}在短时间内"
            f"变动{abs(rapid_delta)}分至{mid}分，{goal_team}进球概率骤升，"
            "但目前仍是疑似。等待文字直播确认！"
        )
    return add_view_context(
        f"盘口突然{'拉升' if rising else '跳水'}！"
        f"{goal_team}这边很可能进球了！先等文字直播确认。"
    )


VENUE_DISPLAY_NAMES = {"kalshi": "Kalshi", "polymarket": "Polymarket"}
# A second venue jumping within this window of a goal signal counts as
# confirmation; beyond it the moves are probably unrelated drift.
GOAL_SIGNAL_CORROBORATION_WINDOW_SECONDS = 90.0
VENUE_DIVERGENCE_COOLDOWN_SECONDS = 600.0


def venue_divergence_alert(
    config: WatchConfig,
    divergence: VenueDivergence,
    label: str,
) -> Alert:
    """Informational fact: two venues disagree on the same outcome.

    Deliberately neutral wording — surfacing the gap is fine, steering the
    user toward either side is not (PRD non-goal).
    """
    quote_a, quote_b = divergence.quote_a, divergence.quote_b
    name_a = VENUE_DISPLAY_NAMES.get(quote_a.venue, quote_a.venue)
    name_b = VENUE_DISPLAY_NAMES.get(quote_b.venue, quote_b.venue)
    percent_a = int(round(quote_a.prob_mid * 100))
    percent_b = int(round(quote_b.prob_mid * 100))
    return Alert(
        ticker=quote_a.market_id,
        label=label,
        kind="venue_divergence",
        priority=60,
        face="surprise",
        balloon=pick(
            config.language,
            f"{label} | 平台分歧 | {name_a} {percent_a}% vs {name_b} {percent_b}%",
            f"{label} | Venue split | {name_a} {percent_a}% vs {name_b} {percent_b}%",
        ),
        speech=pick(
            config.language,
            f"两个平台对{label}的看法出现分歧：{name_a}给到百分之{percent_a}，"
            f"{name_b}给到百分之{percent_b}。市场还没统一意见。",
            f"The venues disagree on {label}: {name_a} says {percent_a} percent, "
            f"{name_b} says {percent_b} percent. The market hasn't made up its mind.",
        ),
        detail=(
            f"venue divergence {quote_a.venue}={quote_a.prob_mid:.2f} "
            f"{quote_b.venue}={quote_b.prob_mid:.2f} gap={divergence.gap:.2f}"
        ),
        spoiler_sensitive=True,
    )


def corroborate_goal_signal_alert(alert: Alert, config: WatchConfig) -> Alert:
    """Upgrade a single-venue goal signal after a second venue moved with it.

    One venue jumping keeps the existing "awaiting confirmation" wording;
    two venues jumping the same way at the same time is treated as a
    high-confidence event (PRD section 4.2, multi-source confirmation).
    """
    boost = pick(
        config.language,
        "两个平台同时跳动，这个信号可信度很高！",
        "Both venues jumped together—this signal looks solid!",
    )
    speech = join_sentences(config.language, alert.speech, boost) if alert.speech else boost
    return replace(
        alert,
        priority=960,
        balloon=pick(
            config.language,
            f"双平台确认 | {alert.balloon}",
            f"Dual-venue | {alert.balloon}",
        ),
        speech=speech,
        detail=f"{alert.detail}; corroborated by polymarket",
    )


def bar_polymarket_ref(config: WatchConfig) -> PolymarketMarketRef | None:
    """The Gamma market paired with the probability bar, if fully configured."""
    bar = config.probability_bar
    if not (config.polymarket.enabled and bar.enabled and bar.polymarket_market_id):
        return None
    if not bar.polymarket_left_outcome or not bar.polymarket_right_outcome:
        return None
    return PolymarketMarketRef(
        market_id=bar.polymarket_market_id,
        outcomes={
            "left": bar.polymarket_left_outcome,
            "right": bar.polymarket_right_outcome,
        },
    )


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
                spoiler_sensitive=True,
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
                    spoiler_sensitive=True,
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
                    spoiler_sensitive=True,
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
            configured_team = (
                config.goal_signal_up_team if rising else config.goal_signal_down_team
            )
            goal_team = configured_team or (
                snapshot.label
                if rising
                else pick(config.language, "对手方", "the opposing side")
            )
            speech = speech_for_market_goal_signal(
                snapshot,
                config,
                rising,
                rapid_delta,
                mid,
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
                        f"盘口突变 {fmt_delta(rapid_delta)} | {goal_team}疑似进球，等待确认",
                        f"Market move {fmt_delta(rapid_delta)} | Possible {goal_team} goal; awaiting confirmation",
                    ),
                    speech=speech,
                    detail=(
                        f"rapid {side_text(config.side_i_care)} move "
                        f"{previous_observed_mid}c -> {mid}c"
                    ),
                    clip_id="odds-up" if rising else "odds-down",
                    prefer_dynamic_voice=True,
                    spoiler_sensitive=True,
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
                        spoiler_sensitive=True,
                    )
                )

    state.last_observed_mid_cents = mid
    state.last_yes_spread_cents = current_spread
    state.last_status = snapshot.status
    return alerts


def apply_spoiler_policy_to_market_alerts(
    enabled: bool,
    alerts: list[Alert],
    snapshot: MarketSnapshot,
    market: MarketConfig,
    state: MarketState,
    now: datetime,
) -> list[Alert]:
    """Consume hidden market moves while retaining non-spoiler market events.

    The alert baseline follows every protected poll, not merely moves large
    enough to raise an alert. Turning protection off therefore starts from the
    latest visible market state instead of replaying an accumulated move.
    """

    if not enabled:
        return alerts
    sensitive_alerts = [alert for alert in alerts if alert.spoiler_sensitive]
    mid = snapshot.implied_probability(market.side_i_care)
    if mid is not None:
        state.last_alert_mid_cents = mid
    if sensitive_alerts:
        state.last_alert_at = now.timestamp()
    return [alert for alert in alerts if not alert.spoiler_sensitive]


def consume_spoiler_market_baselines(
    config: WatchConfig,
    snapshots: dict[str, MarketSnapshot],
    states: dict[str, MarketState],
    now: datetime,
) -> None:
    """Consume the latest passive market state when protection is enabled.

    This closes the small toggle-between-polls window: a queued move must not
    become a fresh catch-up alert if protection is enabled and then disabled
    before the next Kalshi response arrives.
    """

    for market in config.markets:
        snapshot = snapshots.get(market.ticker)
        state = states.get(market.ticker)
        if snapshot is None or state is None:
            continue
        mid = snapshot.implied_probability(market.side_i_care)
        if mid is not None:
            state.last_alert_mid_cents = mid
            state.last_observed_mid_cents = mid
        state.last_yes_spread_cents = snapshot.yes_spread()
        state.last_status = snapshot.status
        if (
            snapshot.status.lower() not in CLOSED_STATUSES
            and snapshot.close_time is not None
            and 0 < (snapshot.close_time - now).total_seconds()
            <= market.near_close_minutes * 60
        ):
            state.near_close_alerted = True


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


def purge_spoiler_sensitive_alerts(queue: list[QueuedAlert]) -> list[QueuedAlert]:
    """Drop protected alerts that have not started delivery yet."""

    return [item for item in queue if not item.alert.spoiler_sensitive]


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
    timeout = (
        STACKCHAN_SPEECH_TIMEOUT_SECONDS
        if command.lstrip().lower().startswith("say ")
        else STACKCHAN_COMMAND_TIMEOUT_SECONDS
    )
    with STACKCHAN_DEVICE_HTTP_LOCK:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            res.read()


def post_json(
    url: str,
    payload: dict[str, Any],
    *,
    timeout: float = STACKCHAN_COMMAND_TIMEOUT_SECONDS,
) -> dict[str, Any]:
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
        with urllib.request.urlopen(request, timeout=timeout) as response:
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
            "commentary_style": str(
                current.get("commentary_style") or config.espn.commentary_style
            ),
            "spoiler_free_mode": normalize_spoiler_free_mode(
                current.get("spoiler_free_mode", config.spoiler_free_mode)
            ),
            "options": options,
            "current": current,
        },
    )


def sync_device_commentary_style(config: WatchConfig, style: str) -> bool:
    """Best-effort effective-style sync without touching setup options or poll state."""

    if config.stackchan_transport != "http":
        return True
    try:
        post_json(
            f"http://{config.stackchan_host}/api/match-setup/options",
            {"commentary_style": normalize_commentary_style(style)},
            timeout=2,
        )
        return True
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        return False


def sync_device_spoiler_free_mode(config: WatchConfig, enabled: Any) -> bool:
    """Best-effort anti-spoiler sync without replacing match setup options."""

    if config.stackchan_transport != "http":
        return True
    try:
        post_json(
            f"http://{config.stackchan_host}/api/match-setup/spoiler",
            {"spoiler_free_mode": normalize_spoiler_free_mode(enabled)},
            timeout=2,
        )
        return True
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        return False


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
        time.sleep(STACKCHAN_FEEDBACK_POLL_SECONDS)
    print(f"warning: Stack-chan feedback still busy after {timeout:g}s", file=sys.stderr)
    return False


def send_alert(config: WatchConfig, alert: Alert, quiet: bool, dry_run: bool, no_say: bool) -> bool:
    # Defense in depth for alerts queued before a live preference change. The
    # watcher also purges them eagerly, but the delivery boundary must never
    # emit protected market information while the mode is active.
    if config.spoiler_free_mode and alert.spoiler_sensitive:
        return True
    timeout_ms = config.alert_balloon_seconds * 1000
    commands = [f"face {alert.face}"]
    deferred_stackchan_speech = ""
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
        commands.append(f"celebrate goal {red} {green} {blue}")
        if use_dynamic_voice:
            deferred_stackchan_speech = alert.speech or ""
    elif use_result_celebration:
        red, green, blue = alert.light_rgb or (255, 255, 255)
        outcome = alert.celebration.removeprefix("result-")
        commands.append(f"celebrate result {outcome} {red} {green} {blue}")
        if alert.speech and config.result_speech_commands:
            deferred_stackchan_speech = alert.speech
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
    celebration_settled = True
    if uses_celebration and not dry_run:
        # Keep LAN TTS out of the MOD's asynchronous celebration window. The
        # local fanfare and head motion finish first; the blocking `say` request
        # below then owns the only active watcher/device HTTP exchange while
        # the WAV stream is open.
        celebration_settled = wait_for_stackchan_feedback_idle(
            config,
            include_light=not bool(deferred_stackchan_speech),
            report_last_error=True,
        )
    if deferred_stackchan_speech and (dry_run or celebration_settled):
        try:
            send_stackchan_commands(
                config,
                [f"say {deferred_stackchan_speech}"],
                dry_run=dry_run,
            )
        except (urllib.error.URLError, OSError, RuntimeError) as error:
            # The balloon, fanfare, and motion were already delivered. Do not
            # retry the full goal/result and replay it just because TTS failed.
            print(f"warning: deferred Stack-chan speech failed: {error}", file=sys.stderr)
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


def venue_quote_from_snapshot(snapshot: MarketSnapshot, outcome: str) -> VenueQuote:
    """Bridge the watcher's Kalshi snapshot into the aggregation model."""
    status_lower = snapshot.status.lower()
    if status_lower in {"settled", "finalized", "determined"} or snapshot.result:
        status = "settled"
    elif status_lower in {"closed", "inactive"}:
        status = "closed"
    elif status_lower == "paused":
        status = "paused"
    else:
        status = "open"
    implied = snapshot.implied_probability("yes")
    return VenueQuote(
        venue="kalshi",
        market_id=snapshot.ticker,
        outcome=outcome,
        prob_mid=implied / 100 if implied is not None else None,
        bid=snapshot.yes_bid_cents / 100 if snapshot.yes_bid_cents is not None else None,
        ask=snapshot.yes_ask_cents / 100 if snapshot.yes_ask_cents is not None else None,
        volume_usd=_optional_float(snapshot.volume_24h),
        liquidity_usd=snapshot.liquidity_usd,
        status=status,
        close_time=snapshot.close_time,
        fetched_at=datetime.now(timezone.utc),
    )


def probability_bar_command(
    config: WatchConfig,
    snapshots: dict[str, MarketSnapshot],
    venue_quotes: dict[str, list[VenueQuote]] | None = None,
) -> str:
    bar = config.probability_bar
    if not bar.enabled:
        return ""
    extra = venue_quotes or {}
    snapshot = snapshots.get(bar.market_ticker)
    if bar.mode == "normalized_outcomes":
        left_quotes = [venue_quote_from_snapshot(snapshot, "left")] if snapshot else []
        left_quotes.extend(extra.get("left") or [])
        right_snapshot = snapshots.get(bar.right_market_ticker)
        right_quotes = (
            [venue_quote_from_snapshot(right_snapshot, "right")] if right_snapshot else []
        )
        right_quotes.extend(extra.get("right") or [])
        left_prob = aggregate_probability(left_quotes)
        right_prob = aggregate_probability(right_quotes)
        if left_prob is None or right_prob is None:
            return ""
        total = left_prob + right_prob
        if total <= 0:
            return ""
        left_percent = int(math.floor((left_prob * 100 / total) + 0.5))
    else:
        if snapshot is None:
            return ""
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


def persistent_display_command(
    config: WatchConfig,
    snapshots: dict[str, MarketSnapshot],
    venue_quotes: dict[str, list[VenueQuote]] | None = None,
) -> str:
    probability_command = probability_bar_command(config, snapshots, venue_quotes)
    if probability_command:
        return probability_command
    if not config.ticker_enabled:
        return ""
    message = ticker_text(config, snapshots)
    return f"ticker {message}" if message else ""


def send_ticker(
    config: WatchConfig,
    snapshots: dict[str, MarketSnapshot],
    dry_run: bool,
    venue_quotes: dict[str, list[VenueQuote]] | None = None,
) -> str:
    command = persistent_display_command(config, snapshots, venue_quotes)
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
        if not config.spoiler_free_mode and not no_say and not quiet and speech:
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
    if not args.dry_run:
        for warning in validate_registry_venues(config):
            print(f"warning: {warning}", file=sys.stderr)
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
    pending_device_style_sync: str | None = None
    next_device_style_sync_at = 0.0
    pending_device_spoiler_sync: bool | None = None
    next_device_spoiler_sync_at = 0.0
    delivery_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="stackchan-delivery")
    delivery_future: Future[bool] | None = None
    delivery_item: QueuedAlert | None = None
    venue_quotes_by_side: dict[str, list[VenueQuote]] = {}
    poly_prev_left_prob: float | None = None
    poly_last_jump_direction = 0
    poly_last_jump_at = 0.0
    poly_failures = 0
    next_poly_poll_at = 0.0
    last_divergence_alert_at = 0.0
    espn_description = f"; ESPN event={config.espn.event_id}" if config.espn.enabled else ""
    poly_description = (
        f"; Polymarket market={config.probability_bar.polymarket_market_id}"
        if bar_polymarket_ref(config) is not None
        else ""
    )
    print(
        f"watching {len(config.markets)} Kalshi markets; Kalshi poll={config.poll_seconds}s"
        f"; ESPN poll={config.espn.poll_seconds}s{espn_description}{poly_description}",
        flush=True,
    )
    try:
        while True:
            now = datetime.now(timezone.utc)
            cycle_monotonic = time.monotonic()
            quiet = in_quiet_hours(config.quiet_hours)

            if setup_service:
                commentary_style_update = setup_service.take_commentary_style_update()
                if commentary_style_update:
                    apply_live_commentary_style(config, commentary_style_update)
                    pending_device_style_sync = commentary_style_update
                    if sync_device_commentary_style(config, commentary_style_update):
                        pending_device_style_sync = None
                    else:
                        next_device_style_sync_at = cycle_monotonic + 5
                    print(
                        f"commentary style applied: {commentary_style_update}",
                        flush=True,
                    )
                spoiler_free_mode_update = setup_service.take_spoiler_free_mode_update()
                if spoiler_free_mode_update is not None:
                    enabled = apply_live_spoiler_free_mode(
                        config,
                        spoiler_free_mode_update,
                    )
                    if enabled:
                        consume_spoiler_market_baselines(
                            config,
                            snapshots,
                            states,
                            now,
                        )
                        alert_queue = purge_spoiler_sensitive_alerts(alert_queue)
                    pending_device_spoiler_sync = enabled
                    if sync_device_spoiler_free_mode(config, enabled):
                        pending_device_spoiler_sync = None
                    else:
                        next_device_spoiler_sync_at = cycle_monotonic + 5
                    print(
                        f"spoiler-free mode applied: {str(enabled).lower()}",
                        flush=True,
                    )

            if (
                pending_device_style_sync
                and cycle_monotonic >= next_device_style_sync_at
            ):
                if sync_device_commentary_style(config, pending_device_style_sync):
                    pending_device_style_sync = None
                else:
                    next_device_style_sync_at = cycle_monotonic + 5

            if (
                pending_device_spoiler_sync is not None
                and cycle_monotonic >= next_device_spoiler_sync_at
            ):
                if sync_device_spoiler_free_mode(config, pending_device_spoiler_sync):
                    pending_device_spoiler_sync = None
                else:
                    next_device_spoiler_sync_at = cycle_monotonic + 5

            if setup_service and setup_service.take_reload_requested():
                previous_dynamic_voice = config.dynamic_voice_commands
                previous_result_commands = config.result_celebration_commands
                previous_result_speech_commands = config.result_speech_commands
                previous_setup_commands = config.setup_qr_commands
                try:
                    reloaded = load_config(config_path)
                    validate_config(reloaded, dry_run=args.dry_run)
                except ConfigError as error:
                    # A bad edit (typoed registry pointer, unconfirmed entry)
                    # must not kill a running watch; keep the old config.
                    print(
                        f"warning: reload rejected, keeping previous config: {error}",
                        file=sys.stderr,
                    )
                    continue
                config = reloaded
                if not args.dry_run:
                    for warning in validate_registry_venues(config):
                        print(f"warning: {warning}", file=sys.stderr)
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
                pending_device_style_sync = None
                pending_device_spoiler_sync = None
                last_poll_tier = ""
                venue_quotes_by_side = {}
                poly_prev_left_prob = None
                poly_last_jump_direction = 0
                poly_last_jump_at = 0.0
                poly_failures = 0
                next_poly_poll_at = 0.0
                last_divergence_alert_at = 0.0
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
                    delivery_item.retries += 1
                    retry_limit = CRITICAL_ALERT_RETRY_LIMITS.get(
                        delivery_item.alert.kind, DEFAULT_ALERT_RETRY_LIMIT
                    )
                    if delivery_item.retries > retry_limit:
                        print(
                            f"delivery dropped after {retry_limit} retries: {delivery_item.alert.kind}",
                            flush=True,
                        )
                    else:
                        delivery_item.not_before = cycle_monotonic + ALERT_RETRY_BACKOFF_SECONDS
                        alert_queue.append(delivery_item)
                        print(
                            f"delivery retry queued: {delivery_item.alert.kind} "
                            f"(attempt {delivery_item.retries}/{retry_limit}, "
                            f"backoff {ALERT_RETRY_BACKOFF_SECONDS:g}s)",
                            flush=True,
                        )
                delivery_future = None
                delivery_item = None

            pending: list[PendingAlertContext] = []

            if setup_service and cycle_monotonic >= next_setup_pending_poll_at:
                next_setup_pending_poll_at = cycle_monotonic + MATCH_SETUP_PENDING_POLL_SECONDS
                try:
                    setup_request = fetch_device_match_setup_pending(config)
                    if setup_request:
                        request_id = str(setup_request.get("request_id") or "")
                        has_match_selection = any(
                            setup_request.get(key)
                            for key in (
                                "kalshi_url",
                                "event_ticker",
                                "espn_event_id",
                            )
                        )
                        spoiler_only_request = bool(
                            setup_request.get("spoiler_only")
                        ) or (
                            "spoiler_free_mode" in setup_request
                            and not has_match_selection
                            and "commentary_style" not in setup_request
                        )
                        style_only_request = bool(setup_request.get("style_only")) or (
                            "commentary_style" in setup_request
                            and not has_match_selection
                            and "spoiler_free_mode" not in setup_request
                        )
                        acknowledgement = setup_acknowledgements.get(request_id)
                        if acknowledgement is None:
                            try:
                                standalone_request = bool(setup_request.get("standalone")) or (
                                    bool(setup_request.get("kalshi_url"))
                                    and not setup_request.get("espn_event_id")
                                )
                                if spoiler_only_request:
                                    result = setup_service.apply_spoiler_free_mode(
                                        setup_request
                                    )
                                    enabled = apply_live_spoiler_free_mode(
                                        config,
                                        result["spoiler_free_mode"],
                                    )
                                    if enabled:
                                        consume_spoiler_market_baselines(
                                            config,
                                            snapshots,
                                            states,
                                            now,
                                        )
                                        alert_queue = purge_spoiler_sensitive_alerts(
                                            alert_queue
                                        )
                                    # The device already holds this value; consume
                                    # the local service notification without
                                    # echoing it back through the relay.
                                    setup_service.take_spoiler_free_mode_update()
                                elif style_only_request:
                                    result = setup_service.apply_commentary_style(setup_request)
                                    apply_live_commentary_style(
                                        config,
                                        result["commentary_style"],
                                    )
                                    # The device relay is already running on the
                                    # watcher thread, so consume the service signal
                                    # now rather than reapplying it next cycle.
                                    setup_service.take_commentary_style_update()
                                elif standalone_request:
                                    result = setup_service.apply_market_selection(setup_request)
                                else:
                                    result = setup_service.apply_selection(setup_request)
                                if (
                                    not spoiler_only_request
                                    and "spoiler_free_mode" in result
                                ):
                                    enabled = apply_live_spoiler_free_mode(
                                        config,
                                        result["spoiler_free_mode"],
                                    )
                                    if enabled:
                                        consume_spoiler_market_baselines(
                                            config,
                                            snapshots,
                                            states,
                                            now,
                                        )
                                        alert_queue = purge_spoiler_sensitive_alerts(
                                            alert_queue
                                        )
                                acknowledgement = {
                                    "request_id": request_id,
                                    "ok": True,
                                }
                                if result.get("commentary_style") is not None:
                                    acknowledgement["commentary_style"] = result[
                                        "commentary_style"
                                    ]
                                if result.get("spoiler_free_mode") is not None:
                                    acknowledgement["spoiler_free_mode"] = result[
                                        "spoiler_free_mode"
                                    ]
                                if result.get("label") is not None:
                                    acknowledgement["label"] = result["label"]
                                if result.get("language") is not None:
                                    acknowledgement["language"] = result["language"]
                            except (OSError, ValueError, json.JSONDecodeError, urllib.error.URLError) as error:
                                acknowledgement = {
                                    "request_id": request_id,
                                    "ok": False,
                                    "error": str(error),
                                }
                                print(
                                    f"match setup apply failed ({request_id}): {error}",
                                    flush=True,
                                )
                            setup_acknowledgements[request_id] = acknowledgement
                        acknowledge_device_match_setup(config, acknowledgement)
                        if style_only_request and acknowledgement.get("ok"):
                            pending_device_style_sync = None
                        if spoiler_only_request and acknowledgement.get("ok"):
                            pending_device_spoiler_sync = None
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
                    if setup_service:
                        style_after_fetch = setup_service.take_commentary_style_update()
                        if style_after_fetch:
                            apply_live_commentary_style(config, style_after_fetch)
                            pending_device_style_sync = style_after_fetch
                            if sync_device_commentary_style(config, style_after_fetch):
                                pending_device_style_sync = None
                            else:
                                next_device_style_sync_at = time.monotonic() + 5
                        spoiler_after_fetch = (
                            setup_service.take_spoiler_free_mode_update()
                        )
                        if spoiler_after_fetch is not None:
                            enabled = apply_live_spoiler_free_mode(
                                config,
                                spoiler_after_fetch,
                            )
                            if enabled:
                                consume_spoiler_market_baselines(
                                    config,
                                    snapshots,
                                    states,
                                    now,
                                )
                                alert_queue = purge_spoiler_sensitive_alerts(
                                    alert_queue
                                )
                            pending_device_spoiler_sync = enabled
                            if sync_device_spoiler_free_mode(config, enabled):
                                pending_device_spoiler_sync = None
                            else:
                                next_device_spoiler_sync_at = time.monotonic() + 5
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
                        alerts = apply_spoiler_policy_to_market_alerts(
                            config.spoiler_free_mode,
                            alerts,
                            snapshot,
                            market,
                            state,
                            now,
                        )
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

            poly_ref = bar_polymarket_ref(config)
            if poly_ref is not None and cycle_monotonic >= next_poly_poll_at:
                bar_market = next(
                    (
                        market
                        for market in config.markets
                        if market.ticker == config.probability_bar.market_ticker
                    ),
                    None,
                )
                try:
                    poly_adapter = PolymarketVenueAdapter(
                        config.polymarket.base_url,
                        fetch=http_json,
                    )
                    poly_quotes = poly_adapter.quotes([poly_ref])
                    poly_failures = 0
                    next_poly_poll_at = cycle_monotonic + config.polymarket.poll_seconds
                    venue_quotes_by_side = {}
                    for quote in poly_quotes:
                        venue_quotes_by_side.setdefault(quote.outcome, []).append(quote)
                    left_quote = next(
                        (
                            quote
                            for quote in venue_quotes_by_side.get("left", [])
                            if quote.prob_mid is not None
                        ),
                        None,
                    )
                    if left_quote is not None and left_quote.status == "open":
                        if poly_prev_left_prob is not None:
                            delta = left_quote.prob_mid - poly_prev_left_prob
                            move_threshold = (
                                bar_market.goal_signal_move_cents if bar_market else 5
                            ) / 100
                            if abs(delta) >= move_threshold:
                                poly_last_jump_direction = 1 if delta > 0 else -1
                                poly_last_jump_at = cycle_monotonic
                        poly_prev_left_prob = left_quote.prob_mid
                    bar_snapshot = snapshots.get(config.probability_bar.market_ticker)
                    if (
                        bar_snapshot is not None
                        and left_quote is not None
                        and cycle_monotonic - last_divergence_alert_at
                        >= VENUE_DIVERGENCE_COOLDOWN_SECONDS
                    ):
                        divergence = max_divergence(
                            [venue_quote_from_snapshot(bar_snapshot, "left"), left_quote]
                        )
                        if divergence is not None:
                            label = bar_market.label if bar_market else bar_snapshot.label
                            pending.append(
                                (
                                    venue_divergence_alert(config, divergence, label),
                                    None,
                                    None,
                                    None,
                                )
                            )
                            last_divergence_alert_at = cycle_monotonic
                except (
                    urllib.error.URLError,
                    TimeoutError,
                    OSError,
                    json.JSONDecodeError,
                    ValueError,
                ) as error:
                    # Degrade to single-source aggregation: the bar keeps
                    # rendering from Kalshi alone and the device never hears
                    # about it (PRD: log-only degradation).
                    poly_failures += 1
                    venue_quotes_by_side = {}
                    retry_seconds = min(
                        300,
                        config.polymarket.poll_seconds * (2 ** min(4, poly_failures)),
                    )
                    next_poly_poll_at = cycle_monotonic + retry_seconds
                    print(
                        f"warning: Polymarket fetch failed ({error}); retry in {retry_seconds}s",
                        file=sys.stderr,
                    )

            if config.espn.enabled and cycle_monotonic >= next_espn_poll_at:
                poll_started_at = cycle_monotonic
                espn_state.last_polled_at = cycle_monotonic
                try:
                    match = fetch_espn_match(config.espn)
                    report_player_catalog_coverage(match, config.espn, espn_state)
                    if setup_service:
                        style_after_fetch = setup_service.take_commentary_style_update()
                        if style_after_fetch:
                            apply_live_commentary_style(config, style_after_fetch)
                            pending_device_style_sync = style_after_fetch
                            if sync_device_commentary_style(config, style_after_fetch):
                                pending_device_style_sync = None
                            else:
                                next_device_style_sync_at = time.monotonic() + 5
                        spoiler_after_fetch = (
                            setup_service.take_spoiler_free_mode_update()
                        )
                        if spoiler_after_fetch is not None:
                            enabled = apply_live_spoiler_free_mode(
                                config,
                                spoiler_after_fetch,
                            )
                            if enabled:
                                consume_spoiler_market_baselines(
                                    config,
                                    snapshots,
                                    states,
                                    now,
                                )
                                alert_queue = purge_spoiler_sensitive_alerts(
                                    alert_queue
                                )
                            pending_device_spoiler_sync = enabled
                            if sync_device_spoiler_free_mode(config, enabled):
                                pending_device_spoiler_sync = None
                            else:
                                next_device_spoiler_sync_at = time.monotonic() + 5
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

            if (
                poly_last_jump_direction
                and cycle_monotonic - poly_last_jump_at
                <= GOAL_SIGNAL_CORROBORATION_WINDOW_SECONDS
            ):
                bar_ticker = config.probability_bar.market_ticker
                corroborated: list[PendingAlertContext] = []
                for item in pending:
                    alert, item_snapshot, item_market, item_state = item
                    if (
                        alert.kind == "market_goal_signal"
                        and alert.ticker == bar_ticker
                        and item_market is not None
                    ):
                        # Poly tracks the bar's left outcome in yes-space;
                        # flip the kalshi direction when the alert watched the
                        # no side so both deltas compare in the same space.
                        yes_direction = 1 if alert.clip_id == "odds-up" else -1
                        if item_market.side_i_care == "no":
                            yes_direction = -yes_direction
                        if yes_direction == poly_last_jump_direction:
                            alert = corroborate_goal_signal_alert(alert, config)
                            item = (alert, item_snapshot, item_market, item_state)
                    corroborated.append(item)
                pending = corroborated

            if config.spoiler_free_mode:
                pending = [
                    item for item in pending if not item[0].spoiler_sensitive
                ]
                alert_queue = purge_spoiler_sensitive_alerts(alert_queue)
            alert_queue = merge_alert_queue(alert_queue, pending, cycle_monotonic)

            current_display_command = persistent_display_command(
                config,
                snapshots,
                venue_quotes_by_side,
            )
            display_stale = (
                cycle_monotonic - last_display_sent_at >= config.display_refresh_seconds
            )
            if (
                delivery_future is None
                and current_display_command
                and (current_display_command != last_display_command or display_stale)
            ):
                try:
                    last_display_command = send_ticker(
                        config,
                        snapshots,
                        dry_run=args.dry_run,
                        venue_quotes=venue_quotes_by_side,
                    )
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
                ready_monotonic = time.monotonic()
                ready_index = next(
                    (
                        index
                        for index, item in enumerate(alert_queue)
                        if item.not_before <= ready_monotonic
                    ),
                    None,
                )
            else:
                ready_index = None

            if ready_index is not None:
                delivery_item = alert_queue.pop(ready_index)
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
