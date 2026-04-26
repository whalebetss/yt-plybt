"""Generate YouTube metadata (title, description, tags) using LLM.

Optimizes for shorts discoverability and includes proper
disclaimers in description.
"""
from __future__ import annotations

from typing import List

import anthropic

from src.utils.logger import log
from src.utils.models import Script, VideoMetadata, WalletProfile
from src.utils.disclaimers import YOUTUBE_DESCRIPTION_DISCLAIMER


SYSTEM_PROMPT = """You are a YouTube Shorts optimization expert.

Your task is to generate viral-ready metadata:
- Titles: Attention-grabbing, under 100 chars, includes numbers/stats
- Descriptions: Educational, includes disclaimer, proper hashtags
- Tags: Relevant keywords for discoverability

Constraints:
- NEVER use "guaranteed" or promise returns
- Always include educational disclaimers
- Use trending format patterns"""


USER_PROMPT_TEMPLATE = """Generate YouTube metadata for a crypto wallet analysis Short.

WALLET INFO:
- Name/Label: {label}
- Address: {address}
- ROI: {roi:.1f}%
- Winrate: {winrate:.0f}%
- Total Trades: {trades}

SCRIPT HOOK: {hook}

Generate:
1. title: Catchy title under 100 chars (include key stat)
2. description: Educational description with disclaimer and hashtags
3. tags: 5-10 relevant tags

Respond as JSON with fields: title, description, tags"""


POLYMARKET_USER_PROMPT_TEMPLATE = """Generate YouTube Shorts metadata for a video that
narrates what a top Polymarket prediction-market trader is currently betting on.

TRADER:
- Pseudonym: {label}
- Realized PnL: ${pnl:,.0f}
- Observed winrate: {winrate:.0f}% over {trades} recent trades

SCRIPT HOOK: {hook}

CURRENT OPEN POSITIONS (top 3 by exposure):
{positions_block}

Tone: prediction-market commentary, not financial advice. Reference Polymarket
explicitly. Use hashtags like #polymarket #predictionmarkets #polygon.

Generate:
1. title: Catchy title under 100 chars. Mention Polymarket and a specific stat.
2. description: 3-5 short paragraphs. Briefly call out the markets being bet on.
   Always include the educational disclaimer.
3. tags: 5-10 relevant tags including polymarket, prediction markets, polygon.

Respond as JSON with fields: title, description, tags"""


class MetadataGenerator:
    def __init__(self, api_key: str, model: str = "claude-sonnet-4-20250514"):
        self._api_key = api_key
        self._model = model

    def generate(self, wallet: WalletProfile, script: Script) -> VideoMetadata:
        if not self._api_key:
            return self._fallback_metadata(wallet, script)

        if wallet.is_polymarket:
            prompt = POLYMARKET_USER_PROMPT_TEMPLATE.format(
                label=wallet.label or "Anonymous trader",
                pnl=wallet.pnl_usd,
                winrate=wallet.winrate * 100,
                trades=wallet.trades,
                hook=script.hook,
                positions_block=_format_positions_block(wallet),
            )
        else:
            prompt = USER_PROMPT_TEMPLATE.format(
                label=wallet.label or "Unknown",
                address=wallet.short_address,
                roi=wallet.roi_percent,
                winrate=wallet.winrate * 100,
                trades=wallet.trades,
                hook=script.hook,
            )

        try:
            client = anthropic.Anthropic(api_key=self._api_key)
            response = client.messages.create(
                model=self._model,
                max_tokens=512,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )
            text = response.content[0].text.strip()
            data = self._parse_json(text)
        except Exception as exc:
            log.warning("Metadata LLM failed: {}, using fallback", exc)
            return self._fallback_metadata(wallet, script)

        return VideoMetadata(
            title=data.get("title", ""),
            description=data.get("description", ""),
            tags=data.get("tags", []),
            category_id="28",
        )

    def _parse_json(self, text: str) -> dict:
        import json
        import re
        text = re.sub(r"[\x00-\x1f]", "", text)
        if text.startswith("```json"):
            text = text[7:]
        if text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # Sometimes the LLM adds trailing prose, or wraps the JSON in
            # extra brackets. Pull out the outermost {...} block and retry.
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if match:
                return json.loads(match.group())
            raise

    def _fallback_metadata(self, wallet: WalletProfile, script: Script) -> VideoMetadata:
        if wallet.is_polymarket:
            return self._fallback_polymarket(wallet, script)

        title = f"{wallet.display_name} made ${wallet.pnl_usd:,.0f} trading crypto"

        description = f"""🚨 This is NOT financial advice!

{script.hook}

This video analyzes on-chain activity for educational purposes only.
Past performance DOES NOT guarantee future results.

{wallet.display_name} ({wallet.short_address}):
- Total PnL: ${wallet.pnl_usd:,.0f}
- Win Rate: {wallet.winrate:.0%}
- Total Trades: {wallet.trades}

{YOUTUBE_DESCRIPTION_DISCLAIMER}

#crypto #trading #web3 #onchain"""

        tags = [
            "crypto",
            "trading",
            "web3",
            "blockchain",
            "defi",
            "nft",
            "ethereum",
            "solana",
            "defi",
            "onchain",
        ]

        return VideoMetadata(
            title=title[:100],
            description=description,
            tags=tags,
            category_id="28",
        )

    def _fallback_polymarket(self, wallet: WalletProfile, script: Script) -> VideoMetadata:
        """Deterministic Polymarket metadata when the LLM is unavailable."""
        name = wallet.label or "Anonymous Polymarket trader"
        pnl = wallet.pnl_usd
        title = f"{name} is up ${pnl:,.0f} on Polymarket — here's their book"

        position_lines = []
        for p in wallet.positions[:3]:
            implied = int(round((p.current_price or 0) * 100))
            position_lines.append(
                f'• "{p.market_question}" — {p.outcome or "?"} side @ {implied}% implied'
            )
        positions_text = "\n".join(position_lines) or "• (positions unavailable)"

        description = f"""🚨 NOT financial advice — educational commentary only.

{script.hook}

Today on the channel we break down what {name} is currently betting on
across Polymarket prediction markets (running on Polygon).

Open positions:
{positions_text}

Realized PnL window: ${pnl:,.0f}
Observed winrate: {wallet.winrate:.0%} over {wallet.trades} recent trades

Prediction-market shares can lose 100% of their value at resolution. Nothing
in this video is a recommendation to enter, mirror, or fade these positions.

{YOUTUBE_DESCRIPTION_DISCLAIMER}

#polymarket #predictionmarkets #polygon #crypto #whalebets"""

        tags = [
            "polymarket",
            "prediction markets",
            "polygon",
            "whalebets",
            "smart money",
            "onchain",
            "trading",
            "web3",
            "betting analytics",
            "crypto",
        ]

        return VideoMetadata(
            title=title[:100],
            description=description,
            tags=tags,
            category_id="28",
        )


def _format_positions_block(wallet: WalletProfile) -> str:
    """Compact bullets the metadata LLM can quote in the description."""
    if not wallet.positions:
        return "- (no open positions returned)"
    lines = []
    for p in wallet.positions[:5]:
        exposure = p.shares * (p.current_price or p.avg_entry_price or 0.0)
        implied = (p.current_price or 0.0) * 100
        lines.append(
            f"- {p.market_question!r} | side={p.outcome or '?'} "
            f"| exposure=${exposure:,.0f} | implied {implied:.0f}%"
        )
    return "\n".join(lines)
