#!/usr/bin/env python3
"""Venue adapters: normalized prediction-market quotes across platforms.

Implements the VenueAdapter axis of docs/multi-venue-roadmap-prd.zh-CN.md
(section 4.1/4.2). Each adapter converts one platform's native units into
the shared VenueQuote model (probabilities 0.0-1.0, money in USD) so the
aggregation layer never sees platform-specific cents or share prices.

Standard library only; HTTP is injected so callers own retries and tests
stub the network without patching urllib.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Protocol, Sequence

KALSHI_BASE_URL = "https://external-api.kalshi.com/trade-api/v2"
POLYMARKET_BASE_URL = "https://gamma-api.polymarket.com"
REQUEST_TIMEOUT_SECONDS = 12
USER_AGENT = "stackchan-matchday-watch/0.1"

# Quotes whose book is wider than this are dropped from aggregation when a
# tighter source exists (PRD section 8: wide spreads mean stale/thin books).
AGGREGATION_SPREAD_CAP = 0.15
# Two venues disagreeing on the same outcome by at least this much is a
# reportable "market divergence" fact (PRD section 4.2 suggests 8 points).
DIVERGENCE_THRESHOLD = 0.08

FetchJson = Callable[[str], Any]


def default_fetch_json(url: str) -> Any:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        return json.loads(response.read().decode("utf-8"))


@dataclass
class VenueQuote:
    venue: str
    market_id: str
    outcome: str
    prob_mid: float | None
    bid: float | None
    ask: float | None
    volume_usd: float | None
    liquidity_usd: float | None
    status: str  # open | paused | closed | settled
    close_time: datetime | None
    fetched_at: datetime

    def spread(self) -> float | None:
        if self.bid is None or self.ask is None:
            return None
        return max(0.0, self.ask - self.bid)


@dataclass
class VenueMarketMeta:
    venue: str
    market_id: str
    title: str
    outcomes: list[str]
    status: str
    close_time: datetime | None


class VenueAdapter(Protocol):
    venue: str

    def discover(self, category: str, days: int) -> list[dict[str, Any]]: ...

    def quotes(self, market_refs: Sequence[Any]) -> list[VenueQuote]: ...

    def metadata(self, market_ref: Any) -> VenueMarketMeta | None: ...


def parse_iso_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _as_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# --- Kalshi ----------------------------------------------------------------

_KALSHI_SETTLED_STATUSES = {"settled", "finalized", "determined"}
_KALSHI_CLOSED_STATUSES = {"closed", "inactive"}


def _kalshi_status(raw_status: str, result: str) -> str:
    status = raw_status.strip().lower()
    if status in _KALSHI_SETTLED_STATUSES or result:
        return "settled"
    if status in _KALSHI_CLOSED_STATUSES:
        return "closed"
    if status == "paused":
        return "paused"
    return "open"


class KalshiVenueAdapter:
    """Kalshi trade-api v2. Prices arrive in dollars-per-contract (0-1)."""

    venue = "kalshi"

    def __init__(self, base_url: str = KALSHI_BASE_URL, fetch: FetchJson = default_fetch_json):
        self.base_url = base_url.rstrip("/")
        self._fetch = fetch

    def raw_markets(self, tickers: Sequence[str]) -> list[dict[str, Any]]:
        query = urllib.parse.urlencode(
            {"tickers": ",".join(tickers), "limit": str(max(100, len(tickers)))}
        )
        payload = self._fetch(f"{self.base_url}/markets?{query}")
        return payload.get("markets", [])

    def quotes(self, market_refs: Sequence[str]) -> list[VenueQuote]:
        fetched_at = datetime.now(timezone.utc)
        wanted = {str(ref).upper() for ref in market_refs}
        quotes = []
        for market in self.raw_markets(sorted(wanted)):
            ticker = str(market.get("ticker", "")).upper()
            if ticker not in wanted:
                continue
            quotes.append(self._quote_from_raw(market, fetched_at))
        return quotes

    def _quote_from_raw(self, market: dict[str, Any], fetched_at: datetime) -> VenueQuote:
        bid = _as_float(market.get("yes_bid_dollars"))
        ask = _as_float(market.get("yes_ask_dollars"))
        last = _as_float(market.get("last_price_dollars"))
        settlement = _as_float(market.get("settlement_value_dollars"))
        result = str(market.get("result") or "").strip().lower()
        status = _kalshi_status(str(market.get("status", "")), result)
        if status == "settled":
            if settlement is not None:
                prob = settlement
            elif result == "yes":
                prob = 1.0
            elif result == "no":
                prob = 0.0
            else:
                prob = last
        elif bid is not None and ask is not None:
            prob = (bid + ask) / 2
        else:
            prob = last
        # Contracts settle at $1, so 24h contract volume doubles as an upper
        # bound on USD notional; good enough as a popularity signal.
        return VenueQuote(
            venue=self.venue,
            market_id=str(market.get("ticker", "")).upper(),
            outcome="yes",
            prob_mid=prob,
            bid=bid,
            ask=ask,
            volume_usd=_as_float(market.get("volume_24h_fp") or market.get("volume_24h")),
            liquidity_usd=_as_float(market.get("liquidity_dollars") or market.get("liquidity")),
            status=status,
            close_time=parse_iso_datetime(market.get("close_time")),
            fetched_at=fetched_at,
        )

    def metadata(self, market_ref: str) -> VenueMarketMeta | None:
        payload = self._fetch(f"{self.base_url}/markets/{urllib.parse.quote(str(market_ref))}")
        market = payload.get("market") if isinstance(payload, dict) else None
        if not market:
            return None
        result = str(market.get("result") or "").strip().lower()
        return VenueMarketMeta(
            venue=self.venue,
            market_id=str(market.get("ticker", "")).upper(),
            title=str(market.get("title") or market.get("yes_sub_title") or ""),
            outcomes=["yes", "no"],
            status=_kalshi_status(str(market.get("status", "")), result),
            close_time=parse_iso_datetime(market.get("close_time")),
        )

    def discover(self, category: str, days: int) -> list[dict[str, Any]]:
        params = {"status": "open", "limit": "200", "with_nested_markets": "true"}
        if category:
            params["series_ticker"] = category
        payload = self._fetch(f"{self.base_url}/events?" + urllib.parse.urlencode(params))
        now = datetime.now(timezone.utc)
        events = []
        for event in payload.get("events", []):
            markets = event.get("markets") or []
            close_times = [parse_iso_datetime(m.get("close_time")) for m in markets]
            close_times = [t for t in close_times if t is not None]
            soonest = min(close_times) if close_times else None
            if soonest is not None and (soonest - now).days > days:
                continue
            events.append(
                {
                    "venue": self.venue,
                    "event_id": event.get("event_ticker"),
                    "title": event.get("title"),
                    "sub_title": event.get("sub_title"),
                    "close_time": soonest.isoformat() if soonest else None,
                    "markets": [m.get("ticker") for m in markets],
                }
            )
        events.sort(key=lambda item: item.get("close_time") or "9999")
        return events


# --- Polymarket ------------------------------------------------------------


@dataclass
class PolymarketMarketRef:
    """One Gamma market plus which of its outcomes we want quotes for.

    ``outcomes`` maps the canonical outcome name used by the caller to the
    outcome label as it appears in the Gamma ``outcomes`` array. Empty means
    "quote every outcome under its native label".
    """

    market_id: str
    outcomes: dict[str, str] | None = None


def _decode_gamma_list(value: Any) -> list[Any]:
    # Gamma returns list fields (outcomes, outcomePrices, clobTokenIds) as
    # JSON-encoded strings.
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return []
    return value if isinstance(value, list) else []


def _polymarket_status(market: dict[str, Any]) -> str:
    if market.get("umaResolutionStatus") == "resolved":
        return "settled"
    if market.get("closed"):
        return "closed"
    if market.get("active") is False:
        return "paused"
    return "open"


class PolymarketVenueAdapter:
    """Polymarket Gamma API: read-only, no auth, ~60 req/min budget."""

    venue = "polymarket"

    def __init__(
        self,
        base_url: str = POLYMARKET_BASE_URL,
        fetch: FetchJson = default_fetch_json,
    ):
        self.base_url = base_url.rstrip("/")
        self._fetch = fetch

    def _raw_markets(self, market_ids: Sequence[str]) -> list[dict[str, Any]]:
        if not market_ids:
            return []
        query = "&".join(
            "id=" + urllib.parse.quote(str(market_id)) for market_id in market_ids
        )
        payload = self._fetch(f"{self.base_url}/markets?{query}")
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            return payload.get("markets", []) or payload.get("data", [])
        return []

    def quotes(self, market_refs: Sequence[PolymarketMarketRef]) -> list[VenueQuote]:
        refs_by_id = {str(ref.market_id): ref for ref in market_refs}
        fetched_at = datetime.now(timezone.utc)
        quotes: list[VenueQuote] = []
        for market in self._raw_markets(list(refs_by_id)):
            ref = refs_by_id.get(str(market.get("id", "")))
            if ref is None:
                continue
            quotes.extend(self._market_quotes(market, ref, fetched_at))
        return quotes

    def _market_quotes(
        self,
        market: dict[str, Any],
        ref: PolymarketMarketRef,
        fetched_at: datetime,
    ) -> list[VenueQuote]:
        outcomes = [str(name) for name in _decode_gamma_list(market.get("outcomes"))]
        prices = [_as_float(p) for p in _decode_gamma_list(market.get("outcomePrices"))]
        status = _polymarket_status(market)
        volume = _as_float(market.get("volume24hr") or market.get("volumeNum") or market.get("volume"))
        liquidity = _as_float(market.get("liquidityNum") or market.get("liquidity"))
        close_time = parse_iso_datetime(market.get("endDate"))
        best_bid = _as_float(market.get("bestBid"))
        best_ask = _as_float(market.get("bestAsk"))

        if ref.outcomes:
            gamma_to_canonical = {
                gamma_name.casefold(): canonical
                for canonical, gamma_name in ref.outcomes.items()
            }
        else:
            gamma_to_canonical = {name.casefold(): name for name in outcomes}

        quotes = []
        for index, name in enumerate(outcomes):
            canonical = gamma_to_canonical.get(name.casefold())
            if canonical is None:
                continue
            price = prices[index] if index < len(prices) else None
            # bestBid/bestAsk describe the first outcome's book; mirror them
            # for the complementary side of a binary market only.
            bid = ask = None
            if index == 0:
                bid, ask = best_bid, best_ask
            elif len(outcomes) == 2 and best_bid is not None and best_ask is not None:
                bid, ask = 1.0 - best_ask, 1.0 - best_bid
            quotes.append(
                VenueQuote(
                    venue=self.venue,
                    market_id=str(market.get("id", "")),
                    outcome=canonical,
                    prob_mid=price,
                    bid=bid,
                    ask=ask,
                    volume_usd=volume,
                    liquidity_usd=liquidity,
                    status=status,
                    close_time=close_time,
                    fetched_at=fetched_at,
                )
            )
        return quotes

    def metadata(self, market_ref: PolymarketMarketRef | str) -> VenueMarketMeta | None:
        market_id = (
            market_ref.market_id
            if isinstance(market_ref, PolymarketMarketRef)
            else str(market_ref)
        )
        markets = self._raw_markets([market_id])
        if not markets:
            return None
        market = markets[0]
        return VenueMarketMeta(
            venue=self.venue,
            market_id=str(market.get("id", "")),
            title=str(market.get("question") or ""),
            outcomes=[str(name) for name in _decode_gamma_list(market.get("outcomes"))],
            status=_polymarket_status(market),
            close_time=parse_iso_datetime(market.get("endDate")),
        )

    def discover(self, category: str, days: int) -> list[dict[str, Any]]:
        params = {
            "closed": "false",
            "limit": "100",
            "order": "volume24hr",
            "ascending": "false",
        }
        if category:
            params["tag_slug"] = category
        payload = self._fetch(f"{self.base_url}/events?" + urllib.parse.urlencode(params))
        events_raw = payload if isinstance(payload, list) else payload.get("events", [])
        events = []
        for event in events_raw:
            events.append(
                {
                    "venue": self.venue,
                    "event_id": event.get("id"),
                    "title": event.get("title"),
                    "sub_title": event.get("slug"),
                    "close_time": event.get("endDate"),
                    "markets": [m.get("id") for m in event.get("markets") or []],
                }
            )
        return events


# --- Aggregation -----------------------------------------------------------


def aggregate_probability(quotes: Sequence[VenueQuote]) -> float | None:
    """Liquidity-weighted mid across venues for one canonical outcome.

    Settled quotes are ground truth and win outright. Open quotes with a
    spread wider than AGGREGATION_SPREAD_CAP are dropped when at least one
    tighter book exists. Liquidity weighting applies only when every
    surviving quote reports liquidity; otherwise all sources weigh equally
    (mixing known and unknown depth would silently bias the mix).
    """
    priced = [q for q in quotes if q.prob_mid is not None]
    if not priced:
        return None
    settled = [q for q in priced if q.status == "settled"]
    if settled:
        return settled[-1].prob_mid
    live = [q for q in priced if q.status == "open"]
    if not live:
        return priced[-1].prob_mid
    tight = [q for q in live if q.spread() is None or q.spread() <= AGGREGATION_SPREAD_CAP]
    if tight:
        live = tight
    weights = [q.liquidity_usd for q in live]
    if all(w is not None and w > 0 for w in weights):
        total = sum(weights)
        return sum(q.prob_mid * w for q, w in zip(live, weights)) / total
    return sum(q.prob_mid for q in live) / len(live)


@dataclass
class VenueDivergence:
    outcome: str
    quote_a: VenueQuote
    quote_b: VenueQuote
    gap: float


def max_divergence(
    quotes: Sequence[VenueQuote],
    threshold: float = DIVERGENCE_THRESHOLD,
) -> VenueDivergence | None:
    """Largest cross-venue gap on the same outcome, if it clears threshold."""
    open_quotes = [q for q in quotes if q.status == "open" and q.prob_mid is not None]
    worst: VenueDivergence | None = None
    for i, quote_a in enumerate(open_quotes):
        for quote_b in open_quotes[i + 1 :]:
            if quote_a.venue == quote_b.venue or quote_a.outcome != quote_b.outcome:
                continue
            gap = abs(quote_a.prob_mid - quote_b.prob_mid)
            if gap >= threshold and (worst is None or gap > worst.gap):
                worst = VenueDivergence(quote_a.outcome, quote_a, quote_b, gap)
    return worst


def same_direction_jump(delta_a: float | None, delta_b: float | None, min_abs: float) -> bool:
    """True when two venues moved the same way by at least min_abs each.

    This is the multi-source upgrade of the goal signal: one venue jumping is
    "possible event, wait for confirmation"; two venues jumping together is a
    high-confidence event regardless of category.
    """
    if delta_a is None or delta_b is None:
        return False
    if abs(delta_a) < min_abs or abs(delta_b) < min_abs:
        return False
    return (delta_a > 0) == (delta_b > 0)
