"""Fetch high-performing Polymarket users + their open positions.

Polymarket exposes two public-ish HTTP services we use here:

* `lb-api.polymarket.com`  – leaderboard endpoints (profit / volume).
* `data-api.polymarket.com` – per-user positions + activity (trades).

Neither is officially documented as a "public API," so endpoints can drift
without notice. Every request is wrapped in tenacity retries, and any HTTP /
JSON failure degrades gracefully (we just skip that user and log the issue
rather than aborting the whole run).

Verified against the live API on 2026-04-24:

* leaderboard:
    GET https://lb-api.polymarket.com/profit?interval=<1d|1w|1m|all>&limit=N
    → [{proxyWallet, name, pseudonym, amount, ...}, ...]
* positions:
    GET https://data-api.polymarket.com/positions?user=<addr>&sizeThreshold=1
    → [{title, slug, outcome, size, avgPrice, curPrice, cashPnl, ...}, ...]
* activity (used to count trades + recency, NOT pnl — that field is absent):
    GET https://data-api.polymarket.com/activity?user=<addr>&type=TRADE&limit=500
    → [{type, side, size, usdcSize, price, timestamp, title, slug, ...}, ...]

The leaderboard is "all-time top" so many entries are dormant — we iterate down
the list and keep only users that currently have open positions, since the
whole point of the bot is to narrate what they're betting on right now.
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
from src.utils.models import Position, WalletProfile


LEADERBOARD_BASE = "https://lb-api.polymarket.com"
DATA_API_BASE = "https://data-api.polymarket.com"

# Map our user-facing window names to the values the leaderboard API accepts.
# The endpoint silently accepts a few aliases; we standardize here.
_INTERVAL_ALIASES = {
    "day": "1d",
    "1d": "1d",
    "week": "1w",
    "1w": "1w",
    "month": "1m",
    "1m": "1m",
    "all": "all",
}


class PolymarketClient:
    """Pull top traders + their current positions from Polymarket."""

    def __init__(
        self,
        leaderboard_window: str = "Month",
        leaderboard_metric: str = "profit",
        leaderboard_limit: int = 25,
        positions_per_user: int = 10,
        position_size_threshold: float = 1.0,
        max_wallets_with_positions: int = 5,
        timeout: int = 20,
    ):
        self._interval = _INTERVAL_ALIASES.get(leaderboard_window.lower(), "1m")
        # "profit" or "volume" — we default to profit since the bot narrates winners.
        self._metric = leaderboard_metric.lower()
        self._lb_limit = leaderboard_limit
        self._pos_limit = positions_per_user
        self._size_threshold = position_size_threshold
        # Stop scanning once we've enriched this many wallets that actually have
        # something to narrate. Saves API calls on a long all-time leaderboard
        # where many top users are dormant.
        self._max_with_positions = max_wallets_with_positions
        self._timeout = timeout

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------
    def fetch(self) -> List[WalletProfile]:
        log.info(
            "Fetching Polymarket {} leaderboard (interval={}, limit={})",
            self._metric, self._interval, self._lb_limit,
        )
        try:
            rows = self._fetch_leaderboard()
        except requests.RequestException as exc:
            log.error("Polymarket leaderboard fetch failed: {}", exc)
            return []

        log.info("Polymarket leaderboard returned {} rows", len(rows))

        wallets: List[WalletProfile] = []
        with_positions = 0
        for row in rows:
            try:
                wallet = self._build_wallet(row)
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "Skipping malformed Polymarket row {}: {}",
                    row.get("proxyWallet") or row.get("user"),
                    exc,
                )
                continue
            if wallet is None:
                continue

            self._attach_positions(wallet)
            self._attach_activity_stats(wallet)

            if wallet.positions:
                with_positions += 1
                wallets.append(wallet)
                if with_positions >= self._max_with_positions:
                    log.info(
                        "Reached cap of {} wallets with open positions, stopping scan.",
                        self._max_with_positions,
                    )
                    break
            else:
                log.debug(
                    "Polymarket {} has no open positions — skipping.",
                    wallet.short_address,
                )

        log.info("Built {} Polymarket wallet profiles with open positions", len(wallets))
        return wallets

    # ------------------------------------------------------------------
    # Leaderboard
    # ------------------------------------------------------------------
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=2, max=20),
        retry=retry_if_exception_type(requests.RequestException),
        reraise=True,
    )
    def _fetch_leaderboard(self) -> List[dict]:
        url = f"{LEADERBOARD_BASE}/{self._metric}"
        resp = requests.get(
            url,
            # NB: the param is `interval`, NOT `window` (that 400s).
            params={"interval": self._interval, "limit": self._lb_limit},
            timeout=self._timeout,
            headers={"Accept": "application/json"},
        )
        resp.raise_for_status()
        payload = resp.json()
        if isinstance(payload, dict):
            for key in ("data", "results", "leaderboard"):
                if isinstance(payload.get(key), list):
                    return payload[key]
            return []
        if isinstance(payload, list):
            return payload
        return []

    # ------------------------------------------------------------------
    # Positions per user
    # ------------------------------------------------------------------
    def _attach_positions(self, wallet: WalletProfile) -> None:
        try:
            raw_positions = self._fetch_positions(wallet.address)
        except requests.RequestException as exc:
            log.warning(
                "Polymarket positions failed for {}: {}",
                wallet.short_address, exc,
            )
            return

        positions: List[Position] = []
        for p in raw_positions[: self._pos_limit]:
            try:
                positions.append(_position_from_row(p))
            except Exception as exc:  # noqa: BLE001
                log.debug("Skipping malformed position row: {}", exc)

        wallet.positions = positions
        wallet.raw["polymarket_positions"] = raw_positions

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=2, max=20),
        retry=retry_if_exception_type(requests.RequestException),
        reraise=True,
    )
    def _fetch_positions(self, address: str) -> List[dict]:
        resp = requests.get(
            f"{DATA_API_BASE}/positions",
            params={
                "user": address,
                "sizeThreshold": self._size_threshold,
                "limit": self._pos_limit,
                "sortBy": "CURRENT",
                "sortDirection": "DESC",
            },
            timeout=self._timeout,
            headers={"Accept": "application/json"},
        )
        if resp.status_code == 404:
            return []
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else []

    # ------------------------------------------------------------------
    # Activity-derived stats: trade count + last_active
    # ------------------------------------------------------------------
    def _attach_activity_stats(self, wallet: WalletProfile) -> None:
        """Count recent trades and find last activity timestamp.

        The activity endpoint does NOT expose realized pnl — only the raw
        BUY/SELL events. Computing a true winrate would require pairing buys
        and sells per conditionId or imputing from REDEEM payouts, which is
        noisy. We instead:

          - set `wallet.trades` to the count of TRADE rows we observed
          - compute a *redemption-based* heuristic winrate: REDEEM rows with a
            positive `usdcSize` (the user got paid by the resolver) divided by
            total REDEEM rows. If we have fewer than 3 redemptions, leave
            winrate at 0.0 and let the filter's MIN_WINRATE=0 still pass it.
          - record `last_active` from the newest event timestamp
        """
        try:
            events = self._fetch_activity(wallet.address)
        except requests.RequestException as exc:
            log.debug("Activity fetch failed for {}: {}", wallet.short_address, exc)
            return

        if not events:
            return

        trade_count = 0
        redeem_total = 0
        redeem_paid = 0
        last_active: Optional[datetime] = None

        for ev in events:
            ts = _coerce_dt(ev.get("timestamp") or ev.get("createdAt"))
            if ts and (last_active is None or ts > last_active):
                last_active = ts

            ev_type = (ev.get("type") or "").upper()
            if ev_type == "TRADE":
                trade_count += 1
            elif ev_type == "REDEEM":
                redeem_total += 1
                if _coerce_float(ev.get("usdcSize")) > 0:
                    redeem_paid += 1

        wallet.trades = max(wallet.trades, trade_count)
        if last_active and not wallet.last_active:
            wallet.last_active = last_active

        if redeem_total >= 3:
            wallet.winrate = redeem_paid / redeem_total
            wallet.raw.setdefault("polymarket_meta", {})["winrate_basis"] = (
                f"{redeem_paid}/{redeem_total} redemptions paid"
            )
        elif redeem_total > 0:
            # Tiny sample — record but stay neutral so we don't over-claim.
            wallet.raw.setdefault("polymarket_meta", {})["winrate_basis"] = (
                f"only {redeem_total} redemption(s); winrate left blank"
            )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=2, max=20),
        retry=retry_if_exception_type(requests.RequestException),
        reraise=True,
    )
    def _fetch_activity(self, address: str) -> List[dict]:
        resp = requests.get(
            f"{DATA_API_BASE}/activity",
            params={"user": address, "limit": 500},
            timeout=self._timeout,
            headers={"Accept": "application/json"},
        )
        if resp.status_code == 404:
            return []
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else []

    # ------------------------------------------------------------------
    # Row -> WalletProfile
    # ------------------------------------------------------------------
    def _build_wallet(self, row: dict) -> Optional[WalletProfile]:
        addr = (
            row.get("proxyWallet")
            or row.get("user")
            or row.get("address")
            or ""
        ).strip().lower()
        if not addr.startswith("0x") or len(addr) != 42:
            return None

        amount = _coerce_float(row.get("amount"))
        pnl_usd = amount if self._metric == "profit" else 0.0
        volume_usd = amount if self._metric == "volume" else 0.0

        label = (
            row.get("name")
            or row.get("pseudonym")
            or row.get("displayUsername")
            or None
        )

        return WalletProfile(
            address=addr,
            chain="polygon",
            label=label,
            roi_percent=0.0,
            winrate=0.0,
            trades=0,
            pnl_usd=pnl_usd,
            volume_usd=volume_usd,
            sources=["polymarket"],
            positions=[],
            raw={
                "polymarket_leaderboard": row,
                "polymarket_meta": {"interval": self._interval, "metric": self._metric},
            },
        )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _position_from_row(p: dict) -> Position:
    """Map a Polymarket /positions row to our Position dataclass.

    Field names observed in the wild (subset):
        title, slug, outcome, size, avgPrice, curPrice, cashPnl, redeemable
    """
    return Position(
        market_question=str(p.get("title") or p.get("question") or "Unknown market"),
        outcome=str(p.get("outcome") or p.get("outcomeName") or ""),
        shares=_coerce_float(p.get("size")),
        avg_entry_price=_coerce_float(p.get("avgPrice") or p.get("entryPrice")),
        current_price=_coerce_float(p.get("curPrice") or p.get("currentPrice")),
        pnl_usd=_coerce_float(p.get("cashPnl") or p.get("pnl")),
        market_slug=p.get("slug") or p.get("marketSlug"),
        is_open=_coerce_float(p.get("size")) > 0 and not p.get("redeemed"),
    )


def _coerce_float(value: Any, default: float = 0.0) -> float:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _coerce_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        ts = float(value)
        if ts > 0:
            return datetime.fromtimestamp(ts, tz=timezone.utc)
    except (TypeError, ValueError):
        pass
    text = str(value).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None
