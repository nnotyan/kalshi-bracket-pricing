"""Thin client for the public half of the Kalshi trade API.

Only the market-data endpoints are used, and those need no credentials, so
nothing here signs a request or touches an API key. The client exists mostly to
absorb three annoyances: cursor pagination, the mixture of integer-cent and
decimal-string price fields that the API returns depending on the endpoint, and
the fact that a 429 shows up the moment you poll more than a couple of series
at once.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Iterator

import requests

log = logging.getLogger(__name__)

BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"

# Prices come back either as integer cents ("yes_bid") or as a decimal string of
# dollars ("yes_bid_dollars"), and which one you get depends on the endpoint and
# on how recently Kalshi touched it. Sizes have the same problem with an "_fp"
# suffix. Everything downstream works in cents, so normalise on the way in.
_PRICE_FIELDS = ("yes_bid", "yes_ask", "no_bid", "no_ask", "last_price", "previous_price",
                 "previous_yes_bid", "previous_yes_ask")
_SIZE_FIELDS = ("yes_bid_size", "yes_ask_size", "volume", "volume_24h", "open_interest",
                "liquidity")


class KalshiError(RuntimeError):
    pass


def _to_cents(raw: Any) -> float | None:
    """Return a price in cents from either an int-cent or a dollar-string field."""
    if raw is None or raw == "":
        return None
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int,)):
        return float(raw)
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return None
    # Dollar strings are always written with a decimal point; integer cents never
    # are. "0.1100" -> 11.0 cents, 11 -> 11.0 cents.
    if isinstance(raw, str) and "." in raw:
        return round(val * 100.0, 4)
    return float(val)


def _to_number(raw: Any) -> float | None:
    if raw is None or raw == "":
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def normalise_market(market: dict) -> dict:
    """Flatten one raw market dict into the field names the rest of the code uses."""
    out = dict(market)
    for name in _PRICE_FIELDS:
        value = market.get(name)
        if value is None:
            value = market.get(f"{name}_dollars")
        out[name] = _to_cents(value)
    for name in _SIZE_FIELDS:
        value = market.get(name)
        if value is None:
            value = market.get(f"{name}_fp")
        if value is None:
            value = market.get(f"{name}_dollars")
        out[name] = _to_number(value)
    out["floor_strike"] = _to_number(market.get("floor_strike"))
    out["cap_strike"] = _to_number(market.get("cap_strike"))
    return out


@dataclass
class KalshiClient:
    base_url: str = BASE_URL
    timeout: float = 20.0
    max_retries: int = 4
    # The public endpoints tolerate a steady poll but will start returning 429 if
    # several series are fetched back to back with no gap at all.
    min_interval: float = 0.12
    session: requests.Session = field(default_factory=requests.Session)
    _last_call: float = field(default=0.0, init=False, repr=False)

    def __post_init__(self) -> None:
        self.session.headers.update({
            "Accept": "application/json",
            "User-Agent": "kalshi-brackets/0.3 (research)",
        })

    def _throttle(self) -> None:
        gap = time.monotonic() - self._last_call
        if gap < self.min_interval:
            time.sleep(self.min_interval - gap)
        self._last_call = time.monotonic()

    def get(self, path: str, **params: Any) -> dict:
        url = f"{self.base_url}{path}"
        params = {k: v for k, v in params.items() if v is not None}
        backoff = 0.6
        last_exc: Exception | None = None
        for attempt in range(self.max_retries):
            self._throttle()
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout)
            except requests.RequestException as exc:
                last_exc = exc
            else:
                if resp.status_code == 200:
                    return resp.json()
                if resp.status_code in (429, 500, 502, 503, 504):
                    last_exc = KalshiError(f"{resp.status_code} from {path}")
                else:
                    raise KalshiError(f"{resp.status_code} from {path}: {resp.text[:200]}")
            sleep_for = backoff * (2 ** attempt)
            log.debug("retrying %s in %.1fs (%s)", path, sleep_for, last_exc)
            time.sleep(sleep_for)
        raise KalshiError(f"gave up on {path} after {self.max_retries} tries") from last_exc

    def _paginate(self, path: str, key: str, limit: int = 200, max_pages: int = 50,
                  **params: Any) -> Iterator[dict]:
        cursor = None
        for _ in range(max_pages):
            payload = self.get(path, limit=limit, cursor=cursor, **params)
            rows = payload.get(key) or []
            for row in rows:
                yield row
            cursor = payload.get("cursor")
            if not cursor or not rows:
                return

    # -- endpoints -----------------------------------------------------------

    def events(self, status: str = "open", series_ticker: str | None = None,
               with_markets: bool = True, max_pages: int = 50) -> list[dict]:
        rows = self._paginate(
            "/events", "events",
            status=status,
            series_ticker=series_ticker,
            with_nested_markets=str(bool(with_markets)).lower(),
            max_pages=max_pages,
        )
        return [self._normalise_event(e) for e in rows]

    def event(self, event_ticker: str, with_markets: bool = True) -> dict:
        payload = self.get(f"/events/{event_ticker}",
                           with_nested_markets=str(bool(with_markets)).lower())
        event = payload.get("event", payload)
        if "markets" not in event and payload.get("markets"):
            event["markets"] = payload["markets"]
        return self._normalise_event(event)

    def markets(self, event_ticker: str | None = None, series_ticker: str | None = None,
                status: str = "open", max_pages: int = 50) -> list[dict]:
        rows = self._paginate("/markets", "markets", status=status,
                              event_ticker=event_ticker, series_ticker=series_ticker,
                              max_pages=max_pages)
        return [normalise_market(m) for m in rows]

    @staticmethod
    def _normalise_event(event: dict) -> dict:
        out = dict(event)
        out["markets"] = [normalise_market(m) for m in event.get("markets") or []]
        return out
