"""Filter wallets based on performance criteria."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import List

from src.utils.logger import log
from src.utils.models import WalletProfile


class WalletFilter:
    @dataclass
    class FilterConfig:
        min_roi_percent: float = 50.0
        min_winrate: float = 0.6
        min_trades: int = 20
        consistency_window_days: int = 90
        max_wallets_per_run: int = 1

    def __init__(self, config: FilterConfig):
        self.config = config

    def apply(self, wallets: List[WalletProfile]) -> List[WalletProfile]:
        filtered = []
        for w in wallets:
            if self._passes(w):
                filtered.append(w)
            else:
                log.debug(
                    "Filtered out {} - ROI: {:.1f}%, WR: {:.0f}%, Trades: {}",
                    w.display_name, w.roi_percent, w.winrate * 100, w.trades
                )

        filtered = self._rank(filtered)
        result = filtered[: self.config.max_wallets_per_run]

        log.info(
            "Filtered: {}/{} wallets passed (showing top {})",
            len(result), len(wallets), len(result)
        )
        return result

    def _passes(self, wallet: WalletProfile) -> bool:
        # Polymarket wallets often have no ROI surfaced by the leaderboard
        # (only raw PnL/volume), so the ROI gate would reject everyone. For
        # that source we instead require non-zero PnL and at least one
        # observable open position to narrate.
        if wallet.is_polymarket:
            if wallet.pnl_usd <= 0 and wallet.volume_usd <= 0:
                return False
            if not wallet.positions:
                return False
        else:
            if wallet.roi_percent < self.config.min_roi_percent:
                return False

        if wallet.winrate < self.config.min_winrate:
            return False
        if wallet.trades < self.config.min_trades:
            return False

        if not self._is_consistent(wallet):
            return False

        return True

    def _is_consistent(self, wallet: WalletProfile) -> bool:
        cutoff = datetime.now(timezone.utc) - timedelta(
            days=self.config.consistency_window_days
        )
        last_active = wallet.last_active or wallet.first_seen
        if last_active:
            if last_active.tzinfo is None:
                last_active = last_active.replace(tzinfo=timezone.utc)
            return last_active >= cutoff
        return True

    def _rank(self, wallets: List[WalletProfile]) -> List[WalletProfile]:
        return sorted(wallets, key=self._score, reverse=True)

    @staticmethod
    def _score(w: WalletProfile) -> float:
        if w.is_polymarket:
            # PnL-led score for Polymarket: dollar wins matter most, with a
            # nudge from winrate and a tie-breaker on number of open positions.
            return (
                w.pnl_usd * 1.0
                + w.winrate * 1000.0
                + len(w.positions) * 50.0
            )
        return (
            w.roi_percent * 0.6
            + w.winrate * 100 * 0.3
            + min(w.trades, 1000) * 0.1
        )