"""Thin client for Arkham Intelligence.

Arkham's public API surface is gated and changes regularly. This client wraps
the documented `/intelligence/address/{address}` endpoint shape so the rest of
the pipeline can stay decoupled from upstream details.

If `ARKHAM_API_KEY` is empty, `enrich()` becomes a no-op and the pipeline runs
on Dune-only data with a warning. That fallback is intentional: Arkham access
isn't required for the pipeline to be useful.
"""
from __future__ import annotations

from datetime import datetime
from typing import Iterable, List

import requests
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from src.utils.logger import log
from src.utils.models import WalletProfile


class ArkhamClient:
    def __init__(self, api_key: str, base_url: str, timeout: int = 20):
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._enabled = bool(api_key)
        if not self._enabled:
            log.warning("Arkham API key not set — enrichment will be skipped.")

    def enrich(self, wallets: Iterable[WalletProfile]) -> List[WalletProfile]:
        if not self._enabled:
            return list(wallets)
        out: List[WalletProfile] = []
        for w in wallets:
            try:
                merged = self._merge_one(w)
            except requests.RequestException as exc:
                log.warning("Arkham lookup failed for {}: {}", w.short_address, exc)
                merged = w
            out.append(merged)
        return out

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=2, max=20),
        retry=retry_if_exception_type(requests.RequestException),
        reraise=True,
    )
    def _fetch(self, address: str) -> dict | None:
        url = f"{self._base_url}/intelligence/address/{address}"
        resp = requests.get(
            url,
            headers={"API-Key": self._api_key, "Accept": "application/json"},
            timeout=self._timeout,
        )
        if resp.status_code == 404:
            return None
        if resp.status_code == 401:
            log.error("Arkham 401 — disabling enrichment for the rest of this run.")
            self._enabled = False
            return None
        resp.raise_for_status()
        return resp.json()

    def _merge_one(self, wallet: WalletProfile) -> WalletProfile:
        payload = self._fetch(wallet.address)
        if not payload:
            return wallet

        wallet.raw["arkham"] = payload
        if "arkham" not in wallet.sources:
            wallet.sources.append("arkham")

        # Arkham label takes precedence over a Dune-supplied label only if Dune
        # didn't have one — preserves any manual cleanup someone did upstream.
        entity = (payload.get("arkhamEntity") or {}).get("name")
        if entity and not wallet.label:
            wallet.label = entity

        # Arkham's stats endpoint may surface independent ROI/winrate metrics.
        stats = payload.get("stats") or {}
        if isinstance(stats.get("roiPercent"), (int, float)):
            wallet.raw.setdefault("arkham_metrics", {})["roi_percent"] = float(stats["roiPercent"])
        if isinstance(stats.get("winRate"), (int, float)):
            wallet.raw.setdefault("arkham_metrics", {})["winrate"] = float(stats["winRate"])
        if isinstance(stats.get("tradeCount"), (int, float)):
            wallet.raw.setdefault("arkham_metrics", {})["trades"] = int(stats["tradeCount"])

        first = payload.get("firstSeen")
        if first and not wallet.first_seen:
            try:
                wallet.first_seen = datetime.fromisoformat(str(first).replace("Z", "+00:00"))
            except ValueError:
                pass
        return wallet
