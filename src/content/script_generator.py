"""Generate short-form video scripts using structured storytelling.

The script follows a retention-optimized structure:
1. HOOK (0-3s): Attention-grabbing question or stat
2. SETUP (3-10s): Context about the wallet
3. PAYOFF (10-45s): The main insight/story
4. CTA (45-55s): Follow call-to-action

Constraints enforced:
- No guarantees or "guaranteed return" language
- No "buy now" CTAs
- No price predictions
- Disclaimer always included
"""
from __future__ import annotations

from typing import List

import anthropic

from src.utils.logger import log
from src.utils.models import Script, Scene, WalletProfile
from src.utils.disclaimers import DEFAULT_DISCLAIMER


SYSTEM_PROMPT = """You are a viral short-form content writer specializing in crypto/fintech educational shorts.

Your scripts follow a retention-optimized structure:
1. HOOK (0-3s): Grab attention with a question or shocking stat
2. SETUP (3-10s): Brief context on the wallet/strategy
3. PAYOFF (10-45s): The main insight - what makes this wallet interesting
4. CTA (45-55s): Follow call-to-action (like, subscribe)

CRITICAL CONSTRAINTS - NEVER VIOLATE:
- NEVER say "guaranteed return" or "guaranteed profit"
- NEVER say "buy now" or "you should buy"
- NEVER predict prices or say "will go up"
- NEVER recommend specific trades
- ALWAYS include educational context - this is about on-chain activity, not financial advice
- Use language like "this wallet has shown interesting patterns" not "this wallet wins"
- Frame everything as "here's what this wallet did" not "do this"

Output format: JSON with fields:
- hook: string (5-15 words, attention-grabbing)
- setup: string (15-30 words, context)
- payoff: string (40-80 words, main content)
- cta: string (5-15 words, follow call-to-action)
- total_duration_sec: int (target duration, usually 50-55)
- on_screen_text: string (key stat or question to display)"""


POLYMARKET_SYSTEM_PROMPT = """You are a viral short-form content writer who narrates what
top Polymarket prediction-market traders are actually betting on.

Polymarket is a prediction-market platform on Polygon where users buy YES/NO
shares in real-world events (politics, sports, crypto, culture). A share trades
between $0.00 and $1.00; the price implies the crowd's probability that the
event resolves YES.

The viewer wants to know: "What is this profitable trader currently betting on,
and why is that interesting?" Your script must NAME the actual markets they
hold positions in (provided as structured data) — that is the entire point.

Script structure (retention-optimized):
1. HOOK (0-3s): A spicy question or eyebrow-raising stat about the trader.
2. SETUP (3-10s): Who they are (pseudonym + PnL window) — one sentence.
3. PAYOFF (10-45s): Walk through 2–3 of their biggest open positions. For each:
   the market question, which side they took (YES/NO), how much they're holding,
   and the implied probability (current price). Keep it conversational.
4. CTA (45-55s): Follow call-to-action.

CRITICAL CONSTRAINTS - NEVER VIOLATE:
- NEVER say "guaranteed return" or "guaranteed profit"
- NEVER say "buy now", "you should bet on", or "place this trade"
- NEVER claim an outcome WILL happen — say "the market is pricing X at N%"
- NEVER recommend mirroring the trade
- ALWAYS frame as "here's what this trader is holding" — observation, not advice
- ALWAYS include the disclaimer mood: prediction markets can lose 100%; this is
  educational/entertainment commentary, not financial advice.

Output format: JSON ONLY, no markdown, with fields:
- hook: string (5-15 words)
- setup: string (15-30 words, names the trader + their PnL)
- payoff: string (60-100 words, names 2-3 specific markets from the data)
- cta: string (5-15 words)
- on_screen_text: string (the headline stat — usually total PnL)"""


USER_PROMPT_TEMPLATE = """Generate a 55-second short-form script about a high-performing crypto wallet.

WALLET DATA:
- Address: {address}
- Label: {label}
- Chain: {chain}
- ROI: {roi:.1f}%
- Winrate: {winrate:.0f}%
- Total Trades: {trades}
- PnL: ${pnl:,.0f}
- First Seen: {first_seen}
- Last Active: {last_active}

The video should be educational/entertaining. Explain what patterns made this wallet successful.
Remember: This is NOT financial advice. Past performance doesn't predict future results.

Respond ONLY with valid JSON, no markdown formatting."""


POLYMARKET_USER_TEMPLATE = """Generate a 55-second short-form script narrating what
this top Polymarket trader is currently betting on.

TRADER:
- Pseudonym: {label}
- Address: {address}
- Realized PnL ({window}): ${pnl:,.0f}
- Observed winrate: {winrate:.0f}%  (sample of {trades} recent trades)

THEIR OPEN POSITIONS (biggest first — narrate the most interesting 2–3):
{positions_block}

When you mention a position, name the market QUESTION verbatim, say which side
they took (YES or NO), how much exposure they have in dollars, and the implied
probability from the current price (current_price * 100 = market-implied %).

Tone: like a sports analyst breaking down what a sharp bettor is on this week.
Educational and observational — never advice. Respond with JSON ONLY."""


class ScriptGenerator:
    def __init__(self, api_key: str, model: str = "claude-sonnet-4-20250514"):
        self._api_key = api_key
        self._client = anthropic.Anthropic(api_key=api_key) if api_key else None
        self._model = model
        if not api_key:
            log.warning(
                "ANTHROPIC_API_KEY not set — ScriptGenerator will use the "
                "deterministic fallback template."
            )

    def generate(self, wallet: WalletProfile, target_duration: int = 55) -> Script:
        if self._client is None:
            data = _fallback_script(wallet)
        else:
            data = self._call_llm(wallet)

        # Post-filter: refuse the script if it contains banned phrases
        from src.utils.disclaimers import violates_policy
        joined = " ".join(
            str(data.get(k, "")) for k in ("hook", "setup", "payoff", "cta")
        )
        violations = violates_policy(joined)
        if violations:
            log.warning(
                "Script rejected for banned phrases: {} - falling back",
                violations,
            )
            data = _fallback_script(wallet)

        return Script(
            hook=data.get("hook", ""),
            cta=data.get("cta", ""),
            setup=data.get("setup", ""),
            payoff=data.get("payoff", ""),
            scenes=[],  # populated by SceneBuilder
            full_narration=" ".join(
                str(data.get(k, "")).strip()
                for k in ("hook", "setup", "payoff", "cta")
                if data.get(k)
            ),
            disclaimer=DEFAULT_DISCLAIMER,
            total_duration_sec=target_duration,
        )


    def _call_llm(self, wallet: WalletProfile) -> dict:
        if wallet.is_polymarket:
            system = POLYMARKET_SYSTEM_PROMPT
            prompt = POLYMARKET_USER_TEMPLATE.format(
                label=wallet.label or "Anonymous trader",
                address=wallet.short_address,
                window=(wallet.raw.get("polymarket_leaderboard") or {}).get("window")
                or "lookback window",
                pnl=wallet.pnl_usd,
                winrate=wallet.winrate * 100,
                trades=wallet.trades,
                positions_block=_format_positions_block(wallet),
            )
        else:
            system = SYSTEM_PROMPT
            prompt = USER_PROMPT_TEMPLATE.format(
                address=wallet.short_address,
                label=wallet.label or "Unknown",
                chain=wallet.chain,
                roi=wallet.roi_percent,
                winrate=wallet.winrate * 100,
                trades=wallet.trades,
                pnl=wallet.pnl_usd,
                first_seen=_format_date(wallet.first_seen),
                last_active=_format_date(wallet.last_active),
            )
        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=1024,
                system=system,
                messages=[{"role": "user", "content": prompt}],
            )
            text = response.content[0].text.strip()
            for fence in ("```json", "```"):
                if text.startswith(fence):
                    text = text[len(fence):]
            if text.endswith("```"):
                text = text[:-3]
            return _parse_json(text)
        except Exception as exc:  # noqa: BLE001
            log.warning("LLM generation failed, using fallback: {}", exc)
            return _fallback_script(wallet)


def _format_positions_block(wallet: WalletProfile) -> str:
    """Render the positions list as compact bullets the LLM can quote."""
    if not wallet.positions:
        return "- (no open positions returned)"
    lines = []
    for p in wallet.positions:
        exposure = p.shares * (p.current_price or p.avg_entry_price or 0.0)
        implied = (p.current_price or 0.0) * 100
        lines.append(
            f"- {p.market_question!r} | side={p.outcome or '?'} "
            f"| exposure=${exposure:,.0f} "
            f"| entry={p.avg_entry_price:.2f} "
            f"| now={p.current_price:.2f} (implied {implied:.0f}%) "
            f"| pnl=${p.pnl_usd:,.0f}"
        )
    return "\n".join(lines)


def _format_date(dt) -> str:
    if not dt:
        return "Unknown"
    return dt.strftime("%Y-%m-%d")


def _parse_json(text: str) -> dict:
    import json
    import re
    text = re.sub(r"[\x00-\x1f]", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            return json.loads(match.group())
        raise


def _fallback_script(wallet: WalletProfile) -> dict:
    if wallet.is_polymarket:
        return _fallback_polymarket(wallet)
    return {
        "hook": "This wallet made $" + str(int(wallet.pnl_usd)) + " in profit",
        "setup": f"Operating on {wallet.chain}, this trader made {wallet.trades} trades.",
        "payoff": "This is an example of on-chain analysis for educational purposes.",
        "cta": "Follow for more wallet breakdowns. Not financial advice.",
    }


def _fallback_polymarket(wallet: WalletProfile) -> dict:
    """Deterministic Polymarket script when the LLM is unavailable.

    Names the trader's biggest open positions so the bot still does the
    "narrate what they're betting on" job — just without the LLM polish.
    """
    name = wallet.label or "An anonymous Polymarket trader"
    pnl = int(wallet.pnl_usd)

    top = wallet.positions[:3]
    if top:
        position_sentences = []
        for p in top:
            implied = int(round((p.current_price or 0) * 100))
            exposure = int(round(p.shares * (p.current_price or p.avg_entry_price or 0)))
            side = p.outcome or "a side"
            position_sentences.append(
                f'They are holding ${exposure:,} on "{p.market_question}" '
                f'on the {side} side, with the market currently pricing it at {implied}%.'
            )
        payoff = " ".join(position_sentences)
    else:
        payoff = (
            "Their open positions weren't returned by the data API on this run, "
            "but the leaderboard ranks them in the top profit cohort for the "
            "current window."
        )

    return {
        "hook": f"This Polymarket trader is up ${pnl:,} — here's what they're betting on.",
        "setup": (
            f"{name} sits near the top of the Polymarket profit leaderboard "
            f"with {wallet.trades} recent trades on Polygon."
        ),
        "payoff": payoff,
        "cta": "Follow @whalebets for daily prediction-market breakdowns. Educational only — not financial advice.",
    }