"""Shared dataclasses passed between pipeline stages."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, List, Optional


@dataclass
class Position:
    """A single open or recent position on Polymarket (or any prediction market).

    `outcome` is the side the trader is on (e.g. "Yes" / "No" / "Trump").
    `avg_entry_price` and `current_price` are in USDC per share (0.00 - 1.00).
    `pnl_usd` may be unrealized (open) or realized (closed).
    """

    market_question: str
    outcome: str
    shares: float = 0.0
    avg_entry_price: float = 0.0
    current_price: float = 0.0
    pnl_usd: float = 0.0
    market_slug: Optional[str] = None
    is_open: bool = True


@dataclass
class WalletProfile:
    """A normalized wallet record. Used for both DEX trader and Polymarket data."""

    address: str
    chain: str = "ethereum"
    label: Optional[str] = None
    roi_percent: float = 0.0
    winrate: float = 0.0
    trades: int = 0
    pnl_usd: float = 0.0
    volume_usd: float = 0.0
    first_seen: Optional[datetime] = None
    last_active: Optional[datetime] = None
    sources: List[str] = field(default_factory=list)
    positions: List[Position] = field(default_factory=list)
    raw: dict = field(default_factory=dict)

    @property
    def short_address(self) -> str:
        if not self.address:
            return "unknown"
        return f"{self.address[:6]}…{self.address[-4:]}"

    @property
    def display_name(self) -> str:
        return self.label or self.short_address

    @property
    def is_polymarket(self) -> bool:
        return "polymarket" in self.sources


@dataclass
class Scene:
    index: int
    duration_sec: float
    narration: str
    on_screen_text: str
    image_prompt: str
    image_path: Optional[Path] = None


@dataclass
class Script:
    hook: str
    cta: str
    setup: str = ""
    payoff: str = ""
    scenes: List[Scene] = field(default_factory=list)
    full_narration: str = ""
    disclaimer: str = ""
    total_duration_sec: float = 55.0


@dataclass
class VideoMetadata:
    title: str
    description: str
    tags: List[str]
    category_id: str


@dataclass
class PipelineResult:
    run_dir: Path
    wallet: WalletProfile
    script: Script
    video_path: Path
    metadata: VideoMetadata
    youtube_video_id: Optional[str] = None


def _json_default(o: Any) -> Any:
    if isinstance(o, datetime):
        return o.isoformat()
    if isinstance(o, Path):
        return str(o)
    raise TypeError(f"Object of type {type(o).__name__} is not JSON serializable")


def dump_json(obj: Any, path: Path) -> None:
    """Persist a dataclass (or list of them) as pretty JSON."""
    if hasattr(obj, "__dataclass_fields__"):
        payload = asdict(obj)
    elif isinstance(obj, list) and obj and hasattr(obj[0], "__dataclass_fields__"):
        payload = [asdict(item) for item in obj]
    else:
        payload = obj
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=_json_default), encoding="utf-8")
