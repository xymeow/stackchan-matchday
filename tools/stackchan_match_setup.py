#!/usr/bin/env python3
"""Phone-friendly pre-match setup and ESPN schedule discovery for Stack-chan."""

from __future__ import annotations

import json
import re
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from functools import wraps
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from stackchan_i18n import SUPPORTED_LANGUAGES, localized_pair, normalize_language, resolve_text


REQUEST_TIMEOUT_SECONDS = 12
DEFAULT_SETUP_PORT = 8788
DEFAULT_COMMENTARY_STYLE = "balanced"
COMMENTARY_STYLES = frozenset({"casual", "balanced", "professional"})


def normalize_commentary_style(value: Any) -> str:
    style = str(DEFAULT_COMMENTARY_STYLE if value is None else value).strip().lower()
    if style not in COMMENTARY_STYLES:
        choices = ", ".join(sorted(COMMENTARY_STYLES))
        raise ValueError(f"espn.commentary_style must be one of: {choices}")
    return style


TEAM_METADATA: dict[str, tuple[str, str, str]] = {
    "Algeria": ("阿尔及利亚", "dz", "#006633"),
    "Argentina": ("阿根廷", "ar", "#75AADB"),
    "Australia": ("澳大利亚", "au", "#FFCD00"),
    "Austria": ("奥地利", "at", "#ED2939"),
    "Belgium": ("比利时", "be", "#EF3340"),
    "Bosnia-Herzegovina": ("波黑", "ba", "#002395"),
    "Brazil": ("巴西", "br", "#009C3B"),
    "Cabo Verde": ("佛得角", "cv", "#003893"),
    "Canada": ("加拿大", "ca", "#D80621"),
    "Colombia": ("哥伦比亚", "co", "#FCD116"),
    "Croatia": ("克罗地亚", "hr", "#FF0000"),
    "Curaçao": ("库拉索", "cw", "#002B7F"),
    "Czechia": ("捷克", "cz", "#11457E"),
    "DR Congo": ("刚果（金）", "cd", "#007FFF"),
    "Ecuador": ("厄瓜多尔", "ec", "#FFD100"),
    "Egypt": ("埃及", "eg", "#CE1126"),
    "England": ("英格兰", "gb-eng", "#CE1124"),
    "France": ("法国", "fr", "#0055A4"),
    "Germany": ("德国", "de", "#DD0000"),
    "Ghana": ("加纳", "gh", "#CE1126"),
    "Iran": ("伊朗", "ir", "#239F40"),
    "Iraq": ("伊拉克", "iq", "#CE1126"),
    "Ivory Coast": ("科特迪瓦", "ci", "#F77F00"),
    "Japan": ("日本", "jp", "#BC002D"),
    "Jordan": ("约旦", "jo", "#CE1126"),
    "Korea Republic": ("韩国", "kr", "#CD2E3A"),
    "Mexico": ("墨西哥", "mx", "#006847"),
    "Morocco": ("摩洛哥", "ma", "#C1272D"),
    "Netherlands": ("荷兰", "nl", "#F36C21"),
    "New Zealand": ("新西兰", "nz", "#00247D"),
    "Norway": ("挪威", "no", "#BA0C2F"),
    "Panama": ("巴拿马", "pa", "#DA121A"),
    "Paraguay": ("巴拉圭", "py", "#D52B1E"),
    "Portugal": ("葡萄牙", "pt", "#046A38"),
    "Qatar": ("卡塔尔", "qa", "#8A1538"),
    "Saudi Arabia": ("沙特阿拉伯", "sa", "#006C35"),
    "Scotland": ("苏格兰", "gb-sct", "#005EB8"),
    "Senegal": ("塞内加尔", "sn", "#00853F"),
    "South Africa": ("南非", "za", "#007A4D"),
    "Spain": ("西班牙", "es", "#AA151B"),
    "Sweden": ("瑞典", "se", "#006AA7"),
    "Switzerland": ("瑞士", "ch", "#D52B1E"),
    "Tunisia": ("突尼斯", "tn", "#E70013"),
    "Turkey": ("土耳其", "tr", "#E30A17"),
    "United States": ("美国", "us", "#3C3B6E"),
    "Uruguay": ("乌拉圭", "uy", "#5CBFEB"),
    "Uzbekistan": ("乌兹别克斯坦", "uz", "#0099B5"),
}


TEAM_ALIASES = {
    "usa": "unitedstates",
    "unitedstatesofamerica": "unitedstates",
    "southkorea": "korearepublic",
    "korea": "korearepublic",
    "cotedivoire": "ivorycoast",
    "capeverde": "caboverde",
    "bosniaandherzegovina": "bosniaherzegovina",
    "democraticrepublicofcongo": "drcongo",
}


def fetch_json(url: str) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "stackchan-match-setup/0.1"},
    )
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        return json.loads(response.read().decode("utf-8"))


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


def normalize_team(value: str) -> str:
    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    normalized = "".join(character for character in ascii_value.casefold() if character.isalnum())
    return TEAM_ALIASES.get(normalized, normalized)


def team_metadata(name: str, abbreviation: str = "", fallback_side: str = "left") -> dict[str, str]:
    normalized = normalize_team(name)
    for configured_name, (localized, flag, color) in TEAM_METADATA.items():
        if normalize_team(configured_name) == normalized:
            return {
                "name": name,
                "localized": localized,
                "flag": flag,
                "color": color,
                "abbreviation": abbreviation,
            }
    return {
        "name": name,
        "localized": name,
        "flag": "fr" if fallback_side == "left" else "ma",
        "color": "#2457A6" if fallback_side == "left" else "#C1272D",
        "abbreviation": abbreviation,
    }


def parse_scoreboard(payload: dict[str, Any], now: datetime | None = None) -> list[dict[str, Any]]:
    now = now or datetime.now(timezone.utc)
    matches: list[dict[str, Any]] = []
    for event in payload.get("events") or []:
        competitions = event.get("competitions") or []
        if not competitions:
            continue
        competition = competitions[0]
        competitors = competition.get("competitors") or []
        by_side = {str(item.get("homeAway") or ""): item for item in competitors}
        if "home" not in by_side or "away" not in by_side:
            continue
        starts_at = parse_datetime(event.get("date") or competition.get("date"))
        if starts_at is None or starts_at < now - timedelta(hours=3):
            continue
        status = ((competition.get("status") or {}).get("type") or {})
        home_raw = by_side["home"].get("team") or {}
        away_raw = by_side["away"].get("team") or {}
        home_name = str(home_raw.get("displayName") or home_raw.get("name") or "")
        away_name = str(away_raw.get("displayName") or away_raw.get("name") or "")
        home_abbreviation = str(home_raw.get("abbreviation") or "")
        away_abbreviation = str(away_raw.get("abbreviation") or "")
        if not home_name or not away_name:
            continue
        home = team_metadata(home_name, home_abbreviation, "left")
        away = team_metadata(away_name, away_abbreviation, "right")
        matches.append(
            {
                "event_id": str(event.get("id") or competition.get("id") or ""),
                "starts_at": starts_at.astimezone(timezone.utc).isoformat(),
                "state": str(status.get("state") or ""),
                "status": str(status.get("description") or status.get("detail") or ""),
                "home": home,
                "away": away,
                "label": f"{home['localized']} vs {away['localized']}",
                "venue": str((competition.get("venue") or {}).get("fullName") or ""),
            }
        )
    return sorted(matches, key=lambda match: match["starts_at"])


def extract_kalshi_event_ticker(value: str) -> str:
    raw = value.strip()
    if not raw:
        raise ValueError("请粘贴 Kalshi 比赛链接")
    parsed = urllib.parse.urlparse(raw if "://" in raw else f"https://kalshi.com/{raw}")
    candidates = [part for part in parsed.path.split("/") if part]
    candidates.append(raw)
    for candidate in reversed(candidates):
        normalized = candidate.split("?", 1)[0].strip().upper()
        if normalized.startswith("KX") and "-" in normalized:
            return normalized
    raise ValueError("无法从链接中识别 Kalshi event ticker")


def market_team_name(market: dict[str, Any]) -> str:
    raw = str(
        market.get("yes_sub_title")
        or market.get("subtitle")
        or market.get("yes_subtitle")
        or ""
    ).strip()
    cleaned = re.sub(
        r"\s+(?:advances?|to advance|wins?(?: the match)?|qualifies?)\s*$",
        "",
        raw,
        flags=re.IGNORECASE,
    ).strip()
    if cleaned:
        return cleaned
    ticker = str(market.get("ticker") or "")
    return ticker.rsplit("-", 1)[-1]


def event_markets(payload: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    event = payload.get("event") or {}
    markets = event.get("markets") or payload.get("markets") or []
    parsed = [market for market in markets if isinstance(market, dict) and market.get("ticker")]
    if len(parsed) != 2:
        raise ValueError("目前只支持恰好包含两个互斥球队盘口的淘汰赛事件")
    teams = [market_team_name(market) for market in parsed]
    if not all(teams) or normalize_team(teams[0]) == normalize_team(teams[1]):
        raise ValueError("Kalshi 事件没有提供可识别的双方球队")
    return event, parsed


# Standalone (non-fixture) events keep at most this many markets in the ticker.
STANDALONE_MARKET_LIMIT = 4


def general_event_markets(payload: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Like event_markets, but for arbitrary Kalshi events: any market count."""
    event = payload.get("event") or {}
    markets = event.get("markets") or payload.get("markets") or []
    parsed = [market for market in markets if isinstance(market, dict) and market.get("ticker")]
    if not parsed:
        raise ValueError("Kalshi 事件没有可用盘口")
    return event, parsed


def match_teams(match: dict[str, Any]) -> set[str]:
    return {
        normalize_team(str(match["home"]["name"])),
        normalize_team(str(match["away"]["name"])),
    }


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def serialized_config_mutation(method):
    """Serialize complete config read-modify-write transactions."""

    @wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._config_mutation_lock:
            return method(self, *args, **kwargs)

    return wrapper


class MatchSetupService:
    def __init__(
        self,
        config_path: Path,
        kalshi_base_url: str,
        espn_base_url: str,
        league: str,
        kalshi_series_ticker: str = "KXWCADVANCE",
        lookahead_days: int = 10,
        cache_seconds: int = 300,
        language: str = "zh",
    ) -> None:
        self.config_path = config_path
        self.kalshi_base_url = kalshi_base_url.rstrip("/")
        self.espn_base_url = espn_base_url.rstrip("/")
        self.league = league
        self.kalshi_series_ticker = kalshi_series_ticker.strip().upper()
        self.lookahead_days = max(1, lookahead_days)
        self.cache_seconds = max(30, cache_seconds)
        self.language = normalize_language(language)
        self.setup_url = ""
        self._lock = threading.Lock()
        self._config_mutation_lock = threading.RLock()
        self._reload_requested = threading.Event()
        self._commentary_style_update: str | None = None
        self._upcoming_cache: list[dict[str, Any]] = []
        self._upcoming_cached_at = 0.0
        self._options_cache: list[dict[str, Any]] = []
        self._options_cached_at = 0.0

    def take_reload_requested(self) -> bool:
        if not self._reload_requested.is_set():
            return False
        self._reload_requested.clear()
        return True

    def take_commentary_style_update(self) -> str | None:
        """Return a live style update without requesting a full watcher reload."""

        with self._lock:
            style = self._commentary_style_update
            self._commentary_style_update = None
        return style

    def upcoming_matches(self, force: bool = False) -> list[dict[str, Any]]:
        with self._lock:
            if (
                not force
                and self._upcoming_cache
                and time.monotonic() - self._upcoming_cached_at < self.cache_seconds
            ):
                return list(self._upcoming_cache)
        now = datetime.now(timezone.utc)
        end = now + timedelta(days=self.lookahead_days)
        dates = f"{now:%Y%m%d}-{end:%Y%m%d}"
        query = urllib.parse.urlencode({"dates": dates, "limit": "100"})
        url = f"{self.espn_base_url}/{urllib.parse.quote(self.league)}/scoreboard?{query}"
        matches = [
            match
            for match in parse_scoreboard(fetch_json(url), now=now)
            if match["state"] in {"pre", "in"}
        ]
        with self._lock:
            self._upcoming_cache = matches
            self._upcoming_cached_at = time.monotonic()
        return list(matches)

    def setup_options(self, force: bool = False) -> list[dict[str, Any]]:
        with self._lock:
            if (
                not force
                and self._options_cache
                and time.monotonic() - self._options_cached_at < self.cache_seconds
            ):
                return list(self._options_cache)
        query = urllib.parse.urlencode(
            {
                "series_ticker": self.kalshi_series_ticker,
                "status": "open",
                "with_nested_markets": "true",
                "limit": "200",
            }
        )
        payload = fetch_json(f"{self.kalshi_base_url}/events?{query}")
        kalshi_by_teams: dict[frozenset[str], str] = {}
        for event in payload.get("events") or []:
            try:
                _parsed_event, markets = event_markets({"event": event})
            except ValueError:
                continue
            teams = frozenset(normalize_team(market_team_name(market)) for market in markets)
            kalshi_by_teams[teams] = str(event.get("event_ticker") or "")
        options: list[dict[str, Any]] = []
        for match in self.upcoming_matches(force=force):
            option = dict(match)
            option["label"] = (
                f"{match['home']['localized']} vs {match['away']['localized']}"
                if self.language == "zh"
                else f"{match['home']['name']} vs {match['away']['name']}"
            )
            option["kalshi_event_ticker"] = kalshi_by_teams.get(
                frozenset(match_teams(match)),
                "",
            )
            if option["kalshi_event_ticker"]:
                options.append(option)
        with self._lock:
            self._options_cache = options
            self._options_cached_at = time.monotonic()
        return list(options)

    def _kalshi_event(self, event_ticker: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        query = urllib.parse.urlencode({"with_nested_markets": "true"})
        url = f"{self.kalshi_base_url}/events/{urllib.parse.quote(event_ticker)}?{query}"
        return event_markets(fetch_json(url))

    def _kalshi_event_any(self, event_ticker: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        query = urllib.parse.urlencode({"with_nested_markets": "true"})
        url = f"{self.kalshi_base_url}/events/{urllib.parse.quote(event_ticker)}?{query}"
        return general_event_markets(fetch_json(url))

    def resolve_kalshi(self, value: str) -> dict[str, Any]:
        event_ticker = extract_kalshi_event_ticker(value)
        event, markets = self._kalshi_event(event_ticker)
        teams = [market_team_name(market) for market in markets]
        team_set = {normalize_team(team) for team in teams}
        upcoming = self.upcoming_matches()
        candidates = [match for match in upcoming if match_teams(match) == team_set]
        if not candidates:
            candidates = upcoming
        return {
            "event_ticker": str(event.get("event_ticker") or event_ticker),
            "title": str(event.get("title") or " vs ".join(teams)),
            "teams": [
                {
                    **team_metadata(team, fallback_side="left" if index == 0 else "right"),
                    "market_ticker": str(markets[index].get("ticker") or "").upper(),
                    "market_status": str(markets[index].get("status") or ""),
                }
                for index, team in enumerate(teams)
            ],
            "espn_candidates": candidates,
            "recommended_event_id": candidates[0]["event_id"] if len(candidates) == 1 else "",
        }

    def _espn_match(self, event_id: str) -> dict[str, Any]:
        for match in self.upcoming_matches(force=True):
            if match["event_id"] == event_id:
                return match
        url = (
            f"{self.espn_base_url}/{urllib.parse.quote(self.league)}/summary?"
            f"{urllib.parse.urlencode({'event': event_id})}"
        )
        payload = fetch_json(url)
        header = payload.get("header") or {}
        competition = (header.get("competitions") or [{}])[0]
        scoreboard_payload = {
            "events": [
                {
                    "id": header.get("id") or event_id,
                    "date": competition.get("date"),
                    "competitions": [competition],
                }
            ]
        }
        matches = parse_scoreboard(
            scoreboard_payload,
            now=datetime.now(timezone.utc) - timedelta(days=1),
        )
        if not matches:
            raise ValueError(f"ESPN event {event_id} 没有有效的比赛信息")
        return matches[0]

    def current_status(self) -> dict[str, Any]:
        raw = json.loads(self.config_path.read_text(encoding="utf-8"))
        language = self.language
        espn = raw.get("espn") or {}
        team_names = espn.get("team_names") or {}
        favorite_team = str(espn.get("favorite_team") or "")
        position_team = str(espn.get("position_team") or "")
        return {
            "setup_url": self.setup_url,
            "language": language,
            "commentary_style": normalize_commentary_style(
                espn.get("commentary_style", DEFAULT_COMMENTARY_STYLE)
            ),
            "kalshi_url": str((raw.get("setup_server") or {}).get("last_kalshi_url") or ""),
            "event_id": str(espn.get("event_id") or ""),
            "label": resolve_text(espn.get("label"), language, path="espn.label"),
            "label_i18n": {
                lang: resolve_text(espn.get("label"), lang, path="espn.label")
                for lang in SUPPORTED_LANGUAGES
            },
            "starts_at": str(espn.get("starts_at") or ""),
            "favorite_team": resolve_text(
                team_names.get(favorite_team),
                language,
                path=f"espn.team_names[{favorite_team!r}]",
                fallback=favorite_team,
            ),
            "favorite_team_i18n": {
                lang: resolve_text(
                    team_names.get(favorite_team),
                    lang,
                    path=f"espn.team_names[{favorite_team!r}]",
                    fallback=favorite_team,
                )
                for lang in SUPPORTED_LANGUAGES
            },
            "position_team": resolve_text(
                team_names.get(position_team),
                language,
                path=f"espn.team_names[{position_team!r}]",
                fallback=position_team,
            ),
            "position_team_i18n": {
                lang: resolve_text(
                    team_names.get(position_team),
                    lang,
                    path=f"espn.team_names[{position_team!r}]",
                    fallback=position_team,
                )
                for lang in SUPPORTED_LANGUAGES
            },
        }

    @serialized_config_mutation
    def apply_commentary_style(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Persist and signal a style-only update without resetting match state."""

        requested = payload.get("commentary_style")
        if not isinstance(requested, str) or not requested.strip():
            raise ValueError("commentary_style is required")
        style = normalize_commentary_style(requested)
        raw = json.loads(self.config_path.read_text(encoding="utf-8"))
        raw.setdefault("espn", {})["commentary_style"] = style
        atomic_write_json(self.config_path, raw)
        with self._lock:
            self._commentary_style_update = style
        return {"ok": True, "commentary_style": style}

    @serialized_config_mutation
    def apply_selection(self, payload: dict[str, Any]) -> dict[str, Any]:
        language = normalize_language(payload.get("language", self.language), path="language")
        requested_style = payload.get("commentary_style")
        commentary_style = (
            normalize_commentary_style(requested_style)
            if requested_style is not None
            else ""
        )
        kalshi_value = str(payload.get("kalshi_url") or payload.get("event_ticker") or "")
        event_ticker = extract_kalshi_event_ticker(kalshi_value)
        event, markets = self._kalshi_event(event_ticker)
        espn_event_id = str(payload.get("espn_event_id") or "").strip()
        if not espn_event_id:
            raise ValueError("请选择对应的 ESPN 比赛")
        match = self._espn_match(espn_event_id)

        market_by_team = {
            normalize_team(market_team_name(market)): market
            for market in markets
        }
        if match_teams(match) != set(market_by_team):
            raise ValueError("Kalshi 双方球队与所选 ESPN 比赛不一致")

        ordered_teams = [match["home"], match["away"]]
        ordered_markets = [market_by_team[normalize_team(team["name"])] for team in ordered_teams]
        team_by_normalized_name = {
            normalize_team(str(team["name"])): str(team["name"])
            for team in ordered_teams
        }

        def canonical_selected_team(field: str, error: str) -> str:
            selected = str(payload.get(field) or "").strip()
            if not selected:
                return ""
            canonical = team_by_normalized_name.get(normalize_team(selected))
            if canonical is None:
                raise ValueError(error)
            return canonical

        # The phone form uses the Kalshi outcome label, while ESPN occasionally
        # uses an equivalent country name (for example Cape Verde/Cabo Verde).
        # Persist the canonical ESPN name after the same normalization that
        # already validated the fixture pairing.
        favorite_team = canonical_selected_team(
            "favorite_team", "支持球队不属于这场比赛"
        )
        position_team = canonical_selected_team(
            "position_team", "持仓球队不属于这场比赛"
        )
        active_market_index = 0
        if position_team:
            active_market_index = next(
                index
                for index, team in enumerate(ordered_teams)
                if str(team["name"]) == position_team
            )

        raw = json.loads(self.config_path.read_text(encoding="utf-8"))
        if not commentary_style:
            commentary_style = normalize_commentary_style(
                (raw.get("espn") or {}).get("commentary_style", DEFAULT_COMMENTARY_STYLE)
            )
        raw["language"] = language
        existing_markets = raw.get("markets") or []
        market_defaults = dict(existing_markets[0]) if existing_markets else {}
        localized = [str(team["localized"]) for team in ordered_teams]
        english = [str(team["name"]) for team in ordered_teams]

        bar = raw.setdefault("probability_bar", {})
        bar.update(
            {
                "enabled": True,
                "mode": "normalized_outcomes",
                "market_ticker": str(ordered_markets[0]["ticker"]).upper(),
                "right_market_ticker": str(ordered_markets[1]["ticker"]).upper(),
                "side": "yes",
                "left_flag": ordered_teams[0]["flag"],
                "left_color": ordered_teams[0]["color"],
                "right_flag": ordered_teams[1]["flag"],
                "right_color": ordered_teams[1]["color"],
            }
        )

        espn = raw.setdefault("espn", {})
        espn.update(
            {
                "enabled": True,
                "event_id": espn_event_id,
                "league": self.league,
                "label": localized_pair(
                    f"{localized[0]} vs {localized[1]}",
                    f"{english[0]} vs {english[1]}",
                ),
                "starts_at": match["starts_at"],
                "favorite_team": favorite_team,
                "position_team": position_team,
                "commentary_style": commentary_style,
            }
        )
        team_names = espn.setdefault("team_names", {})
        team_colors = espn.setdefault("team_colors", {})
        for team in ordered_teams:
            localized_name = localized_pair(str(team["localized"]), str(team["name"]))
            team_names[team["name"]] = localized_name
            if team.get("abbreviation"):
                team_names[team["abbreviation"]] = localized_name
            team_colors[team["name"]] = team["color"]
            if team.get("abbreviation"):
                team_colors[team["abbreviation"]] = team["color"]

        configured_markets: list[dict[str, Any]] = []
        for index, (team, market) in enumerate(zip(ordered_teams, ordered_markets)):
            market_is_active = index == active_market_index
            opposing_index = 1 - index
            configured = dict(market_defaults)
            configured.update(
                {
                    "ticker": str(market["ticker"]).upper(),
                    "label": localized_pair(
                        f"{team['localized']}晋级",
                        f"{team['name']} to advance",
                    ),
                    "side_i_care": "yes",
                    "alerts_enabled": market_is_active,
                    "show_in_ticker": market_is_active,
                    "tracks_position": bool(position_team and market_is_active),
                }
            )
            if market_is_active:
                configured.update(
                    {
                        "goal_signal_enabled": True,
                        "goal_signal_up_speech": localized_pair(
                            (
                                f"{localized[index]}晋级盘口快速上行。"
                                f"{localized[index]}进球的可能性上升，但目前仍属疑似，"
                                "等待文字直播确认。"
                            ),
                            (
                                f"The {english[index]} advancement market moved sharply higher. "
                                f"Possible goal for {english[index]}; awaiting commentary confirmation."
                            ),
                        ),
                        "goal_signal_down_speech": localized_pair(
                            (
                                f"{localized[index]}晋级盘口快速下挫。"
                                f"{localized[opposing_index]}进球的可能性上升，但目前仍属疑似，"
                                "等待文字直播确认。"
                            ),
                            (
                                f"The {english[index]} advancement market moved sharply lower. "
                                f"Possible goal for {english[opposing_index]}; awaiting commentary confirmation."
                            ),
                        ),
                        "goal_signal_up_team": localized_pair(
                            localized[index],
                            english[index],
                        ),
                        "goal_signal_down_team": localized_pair(
                            localized[opposing_index],
                            english[opposing_index],
                        ),
                    }
                )
            else:
                configured.pop("goal_signal_up_speech", None)
                configured.pop("goal_signal_down_speech", None)
                configured.pop("goal_signal_up_team", None)
                configured.pop("goal_signal_down_team", None)
                configured["goal_signal_enabled"] = False
            configured_markets.append(configured)
        raw["markets"] = configured_markets

        setup = raw.setdefault("setup_server", {})
        setup["last_kalshi_url"] = kalshi_value
        setup["last_event_ticker"] = str(event.get("event_ticker") or event_ticker)

        atomic_write_json(self.config_path, raw)
        self.language = language
        with self._lock:
            self._options_cache = []
            self._options_cached_at = 0.0
        self._reload_requested.set()
        return {
            "ok": True,
            "language": language,
            "commentary_style": commentary_style,
            "label": resolve_text(espn["label"], language, path="espn.label"),
            "label_i18n": {
                lang: resolve_text(espn["label"], lang, path="espn.label")
                for lang in SUPPORTED_LANGUAGES
            },
            "event_id": espn_event_id,
            "favorite_team": favorite_team,
            "position_team": position_team,
            "starts_at": match["starts_at"],
        }

    @serialized_config_mutation
    def apply_market_selection(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Watch an arbitrary Kalshi event without an ESPN pairing.

        Configures up to STANDALONE_MARKET_LIMIT of the event's most-traded
        markets for the legacy text ticker; the probability bar and ESPN
        commentary are turned off because standalone markets have neither
        team flags nor a matching fixture.
        """
        language = normalize_language(payload.get("language", self.language), path="language")
        requested_style = payload.get("commentary_style")
        commentary_style = (
            normalize_commentary_style(requested_style)
            if requested_style is not None
            else ""
        )
        kalshi_value = str(payload.get("kalshi_url") or payload.get("event_ticker") or "").strip()
        event_ticker = extract_kalshi_event_ticker(kalshi_value)
        if not event_ticker:
            raise ValueError("请提供 Kalshi 事件链接或 event ticker")
        event, markets = self._kalshi_event_any(event_ticker)

        def traded_volume(market: dict[str, Any]) -> float:
            for key in ("volume_24h", "volume"):
                try:
                    return float(market.get(key) or 0)
                except (TypeError, ValueError):
                    continue
            return 0.0

        markets = sorted(markets, key=traded_volume, reverse=True)[:STANDALONE_MARKET_LIMIT]
        event_title = str(event.get("title") or event.get("sub_title") or event_ticker)

        raw = json.loads(self.config_path.read_text(encoding="utf-8"))
        if not commentary_style:
            commentary_style = normalize_commentary_style(
                (raw.get("espn") or {}).get("commentary_style", DEFAULT_COMMENTARY_STYLE)
            )
        raw["language"] = language
        existing_markets = raw.get("markets") or []
        market_defaults = dict(existing_markets[0]) if existing_markets else {}

        configured_markets: list[dict[str, Any]] = []
        for market in markets:
            configured = dict(market_defaults)
            configured.pop("goal_signal_up_speech", None)
            configured.pop("goal_signal_down_speech", None)
            configured.pop("goal_signal_up_team", None)
            configured.pop("goal_signal_down_team", None)
            label = str(
                market.get("yes_sub_title")
                or market.get("subtitle")
                or market.get("title")
                or market["ticker"]
            )
            configured.update(
                {
                    "ticker": str(market["ticker"]).upper(),
                    "label": label,
                    "side_i_care": "yes",
                    "alerts_enabled": True,
                    "show_in_ticker": True,
                    "goal_signal_enabled": False,
                    "favorite_team": "",
                    "position_team": "",
                    "tracks_position": False,
                }
            )
            configured_markets.append(configured)
        raw["markets"] = configured_markets
        raw["ticker_enabled"] = True
        raw.setdefault("probability_bar", {})["enabled"] = False
        espn = raw.setdefault("espn", {})
        espn["enabled"] = False
        espn["commentary_style"] = commentary_style
        espn["favorite_team"] = ""
        espn["position_team"] = ""

        setup = raw.setdefault("setup_server", {})
        setup["last_kalshi_url"] = kalshi_value
        setup["last_event_ticker"] = str(event.get("event_ticker") or event_ticker)

        atomic_write_json(self.config_path, raw)
        self.language = language
        with self._lock:
            self._options_cache = []
            self._options_cached_at = 0.0
        self._reload_requested.set()
        return {
            "ok": True,
            "language": language,
            "commentary_style": commentary_style,
            "label": event_title,
            "label_i18n": {lang: event_title for lang in SUPPORTED_LANGUAGES},
            "event_id": "",
            "favorite_team": "",
            "position_team": "",
            "starts_at": "",
            "markets": [entry["ticker"] for entry in configured_markets],
        }

    def last_daily_prompt(self) -> str:
        try:
            raw = json.loads(self.config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return ""
        return str((raw.get("setup_server") or {}).get("last_daily_prompt") or "")

    @serialized_config_mutation
    def record_daily_prompt(self, day: str) -> None:
        raw = json.loads(self.config_path.read_text(encoding="utf-8"))
        setup = raw.setdefault("setup_server", {})
        if setup.get("last_daily_prompt") == day:
            return
        setup["last_daily_prompt"] = day
        # Bookkeeping only: no reload request, nothing semantic changed.
        atomic_write_json(self.config_path, raw)


def setup_page_html() -> str:
    return """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
  <title>Stack-chan 赛前设置</title>
  <style>
    :root{color-scheme:light;--ink:#17202a;--muted:#65717d;--line:#d8dee4;--panel:#fff;--bg:#f4f6f7;--accent:#d62828;--accent2:#006847;--focus:#1769aa}
    *{box-sizing:border-box;letter-spacing:0}
    body{margin:0;background:var(--bg);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
    main{width:min(720px,100%);margin:0 auto;padding:18px 16px 40px}
    header{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:18px}
    h1{font-size:22px;margin:0}.status{font-size:12px;color:var(--muted)}
    section{padding:16px 0;border-top:1px solid var(--line)}
    h2{font-size:15px;margin:0 0 12px}label.field{display:block;font-size:13px;font-weight:650;margin:12px 0 6px}
    input,select,button{font:inherit;min-height:44px;border:1px solid var(--line);border-radius:6px;background:#fff;color:var(--ink)}
    input,select{width:100%;padding:10px 11px}button{padding:9px 13px;font-weight:700;cursor:pointer}
    button.primary{width:100%;background:var(--accent);border-color:var(--accent);color:#fff}
    button.secondary{width:100%;background:#fff}.row{display:grid;grid-template-columns:1fr auto;gap:8px}.row button{min-width:88px}
    .matches{display:grid;gap:8px}.match{width:100%;display:grid;grid-template-columns:1fr auto;text-align:left;gap:10px;padding:11px;background:var(--panel)}
    .match strong{display:block;font-size:15px}.match time{font-size:12px;color:var(--muted);align-self:center}.market-ready{display:block;margin-top:3px;font-size:11px;color:#067647;font-style:normal}.match.selected{border-color:var(--focus);box-shadow:0 0 0 1px var(--focus)}
    .resolved{display:none;margin-top:14px;padding:12px;border-left:3px solid var(--accent2);background:#fff}.resolved.show{display:block}.versus{font-size:18px;font-weight:750;margin-bottom:10px}
    .segment{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:0;border:1px solid var(--line);border-radius:6px;overflow:hidden;background:#fff}.segment.two{grid-template-columns:repeat(2,minmax(0,1fr))}
    .segment label{min-width:0}.segment input{position:absolute;width:1px;height:1px;opacity:0;pointer-events:none}.segment span{display:flex;align-items:center;justify-content:center;min-height:44px;padding:7px 5px;font-size:13px;text-align:center;border-right:1px solid var(--line);overflow-wrap:anywhere}.segment label:last-child span{border-right:0}.segment input:checked+span{background:#17202a;color:#fff}
    .message{min-height:22px;margin-top:10px;font-size:13px;color:var(--muted)}.message.error{color:#b42318}.message.ok{color:#067647}
    .empty{font-size:13px;color:var(--muted);padding:12px 0}.current,.hint{font-size:13px;color:var(--muted);line-height:1.6}.hint{margin-top:7px}
    @media(max-width:420px){main{padding-left:12px;padding-right:12px}.row{grid-template-columns:1fr}.row button{width:100%}.match{grid-template-columns:1fr}.match time{justify-self:start}}
  </style>
</head>
<body>
<main>
  <header><h1>Stack-chan 赛前设置</h1><div class="status" id="health">连接中</div></header>
  <section><h2>播报语言 / Commentary language</h2><div class="segment two" id="language"><label><input type="radio" name="language" value="zh" checked><span>中文</span></label><label><input type="radio" name="language" value="en"><span>English</span></label></div><div class="hint">选择比赛并点“开始看球”后生效</div></section>
  <section><h2>播报语气</h2><div class="segment" id="commentary-style"><label><input type="radio" name="commentary_style" value="casual"><span>朋友陪看</span></label><label><input type="radio" name="commentary_style" value="balanced" checked><span>自然播报</span></label><label><input type="radio" name="commentary_style" value="professional"><span>专业解说</span></label></div><div class="hint" id="style-effective">当前生效：自然播报</div><div class="hint">可在比赛中即时切换，不会重播旧事件</div></section>
  <section><h2>未来比赛</h2><div class="matches" id="matches"><div class="empty">正在读取赛程</div></div></section>
  <section>
    <h2>盘口与直播</h2>
    <label class="field" for="kalshi">Kalshi 比赛链接</label>
    <div class="row"><input id="kalshi" inputmode="url" autocomplete="url" placeholder="https://kalshi.com/markets/..."><button class="secondary" id="resolve">解析</button></div>
    <div class="resolved" id="resolved">
      <div class="versus" id="versus"></div>
      <label class="field" for="espn">ESPN 比赛</label><select id="espn"></select>
      <label class="field">支持球队</label><div class="segment" id="favorite"></div>
      <label class="field">赛前持仓</label><div class="segment" id="position"></div>
      <button class="primary" id="apply" style="margin-top:16px">开始看球</button>
    </div>
    <div class="message" id="message"></div>
  </section>
  <section><h2>当前监控</h2><div class="current" id="current">读取中</div></section>
</main>
<script>
const state={selectedEventId:'',resolved:null};
const $=id=>document.getElementById(id);
const selectedStyle=()=>document.querySelector('input[name="commentary_style"]:checked')?.value||'balanced';
const styleNames={casual:'朋友陪看',balanced:'自然播报',professional:'专业解说'};
function showEffectiveStyle(style){$('style-effective').textContent=`当前生效：${styleNames[style]||styleNames.balanced}`}
const localTime=value=>new Intl.DateTimeFormat('zh-CN',{weekday:'short',month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit'}).format(new Date(value));
function setMessage(text,kind=''){const el=$('message');el.textContent=text;el.className='message '+kind}
function renderMatches(matches){
  const root=$('matches');root.textContent='';
  if(!matches.length){root.innerHTML='<div class="empty">未来十天没有开放的双方盘口</div>';return}
  matches.forEach(match=>{const button=document.createElement('button');button.className='match';button.dataset.id=match.event_id;button.innerHTML=`<strong>${match.label}${match.kalshi_event_ticker?'<em class="market-ready">Kalshi 盘口可用</em>':''}</strong><time>${localTime(match.starts_at)}</time>`;button.onclick=()=>chooseMatch(match);root.appendChild(button)})
}
function choices(rootId,name,teams,emptyLabel){const root=$(rootId);root.textContent='';const language=document.querySelector('input[name="language"]:checked')?.value||'zh';[...teams,{name:'',localized:emptyLabel}].forEach((team,index)=>{const label=document.createElement('label');const text=language==='en'&&team.name?team.name:team.localized;label.innerHTML=`<input type="radio" name="${name}" value="${team.name}" ${index===teams.length?'checked':''}><span>${text}</span>`;root.appendChild(label)})}
function selectRecommendedEspn(){const select=$('espn');const preferred=state.selectedEventId||state.resolved?.recommended_event_id;if(preferred&&[...select.options].some(option=>option.value===preferred))select.value=preferred}
function renderResolved(data){state.resolved=data;const english=document.querySelector('input[name="language"]:checked')?.value==='en';$('versus').textContent=data.teams.map(team=>english?team.name:team.localized).join(' vs ');const select=$('espn');select.textContent='';data.espn_candidates.forEach(match=>{const option=document.createElement('option');option.value=match.event_id;option.textContent=`${match.label} · ${localTime(match.starts_at)}`;select.appendChild(option)});choices('favorite','favorite_team',data.teams,english?'Neutral':'中立');choices('position','position_team',data.teams,english?'No position':'没买');selectRecommendedEspn();$('resolved').classList.add('show')}
async function json(url,options){const response=await fetch(url,options);const data=await response.json();if(!response.ok)throw new Error(data.error||'请求失败');return data}
async function resolveKalshi(){setMessage('正在解析盘口和比赛');try{const data=await json('/api/setup/resolve',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({kalshi_url:$('kalshi').value})});renderResolved(data);setMessage('已匹配双方盘口','ok')}catch(error){setMessage(error.message,'error')}}
async function chooseMatch(match){state.selectedEventId=match.event_id;document.querySelectorAll('.match').forEach(el=>el.classList.toggle('selected',el.dataset.id===match.event_id));if(match.kalshi_event_ticker){$('kalshi').value=match.kalshi_event_ticker;await resolveKalshi()}else if(state.resolved){selectRecommendedEspn()}}
async function refreshEffectiveStyle(){try{const status=await json('/api/setup/status');const style=status.commentary_style||'balanced';const input=document.querySelector(`input[name="commentary_style"][value="${style}"]`);if(input)input.checked=true;showEffectiveStyle(style)}catch(_error){}}
async function boot(){try{const [status,upcoming]=await Promise.all([json('/api/setup/status'),json('/api/setup/upcoming')]);$('health').textContent='watcher 在线';$('kalshi').value=status.kalshi_url||'';const language=status.language==='en'?'en':'zh';const input=document.querySelector(`input[name="language"][value="${language}"]`);if(input)input.checked=true;const style=status.commentary_style||'balanced';const styleInput=document.querySelector(`input[name="commentary_style"][value="${style}"]`);if(styleInput)styleInput.checked=true;showEffectiveStyle(style);$('current').textContent=status.label?`${status.label} · 支持 ${status.favorite_team||'中立'} · 持仓 ${status.position_team||'无'} · ${selectedStyle()}`:'尚未配置';renderMatches(upcoming.matches)}catch(error){$('health').textContent='连接失败';setMessage(error.message,'error')}}
$('resolve').onclick=resolveKalshi;
document.querySelectorAll('input[name="language"]').forEach(input=>{input.onchange=()=>{if(state.resolved)renderResolved(state.resolved)}});
document.querySelectorAll('input[name="commentary_style"]').forEach(input=>{input.onchange=async()=>{try{const data=await json('/api/setup/style',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({commentary_style:selectedStyle()})});showEffectiveStyle(data.commentary_style);setMessage(`播报语气已切换为 ${data.commentary_style}`,'ok')}catch(error){setMessage(error.message,'error')}}});
$('apply').onclick=async()=>{const favorite=document.querySelector('input[name="favorite_team"]:checked')?.value||'';const position=document.querySelector('input[name="position_team"]:checked')?.value||'';const language=document.querySelector('input[name="language"]:checked')?.value||'zh';const commentary_style=selectedStyle();setMessage('正在切换 watcher');try{const data=await json('/api/setup/apply',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({kalshi_url:$('kalshi').value,event_ticker:state.resolved.event_ticker,espn_event_id:$('espn').value,favorite_team:favorite,position_team:position,language,commentary_style})});setMessage(`${data.label} 已开始监控`,'ok');$('current').textContent=`${data.label} · 支持 ${favorite||'中立'} · 持仓 ${position||'无'} · ${data.commentary_style}`}catch(error){setMessage(error.message,'error')}};
boot();
setInterval(refreshEffectiveStyle,5000);
</script>
</body>
</html>"""


class SetupRequestHandler(BaseHTTPRequestHandler):
    server_version = "StackchanSetup/0.1"

    @property
    def service(self) -> MatchSetupService:
        return self.server.service  # type: ignore[attr-defined,no-any-return]

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload: Any, status: int = 200) -> None:
        self._send(
            status,
            json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            "application/json; charset=utf-8",
        )

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._send(204, b"", "text/plain")

    def do_GET(self) -> None:  # noqa: N802
        path = urllib.parse.urlparse(self.path).path
        try:
            if path in {"/", "/setup"}:
                self._send(200, setup_page_html().encode("utf-8"), "text/html; charset=utf-8")
                return
            if path == "/health":
                self._json({"ok": True})
                return
            if path == "/api/setup/status":
                self._json(self.service.current_status())
                return
            if path == "/api/setup/upcoming":
                self._json({"matches": self.service.setup_options()})
                return
            self._json({"error": "not found"}, 404)
        except (OSError, ValueError, json.JSONDecodeError, urllib.error.URLError) as error:
            self._json({"error": str(error)}, 500)

    def do_POST(self) -> None:  # noqa: N802
        path = urllib.parse.urlparse(self.path).path
        try:
            length = min(int(self.headers.get("Content-Length") or "0"), 65536)
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("请求内容必须是 JSON object")
            if path == "/api/setup/resolve":
                self._json(self.service.resolve_kalshi(str(payload.get("kalshi_url") or "")))
                return
            if path == "/api/setup/apply":
                self._json(self.service.apply_selection(payload))
                return
            if path == "/api/setup/style":
                self._json(self.service.apply_commentary_style(payload))
                return
            self._json({"error": "not found"}, 404)
        except (OSError, ValueError, json.JSONDecodeError, urllib.error.URLError) as error:
            self._json({"error": str(error)}, 400)

    def log_message(self, _format: str, *_args: Any) -> None:
        return


def start_setup_server(
    service: MatchSetupService,
    host: str,
    port: int,
) -> tuple[ThreadingHTTPServer, threading.Thread]:
    server = ThreadingHTTPServer((host, port), SetupRequestHandler)
    server.daemon_threads = True
    server.service = service  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, name="stackchan-setup", daemon=True)
    thread.start()
    return server, thread
