"""Centralized settings loaded from .env via pydantic-settings.

Importing modules should call `get_settings()` (cached) rather than constructing
`Settings()` directly so the same object is shared across the process.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- LLM ---
    anthropic_api_key: str = Field(default="")
    anthropic_model: str = Field(default="claude-sonnet-4-6")

    # --- Data sources ---
    # Which upstream feeds high-performing wallets into the pipeline.
    #   "dune"       – generic DEX traders from a saved Dune query
    #   "polymarket" – top Polymarket users + their open positions
    data_source: Literal["dune", "polymarket"] = Field(default="polymarket")

    dune_api_key: str = Field(default="")
    dune_wallet_query_id: int = Field(default=0)
    arkham_api_key: str = Field(default="")
    arkham_base_url: str = Field(default="https://api.arkhamintelligence.com")

    # Polymarket leaderboard tuning
    polymarket_window: Literal["Day", "Week", "Month", "All"] = Field(default="Month")
    polymarket_metric: Literal["profit", "volume"] = Field(default="profit")
    polymarket_leaderboard_limit: int = Field(default=25)
    polymarket_positions_per_user: int = Field(default=8)
    # Stop scanning the leaderboard once this many users with open positions
    # have been enriched. The all-time leaderboard contains many dormant users.
    polymarket_max_wallets_with_positions: int = Field(default=10)

    # --- TTS ---
    tts_provider: Literal["edge", "elevenlabs", "openai", "gtts"] = Field(default="edge")
    edge_voice: str = Field(default="en-US-GuyNeural")
    elevenlabs_api_key: str = Field(default="")
    elevenlabs_voice_id: str = Field(default="21m00Tcm4TlvDq8ikWAM")
    elevenlabs_model: str = Field(default="eleven_turbo_v2_5")
    openai_api_key: str = Field(default="")
    openai_tts_voice: str = Field(default="onyx")

    # --- Image gen ---
    image_provider: Literal["openai", "stability", "leonardo", "huggingface", "none"] = Field(default="huggingface")
    stability_api_key: str = Field(default="")
    leonardo_api_key: str = Field(default="")
    huggingface_api_key: str = Field(default="")

    # --- YouTube ---
    youtube_client_secrets_file: Path = Field(default=Path("credentials/client_secret.json"))
    youtube_token_file: Path = Field(default=Path("credentials/token.json"))
    youtube_category_id: str = Field(default="28")
    youtube_privacy_status: Literal["private", "unlisted", "public"] = Field(default="private")
    youtube_made_for_kids: bool = Field(default=False)

    # --- Filtering ---
    min_roi_percent: float = Field(default=50.0)
    min_winrate: float = Field(default=0.6)
    min_trades: int = Field(default=20)
    consistency_window_days: int = Field(default=90)
    max_wallets_per_run: int = Field(default=1)

    # --- Pipeline ---
    # Default fires 4×/day (08:00, 12:00, 16:00, 20:00 server time).
    # Common alternatives: "0 8,14,20 * * *" for 3×/day, "0 14 * * *" for 1×/day.
    run_schedule_cron: str = Field(default="0 8,12,16,20 * * *")
    output_dir: Path = Field(default=Path("./output"))
    dry_run: bool = Field(default=True)
    log_level: str = Field(default="INFO")
    target_video_seconds: int = Field(default=55)

    # --- Deduplication (avoid re-publishing the same wallet/markets) ---
    history_file: Path = Field(default=Path("./output/history.json"))
    # Don't feature the same address again within this many days.
    wallet_lookback_days: int = Field(default=14)
    # Don't feature the same set of open positions again within this many days
    # (catches "different wallet, same hot market" duplication).
    position_lookback_days: int = Field(default=3)

    # --- Subtitles / Captions ---
    # When false, ffmpeg won't burn the SRT into the video. We instead upload
    # the SRT (and translated copies) as YouTube caption tracks, which gives
    # viewers a real "CC" toggle plus the language picker.
    burn_subtitles: bool = Field(default=False)
    # Comma-separated language codes for caption translation. The first one
    # is treated as the source (matches the narration language).
    caption_languages: str = Field(default="en,es,pt,hi,id")

    # --- Compliance ---
    include_disclaimer: bool = Field(default=True)

    @field_validator("target_video_seconds")
    @classmethod
    def _under_60(cls, v: int) -> int:
        if not (10 <= v < 60):
            raise ValueError("target_video_seconds must be in [10, 60)")
        return v

    @field_validator("min_winrate")
    @classmethod
    def _winrate_fraction(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError("min_winrate must be a fraction in [0, 1]")
        return v

    @field_validator("include_disclaimer")
    @classmethod
    def _force_disclaimer(cls, v: bool) -> bool:
        # Hardwired safeguard: refuse to disable in code.
        if not v:
            raise ValueError(
                "include_disclaimer cannot be false. Disclaimers are mandatory "
                "for this pipeline to comply with its own safety policy."
            )
        return v


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    # Force .env to override anything already in the shell environment.
    # Without override=True, a stale empty `ANTHROPIC_API_KEY=""` exported
    # in the user's shell will silently win over the real key in `.env`.
    load_dotenv(override=True)
    return Settings()
