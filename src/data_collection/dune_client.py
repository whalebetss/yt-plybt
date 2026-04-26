"""Fetch wallet leaderboard rows from a Dune Analytics query.

The query (DUNE_WALLET_QUERY_ID) is expected to expose at least these columns:
    wallet_address, chain, label, roi_percent, winrate, trades, pnl_usd,
    first_seen, last_active

Anything missing is treated as an empty value. Numeric coercion is best-effort:
malformed rows are skipped with a warning rather than crashing the run.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, List

from dune_client.client import DuneClient
from dune_client.query import QueryBase
from tenacity import retry, stop_after_attempt, wait_exponential

from src.utils.logger import log
from src.utils.models import WalletProfile


class DuneWalletClient:
    def __init__(self, api_key: str, query_id: int):
        if not api_key:
            raise ValueError("DUNE_API_KEY is required.")
        if not query_id:
            raise ValueError("DUNE_WALLET_QUERY_ID is required.")
        self._client = DuneClient(api_key=api_key)
        self._query = QueryBase(query_id=query_id)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=2, max=30),
        reraise=True,
    )
    def fetch(self) -> List[WalletProfile]:
        log.info("Fetching latest results for Dune query {}", self._query.query_id)
        result = self._client.get_latest_result(self._query)
        rows = (result.result.rows if result and result.result else []) or []
        log.info("Dune returned {} rows", len(rows))

        wallets: List[WalletProfile] = []
        for row in rows:
            try:
                wallets.append(_row_to_wallet(row))
            except Exception as exc:  # noqa: BLE001
                log.warning("Skipping malformed Dune row: {} ({})", row, exc)
        return wallets


def _coerce_float(value: Any, default: float = 0.0) -> float:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _coerce_int(value: Any, default: int = 0) -> int:
    if value is None or value == "":
        return default
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _coerce_dt(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _row_to_wallet(row: dict[str, Any]) -> WalletProfile:
    addr = (row.get("wallet_address") or row.get("address") or "").strip().lower()
    if not addr.startswith("0x") or len(addr) < 10:
        raise ValueError(f"invalid address: {addr!r}")

    # Match column names from the user's saved Dune query.
    pnl = _coerce_float(row.get("pnl_usd"))
    volume = _coerce_float(row.get("total_volume_usd"))
    trades = _coerce_int(row.get("num_trades") or row.get("trades"))

    roi = _coerce_float(row.get("roi_percent"))
    if roi == 0 and volume > 0:
        roi = (pnl / volume) * 100

    winrate = _resolve_winrate(row, trades)

    return WalletProfile(
        address=addr,
        chain=(row.get("blockchain") or row.get("chain") or "ethereum").lower(),
        label=row.get("label") or None,
        roi_percent=roi,
        winrate=winrate,
        trades=trades,
        pnl_usd=pnl,
        first_seen=_coerce_dt(row.get("first_seen") or row.get("first_trade")),
        last_active=_coerce_dt(row.get("last_active") or row.get("last_trade")),
        sources=["dune"],
        raw={"dune": row},
    )


def _resolve_winrate(row: dict[str, Any], trades: int) -> float:
    """Best-effort winrate derivation from whatever columns the Dune query exposes.

    Tries (in order):
        - explicit `winrate` / `win_rate` column (already a fraction 0..1, or %)
        - `winning_trades` + `num_trades` / `total_trades`
        - if only `trades > 0` is known, fall back to a neutral 0.5 marker
    Returns 0.0 when there's no signal at all.
    """
    explicit = row.get("winrate") if "winrate" in row else row.get("win_rate")
    if explicit is not None and explicit != "":
        val = _coerce_float(explicit)
        return val / 100.0 if val > 1.0 else val

    wins = _coerce_int(
        row.get("winning_trades") or row.get("wins") or row.get("num_wins")
    )
    total = _coerce_int(row.get("num_trades") or row.get("total_trades")) or trades
    if wins and total:
        return wins / total

    return 0.5 if trades > 0 else 0.0
