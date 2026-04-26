"""Fetch the most-moved Polymarket markets in the last 24 hours.

Used by the "movers" content type — instead of a single trader's portfolio,
the script narrates the 5 prediction-market moves of the day (the political
race that flipped, the sports underdog that rallied, the crypto futures
contract that crashed, etc.).

Endpoint we use:
    GET https://gamma-api.polymarket.com/markets
        ?active=true&closed=false&archived=false&limit=200
        &order=volume24hr&ascending=false
    → list of dicts with: question, slug, outcomePrices, volume, volumeNum,
      volume24hr, oneDayPriceChange, lastTradePrice, endDate, ...

Most active markets (top 200 by 24h volume) are the only ones worth
narrating — sub-$1K-volume markets aren't where the story lives.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, List, Optional

import requests
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from src.utils.logger import log
from src.utils.models import MarketMover, MoversStory


GAMMA_BASE = "https://gamma-api.polymarket.com"


class PolymarketMoversClient:
    """Top-N gainers/losers across active Polymarket markets."""

    def __init__(
        self,
        top_n: int = 5,
        scan_limit: int = 200,
        min_volume_24hr: float = 5_000.0,
        min_abs_change_pts: float = 5.0,
        timeout: int = 20,
    ):
        self._top_n = top_n
        self._scan_limit = scan_limit
        self._min_volume_24hr = min_volume_24hr
        self._min_abs_change_pts = min_abs_change_pts
        self._timeout = timeout

    # ------------------------------------------------------------------
    def fetch(self) -> MoversStory:
        log.info(
            "Fetching Polymarket movers (scan_limit={}, top_n={}, min_vol_24h=${:,.0f})",
            self._scan_limit, self._top_n, self._min_volume_24hr,
        )
        try:
            raw_markets = self._fetch_markets()
        except requests.RequestException as exc:
            log.error("Gamma /markets request failed: {}", exc)
            return MoversStory(movers=[], fetched_at=datetime.now(timezone.utc))

        log.info("Gamma returned {} markets", len(raw_markets))

        movers: List[MarketMover] = []
        for m in raw_markets:
            try:
                mover = self._to_mover(m)
                if mover is None:
                    continue
                if mover.volume_24hr < self._min_volume_24hr:
                    continue
                if abs(mover.change_pts) < self._min_abs_change_pts:
                    continue
                movers.append(mover)
            except Exception as exc:  # noqa: BLE001
                log.debug("Skipping malformed market: {}", exc)

        # Sort by absolute price change descending — biggest swings first.
        movers.sort(key=lambda x: abs(x.change_pts), reverse=True)
        selected = movers[: self._top_n]

        for m in selected:
            log.info(
                "  {}{:>+5.1f}pts  {:>3}% (was {:>3}%)  vol24h=${:>10,.0f}  {}",
                "+" if m.change_pts > 0 else "",
                m.change_pts, m.implied_pct, m.previous_pct,
                m.volume_24hr, m.question[:70],
            )

        log.info(
            "Selected {} movers (filtered from {} qualifying out of {} markets)",
            len(selected), len(movers), len(raw_markets),
        )

        return MoversStory(
            movers=selected,
            window_hours=24,
            fetched_at=datetime.now(timezone.utc),
        )

    # ------------------------------------------------------------------
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=2, max=20),
        retry=retry_if_exception_type(requests.RequestException),
        reraise=True,
    )
    def _fetch_markets(self) -> List[dict]:
        resp = requests.get(
            f"{GAMMA_BASE}/markets",
            params={
                "active": "true",
                "closed": "false",
                "archived": "false",
                "limit": self._scan_limit,
                "order": "volume24hr",
                "ascending": "false",
            },
            timeout=self._timeout,
            headers={"Accept": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for k in ("data", "markets", "results"):
                if isinstance(data.get(k), list):
                    return data[k]
        return []

    # ------------------------------------------------------------------
    def _to_mover(self, m: dict) -> Optional[MarketMover]:
        slug = m.get("slug") or ""
        question = m.get("question") or m.get("title") or ""
        if not slug or not question:
            return None

        # Some markets list outcome prices as a JSON-encoded string.
        current_price = self._extract_yes_price(m)
        if current_price is None:
            return None

        change = _coerce_float(m.get("oneDayPriceChange"))
        if change == 0.0:
            # No movement → not a story. (Genuine 0.0 changes are common on
            # very illiquid markets; skip them.)
            return None
        previous_price = max(0.0, min(1.0, current_price - change))

        volume_24hr = _coerce_float(
            m.get("volume24hr") or m.get("oneDayVolume") or 0.0
        )

        return MarketMover(
            slug=slug,
            question=question,
            current_price=current_price,
            previous_price=previous_price,
            volume_24hr=volume_24hr,
            end_date=m.get("endDate") or m.get("end_date_iso"),
            category=(m.get("category") or "").strip() or None,
            raw={"gamma": m},
        )

    @staticmethod
    def _extract_yes_price(m: dict) -> Optional[float]:
        """The YES-side implied probability (0.0–1.0).

        Gamma surfaces this in several places depending on the market type;
        we try them in priority order.
        """
        last = _coerce_float(m.get("lastTradePrice"))
        if 0.0 < last < 1.0:
            return last

        outcome_prices = m.get("outcomePrices")
        if isinstance(outcome_prices, str):
            try:
                import json
                outcome_prices = json.loads(outcome_prices)
            except (json.JSONDecodeError, TypeError):
                outcome_prices = None
        if isinstance(outcome_prices, list) and outcome_prices:
            try:
                p = float(outcome_prices[0])
                if 0.0 <= p <= 1.0:
                    return p
            except (TypeError, ValueError):
                pass

        bid = _coerce_float(m.get("bestBid"))
        ask = _coerce_float(m.get("bestAsk"))
        if bid > 0 and ask > 0:
            return (bid + ask) / 2.0

        return None


def _coerce_float(value: Any, default: float = 0.0) -> float:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
