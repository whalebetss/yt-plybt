"""Mandatory disclaimer text + the policy filter the script generator uses."""
from __future__ import annotations

import re
from typing import List

ON_SCREEN_DISCLAIMER = (
    "Not financial advice. Educational content only. "
    "Past on-chain performance does not predict future results."
)

DESCRIPTION_DISCLAIMER = (
    "\n\n---\n"
    "DISCLAIMER: This video is for educational and informational purposes only "
    "and is NOT financial, investment, trading, or legal advice. Wallet activity "
    "shown here is publicly observable on-chain data. Past performance of any "
    "wallet is not indicative of future returns. Cryptocurrency trading carries "
    "substantial risk of loss. Do your own research and consult a licensed "
    "financial advisor before making any investment decision.\n"
)

DEFAULT_DISCLAIMER = "This is educational content only. Not financial advice."

YOUTUBE_DESCRIPTION_DISCLAIMER = (
    "\n\n---\n"
    "DISCLAIMER: This video is for educational and informational purposes only "
    "and is NOT financial advice. Past on-chain performance does not guarantee "
    "future results. Always do your own research.\n"
)

# ----------------------------------------------------------------------
# Policy filter
# ----------------------------------------------------------------------
# Two tiers:
#   ABSOLUTE_BAN — these phrases are unsafe regardless of context. Even
#       wrapped in negation they'd read awkwardly enough that the script
#       should be regenerated from scratch.
#   NEGATABLE   — these phrases are FINE inside disclaimers (e.g. "not
#       financial advice", "does not guarantee future results") but unsafe
#       when stated as a positive promise. We allow them when a negator
#       appears within the few words immediately before the phrase.

ABSOLUTE_BAN: tuple[str, ...] = (
    "buy now",
    "buy this coin",
    "you'll get rich",
    "you will get rich",
    "you should buy",
    "you should invest",
    "i recommend you buy",
    "easy money",
    "100% return",
    "risk-free",
    "risk free",
    "can't lose",
    "cannot lose",
)

# All "guaranteed X" variants are caught by the bare "guaranteed" entry
# below (substring match) and become negatable — so disclaimer phrasings
# like "without guaranteed returns" or "no guaranteed profit" pass.
NEGATABLE: tuple[str, ...] = (
    "financial advice",
    "investment advice",
    "trading advice",
    "guaranteed",
    "guarantee future",
    "predict future",
    "predict the future",
    "will go up",
    "will moon",
)

# Words / contractions that "license" a NEGATABLE phrase when one of them
# appears within the lookback window before the phrase. The window is
# measured in word-tokens, not characters.
_NEGATORS = (
    "not", "no", "never", "neither", "nor", "without", "non",
    "isn't", "aren't", "wasn't", "weren't",
    "won't", "wouldn't", "shouldn't", "couldn't",
    "doesn't", "don't", "didn't",
    # Common phrasings the LLM tends to produce around disclaimers:
    "nothing", "nobody", "nowhere",
)
_NEGATOR_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(n) for n in _NEGATORS) + r")\b",
    re.IGNORECASE,
)
_LOOKBACK_TOKENS = 5  # how many words back we scan for a negator


def violates_policy(text: str) -> List[str]:
    """Return the list of unique banned phrases present in `text`.

    A NEGATABLE phrase only counts as a violation when no negator appears
    within the previous ``_LOOKBACK_TOKENS`` word-tokens — that way real
    disclaimer language ("not financial advice", "does not guarantee
    future results") is allowed through.
    """
    if not text:
        return []
    haystack = text.lower()
    hits: list[str] = []

    for phrase in ABSOLUTE_BAN:
        if phrase in haystack and phrase not in hits:
            hits.append(phrase)

    for phrase in NEGATABLE:
        for m in re.finditer(re.escape(phrase), haystack):
            if not _negated_before(haystack, m.start()):
                if phrase not in hits:
                    hits.append(phrase)
                break  # one positive use is enough; don't double-count

    return hits


def _negated_before(haystack: str, idx: int) -> bool:
    """Is there a negator in the few words immediately before ``idx``?"""
    # Pull the slice before the match; cap at 80 chars to bound the work.
    preceding = haystack[max(0, idx - 80):idx]
    tokens = re.findall(r"[\w']+", preceding)
    if not tokens:
        return False
    window = " ".join(tokens[-_LOOKBACK_TOKENS:])
    return bool(_NEGATOR_RE.search(window))
