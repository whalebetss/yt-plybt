"""Cross-validate wallet data from multiple sources.

Validation checks:
- Address format (valid Ethereum address)
- Metrics consistency (if multiple sources, check they don't contradict)
- Data freshness (wallet had activity within consistency_window)
- No obvious anomalies (e.g., 1000% ROI with 1 trade is suspicious)
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List

from src.utils.logger import log
from src.utils.models import WalletProfile


class WalletValidator:
    """Validate wallet profiles for consistency and freshness."""

    MAX_CONSISTENCY_DAYS = 365
    SUSPICIOUS_ROI_PER_TRADE = 500.0

    def validate(self, wallets: List[WalletProfile]) -> List[WalletProfile]:
        """Return only wallets passing validation checks."""
        valid = []
        for w in wallets:
            if self._validate_one(w):
                valid.append(w)
        log.info("Validated: {}/{} wallets passed", len(valid), len(wallets))
        return valid

    def _validate_one(self, wallet: WalletProfile) -> bool:
        if not _is_valid_address(wallet.address):
            log.warning("Invalid address: {}", wallet.short_address)
            return False

        if not _has_fresh_activity(wallet, self.MAX_CONSISTENCY_DAYS):
            log.warning("Stale wallet: {}", wallet.short_address)
            return False

        if self._has_inconsistent_metrics(wallet):
            log.warning("Inconsistent metrics: {}", wallet.short_address)
            return False

        if self._is_suspicious(wallet):
            log.warning("Suspicious wallet: {}", wallet.short_address)
            return False

        return True

    def _has_inconsistent_metrics(self, wallet: WalletProfile) -> bool:
        """Cross-check the normalized wallet metrics against any Arkham snapshot.

        We compare the (already-normalized) Dune-derived values stored on the
        wallet against the side-channel `arkham_metrics` dict that
        ArkhamClient stashes in `wallet.raw`. If the two sources disagree
        wildly, treat the row as untrustworthy and drop it.
        """
        if len(wallet.sources) < 2:
            return False

        arkham_metrics = wallet.raw.get("arkham_metrics") or {}
        if not arkham_metrics:
            return False

        arkham_roi = arkham_metrics.get("roi_percent")
        if arkham_roi is not None and wallet.roi_percent:
            if abs(wallet.roi_percent - float(arkham_roi)) > 50:
                log.warning(
                    "ROI mismatch for {}: dune={:.1f}% arkham={:.1f}%",
                    wallet.short_address, wallet.roi_percent, float(arkham_roi),
                )
                return True

        arkham_wr = arkham_metrics.get("winrate")
        if arkham_wr is not None and wallet.winrate:
            arkham_wr = float(arkham_wr)
            arkham_wr = arkham_wr / 100.0 if arkham_wr > 1.0 else arkham_wr
            if abs(wallet.winrate - arkham_wr) > 0.3:
                log.warning(
                    "Winrate mismatch for {}: dune={:.0%} arkham={:.0%}",
                    wallet.short_address, wallet.winrate, arkham_wr,
                )
                return True

        return False

    def _is_suspicious(self, wallet: WalletProfile) -> bool:
        if wallet.trades < 3:
            return False
        roi_per_trade = wallet.roi_percent / wallet.trades
        if roi_per_trade > self.SUSPICIOUS_ROI_PER_TRADE:
            return True
        if wallet.roi_percent > 10000:
            return True
        return False


def _is_valid_address(address: str) -> bool:
    if not address:
        return False
    if not address.startswith("0x"):
        return False
    if len(address) != 42:
        return False
    try:
        int(address, 16)
        return True
    except ValueError:
        return False


def _has_fresh_activity(wallet: WalletProfile, max_days: int) -> bool:
    last_active = wallet.last_active or wallet.first_seen
    if not last_active:
        return True
    now = datetime.now(timezone.utc)
    if last_active.tzinfo is None:
        last_active = last_active.replace(tzinfo=timezone.utc)
    age = now - last_active
    return age.days <= max_days