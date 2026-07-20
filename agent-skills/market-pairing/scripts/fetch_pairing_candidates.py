#!/usr/bin/env python3
"""Fetch trimmed pairing candidates from Kalshi, Polymarket, or ESPN.

Emits compact JSON on stdout so an agent can match events across venues
without loading raw API responses into context. Standard library only.

Examples:
    fetch_pairing_candidates.py --source kalshi --days 7 --query yankees
    fetch_pairing_candidates.py --source kalshi --list-series --query baseball
    fetch_pairing_candidates.py --source polymarket --tag mlb --days 7
    fetch_pairing_candidates.py --source espn --espn-league baseball/mlb --days 7
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

KALSHI_BASE = "https://external-api.kalshi.com/trade-api/v2"
POLYMARKET_BASE = "https://gamma-api.polymarket.com"
ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports"
USER_AGENT = "stackchan-matchday-pairing/1.0"
TIMEOUT_SECONDS = 20


def fetch_json(url: str) -> object:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        return json.loads(response.read().decode("utf-8"))


def parse_iso(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def within_window(when: datetime | None, now: datetime, days: int) -> bool:
    if when is None:
        return True  # keep candidates with unknown timestamps; agent decides
    return now - timedelta(hours=12) <= when <= now + timedelta(days=days)


def text_matches(query: str, *fields: object) -> bool:
    if not query:
        return True
    needle = query.casefold()
    return any(needle in str(field).casefold() for field in fields if field)


# --- Kalshi ---------------------------------------------------------------


def kalshi_series(args: argparse.Namespace) -> list[dict]:
    url = f"{args.kalshi_base}/series?category=" + urllib.parse.quote(
        args.kalshi_category
    )
    payload = fetch_json(url)
    out = []
    for series in payload.get("series", []):
        if not text_matches(args.query, series.get("ticker"), series.get("title"),
                            series.get("category")):
            continue
        out.append({
            "series_ticker": series.get("ticker"),
            "title": series.get("title"),
            "category": series.get("category"),
            "frequency": series.get("frequency"),
        })
    return out


def kalshi_events(args: argparse.Namespace, now: datetime) -> list[dict]:
    candidates: list[dict] = []
    cursor = ""
    pages = 0
    while pages < args.max_pages:
        params = {"status": "open", "limit": "200", "with_nested_markets": "true"}
        if args.kalshi_series:
            params["series_ticker"] = args.kalshi_series
        if cursor:
            params["cursor"] = cursor
        url = f"{args.kalshi_base}/events?" + urllib.parse.urlencode(params)
        payload = fetch_json(url)
        for event in payload.get("events", []):
            markets = event.get("markets") or []
            close_times = [parse_iso(m.get("close_time")) for m in markets]
            close_times = [t for t in close_times if t is not None]
            soonest_close = min(close_times) if close_times else None
            if not within_window(soonest_close, now, args.days):
                continue
            if not text_matches(args.query, event.get("event_ticker"),
                                event.get("title"), event.get("sub_title")):
                continue
            trimmed_markets = []
            for market in markets[: args.max_markets_per_event]:
                trimmed_markets.append({
                    "ticker": market.get("ticker"),
                    "yes_sub_title": market.get("yes_sub_title")
                    or market.get("subtitle") or market.get("title"),
                    "status": market.get("status"),
                    "close_time": market.get("close_time"),
                    "volume": market.get("volume"),
                    "open_interest": market.get("open_interest"),
                    "yes_bid": market.get("yes_bid"),
                    "yes_ask": market.get("yes_ask"),
                })
            candidates.append({
                "event_ticker": event.get("event_ticker"),
                "series_ticker": event.get("series_ticker"),
                "title": event.get("title"),
                "sub_title": event.get("sub_title"),
                "soonest_close": soonest_close.isoformat() if soonest_close else None,
                "markets": trimmed_markets,
            })
        cursor = payload.get("cursor") or ""
        pages += 1
        if not cursor:
            break
    candidates.sort(key=lambda c: c.get("soonest_close") or "9999")
    return candidates[: args.limit]


# --- Polymarket -----------------------------------------------------------


def polymarket_events(args: argparse.Namespace, now: datetime) -> list[dict]:
    params = {
        "closed": "false",
        "limit": "100",  # always fetch a full page; text filter happens locally
        "order": "volume24hr",
        "ascending": "false",
    }
    if args.tag:
        params["tag_slug"] = args.tag
    url = f"{POLYMARKET_BASE}/events?" + urllib.parse.urlencode(params)
    payload = fetch_json(url)
    events = payload if isinstance(payload, list) else payload.get("events", [])
    candidates = []
    for event in events:
        end_date = parse_iso(event.get("endDate"))
        # endDate often trails the actual start time (settlement buffer,
        # multi-game bundles), so allow extra slack beyond --days.
        if not within_window(end_date, now, args.days + 3):
            continue
        if not text_matches(args.query, event.get("title"), event.get("slug")):
            continue
        def decode(value: object) -> object:
            # Gamma often returns these fields as JSON-encoded strings.
            if isinstance(value, str):
                try:
                    return json.loads(value)
                except ValueError:
                    return value
            return value

        trimmed_markets = []
        for market in (event.get("markets") or [])[: args.max_markets_per_event]:
            trimmed_markets.append({
                "question": market.get("question"),
                "condition_id": market.get("conditionId"),
                "outcomes": decode(market.get("outcomes")),
                "outcome_prices": decode(market.get("outcomePrices")),
                "clob_token_ids": decode(market.get("clobTokenIds")),
                "volume": market.get("volume"),
                "liquidity": market.get("liquidity"),
                "end_date": market.get("endDate"),
                "active": market.get("active"),
            })
        candidates.append({
            "event_id": event.get("id"),
            "slug": event.get("slug"),
            "title": event.get("title"),
            "end_date": event.get("endDate"),
            "volume": event.get("volume"),
            "liquidity": event.get("liquidity"),
            "markets": trimmed_markets,
        })
    return candidates[: args.limit]


# --- ESPN -----------------------------------------------------------------


def espn_events(args: argparse.Namespace, now: datetime) -> list[dict]:
    if not args.espn_league:
        raise SystemExit("--espn-league is required for --source espn "
                         "(e.g. baseball/mlb, soccer/eng.1, tennis/atp)")
    start = now.strftime("%Y%m%d")
    end = (now + timedelta(days=args.days)).strftime("%Y%m%d")
    url = (f"{ESPN_BASE}/{args.espn_league}/scoreboard?"
           + urllib.parse.urlencode({"dates": f"{start}-{end}", "limit": "300"}))
    payload = fetch_json(url)
    candidates = []
    for event in payload.get("events", []):
        if not text_matches(args.query, event.get("name"), event.get("shortName")):
            continue
        competitors = []
        for competition in (event.get("competitions") or [])[:1]:
            for competitor in competition.get("competitors", []):
                team = competitor.get("team") or competitor.get("athlete") or {}
                competitors.append({
                    "home_away": competitor.get("homeAway"),
                    "abbreviation": team.get("abbreviation"),
                    "display_name": team.get("displayName")
                    or team.get("fullName"),
                })
        candidates.append({
            "event_id": event.get("id"),
            "name": event.get("name"),
            "short_name": event.get("shortName"),
            "date": event.get("date"),
            "competitors": competitors,
        })
    candidates.sort(key=lambda c: c.get("date") or "9999")
    return candidates[: args.limit]


# --- main -----------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True,
                        choices=["kalshi", "polymarket", "espn"])
    parser.add_argument("--days", type=int, default=7,
                        help="lookahead window in days (default 7)")
    parser.add_argument("--query", default="",
                        help="case-insensitive text filter on titles/tickers")
    parser.add_argument("--limit", type=int, default=25,
                        help="max candidates in output (default 25)")
    parser.add_argument("--max-markets-per-event", type=int, default=8)
    parser.add_argument("--kalshi-base", default=KALSHI_BASE)
    parser.add_argument("--kalshi-series", default="",
                        help="filter Kalshi events by series ticker")
    parser.add_argument("--kalshi-category", default="Sports",
                        help="category for --list-series (default Sports)")
    parser.add_argument("--list-series", action="store_true",
                        help="list Kalshi series instead of events")
    parser.add_argument("--max-pages", type=int, default=3,
                        help="max Kalshi pagination pages (200 events each)")
    parser.add_argument("--tag", default="",
                        help="Polymarket tag_slug filter (e.g. mlb, nba, epl)")
    parser.add_argument("--espn-league", default="",
                        help="ESPN sport/league path (e.g. baseball/mlb)")
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    try:
        if args.source == "kalshi" and args.list_series:
            candidates = kalshi_series(args)
        elif args.source == "kalshi":
            candidates = kalshi_events(args, now)
        elif args.source == "polymarket":
            candidates = polymarket_events(args, now)
        else:
            candidates = espn_events(args, now)
    except Exception as error:  # noqa: BLE001 - agent needs the reason
        json.dump({"source": args.source, "error": str(error)}, sys.stdout)
        print()
        raise SystemExit(1)

    json.dump({
        "source": args.source,
        "fetched_at": now.isoformat(),
        "count": len(candidates),
        "candidates": candidates,
    }, sys.stdout, ensure_ascii=False, indent=1)
    print()


if __name__ == "__main__":
    main()
