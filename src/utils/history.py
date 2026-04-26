"""Persistent record of which wallets/positions we've already published.

Two reasons we need this:

1. The bot runs 3-4 times per day. Without state, every run would pick the
   same top-of-leaderboard wallet and produce a near-identical video.
2. Even if the leaderboard rotates, the same wallet's open positions don't
   change minute-to-minute — narrating the same Stanley Cup bet twice in
   one day is the kind of repetition YouTube and viewers punish.

The file format is a flat JSON list of entries written atomically. We don't
need a real DB at this scale (a few entries per day for years is fine in
JSON). Each entry captures enough to dedup on either dimension:

    {
      "address": "0x...",
      "label": "kch123",
      "featured_at": "2026-04-24T08:00:00+00:00",
      "run_id":     "2026-04-24T08-00-00",
      "video_id":   "abcDEF12345",            # YouTube ID, may be null on dry runs
      "positions_hash": "sha1 of sorted slugs", # changes when their book changes
      "position_slugs": ["stanley-cup-2026", "..."]
    }

`was_featured_recently` exposes both kinds of dedup so the pipeline can refuse
a wallet if EITHER:
  - the address was featured within `wallet_lookback_days`, OR
  - the same set of position slugs was featured within `position_lookback_days`
    (catches "different wallet, same hot market" duplication).
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, List, Optional

from src.utils.logger import log
from src.utils.models import WalletProfile


@dataclass
class HistoryEntry:
    address: str
    label: Optional[str]
    featured_at: datetime
    run_id: str
    video_id: Optional[str]
    positions_hash: str
    position_slugs: List[str]

    def to_dict(self) -> dict:
        return {
            "address": self.address,
            "label": self.label,
            "featured_at": self.featured_at.isoformat(),
            "run_id": self.run_id,
            "video_id": self.video_id,
            "positions_hash": self.positions_hash,
            "position_slugs": list(self.position_slugs),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "HistoryEntry":
        ts = d.get("featured_at")
        dt = datetime.fromisoformat(ts) if ts else datetime.now(timezone.utc)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return cls(
            address=str(d.get("address", "")).lower(),
            label=d.get("label"),
            featured_at=dt,
            run_id=str(d.get("run_id", "")),
            video_id=d.get("video_id"),
            positions_hash=str(d.get("positions_hash", "")),
            position_slugs=list(d.get("position_slugs") or []),
        )


class HistoryStore:
    """Atomic JSON-backed history file."""

    def __init__(
        self,
        path: Path,
        wallet_lookback_days: int = 14,
        position_lookback_days: int = 3,
    ):
        self._path = Path(path)
        self._wallet_lookback = timedelta(days=wallet_lookback_days)
        self._position_lookback = timedelta(days=position_lookback_days)
        self._entries: List[HistoryEntry] = self._load()

    # ------------------------------------------------------------------
    # IO
    # ------------------------------------------------------------------
    def _load(self) -> List[HistoryEntry]:
        if not self._path.exists():
            return []
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("Could not read history file {}: {} — starting fresh.",
                        self._path, exc)
            return []
        if not isinstance(raw, list):
            return []
        out: List[HistoryEntry] = []
        for item in raw:
            try:
                out.append(HistoryEntry.from_dict(item))
            except Exception as exc:  # noqa: BLE001
                log.debug("Skipping malformed history entry {}: {}", item, exc)
        return out

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic write so a crash mid-write can't truncate the file.
        fd, tmp_path = tempfile.mkstemp(
            prefix=".history-", suffix=".json", dir=str(self._path.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(
                    [e.to_dict() for e in self._entries],
                    fh, indent=2, ensure_ascii=False,
                )
            os.replace(tmp_path, self._path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------
    def was_featured_recently(self, wallet: WalletProfile) -> bool:
        """True if either the address OR this exact set of positions was used recently."""
        now = datetime.now(timezone.utc)
        wallet_cutoff = now - self._wallet_lookback
        position_cutoff = now - self._position_lookback
        addr = wallet.address.lower()
        positions_hash = hash_positions(wallet)

        for e in self._entries:
            if e.address == addr and e.featured_at >= wallet_cutoff:
                log.info(
                    "Skipping {} — featured at {} (within wallet lookback)",
                    wallet.short_address, e.featured_at.isoformat(),
                )
                return True
            if (
                positions_hash
                and e.positions_hash == positions_hash
                and e.featured_at >= position_cutoff
            ):
                log.info(
                    "Skipping {} — same positions set already covered at {}",
                    wallet.short_address, e.featured_at.isoformat(),
                )
                return True
        return False

    def filter_unseen(self, wallets: Iterable[WalletProfile]) -> List[WalletProfile]:
        """Return only the wallets that aren't in our recent-history window."""
        return [w for w in wallets if not self.was_featured_recently(w)]

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------
    def record(
        self,
        wallet: WalletProfile,
        run_id: str,
        video_id: Optional[str] = None,
    ) -> None:
        entry = HistoryEntry(
            address=wallet.address.lower(),
            label=wallet.label,
            featured_at=datetime.now(timezone.utc),
            run_id=run_id,
            video_id=video_id,
            positions_hash=hash_positions(wallet),
            position_slugs=[
                str(p.market_slug) for p in wallet.positions if p.market_slug
            ],
        )
        self._entries.append(entry)
        self._prune()
        self._save()
        log.info(
            "Recorded {} ({}) in history (total entries: {})",
            wallet.short_address, wallet.label or "—", len(self._entries),
        )

    def _prune(self, keep_days: int = 90) -> None:
        """Drop entries older than `keep_days` to keep the file bounded."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=keep_days)
        before = len(self._entries)
        self._entries = [e for e in self._entries if e.featured_at >= cutoff]
        dropped = before - len(self._entries)
        if dropped:
            log.debug("Pruned {} history entries older than {} days", dropped, keep_days)

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------
    @property
    def entries(self) -> List[HistoryEntry]:
        return list(self._entries)


def hash_positions(wallet: WalletProfile) -> str:
    """Stable fingerprint of a wallet's open-positions set.

    Uses sorted market slugs (or, fallback, lowered question text) so the
    hash is order-independent. Empty positions list yields an empty string,
    which `was_featured_recently` treats as "no positions dedup possible."
    """
    keys: list[str] = []
    for p in wallet.positions:
        if p.market_slug:
            keys.append(str(p.market_slug).strip().lower())
        elif p.market_question:
            keys.append(str(p.market_question).strip().lower())
    if not keys:
        return ""
    keys.sort()
    return hashlib.sha1("|".join(keys).encode("utf-8")).hexdigest()
