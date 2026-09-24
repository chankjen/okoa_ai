"""Crisis keyword fallback — ships in Phase 1 as belt-and-braces until the
Phase 2 transformer model replaces it (roadmap 2.2 explicitly calls for this).

Design bias: RECALL FIRST. Any hit routes to the static crisis response with
the 1199 helpline and pauses normal flow. Word-boundary matching avoids false
positives like "kuishana" (to ignite each other... ) vs "kuisha".
"""
from __future__ import annotations

import re

# Curated with clinical input pending (roadmap 0.2 sign-off required before
# production thresholds change). English + Swahili + common Sheng forms.
CRISIS_PATTERNS: list[str] = [
    # English
    r"\bkill(ing)?\s+(myself?|them?selves?)\b",
    r"\bcommit\s+suicide\b",
    r"\bsuicidal\b",
    r"\bwant(ed)?\s+to\s+die\b",
    r"\bend(ing)?\s+my\s+life\b",
    r"\btake\s+my\s+(own\s+)?life\b",
    r"\bno\s+reason\s+to\s+live\b",
    # Swahili / Sheng — "nataka kuisha", "nitajiua", "siwezi kuendelea"...
    r"\bna(taka|hitaji)\s+kuisha\b",
    r"\bnataka\s+kuishi?\b",
    r"\bni?(ta|me|na)?jiua\b",
    r"\bu?(ku|a)?jishe\b",
    r"\bsiwezi\s+kuendelea\b",
    r"\bmaisha\s+yamekwisha\b",
    r"\bnimechoka\s+kuishi?\b",
    r"\bkifo\s+ni\s+suluhisho\b",
    # Sheng orthographic variants
    r"\bnataka\s+kufa\b",
    r"\bnitakufa\b",
    r"\bnikufa\b",
]

_COMPILED = [re.compile(p, re.IGNORECASE) for p in CRISIS_PATTERNS]

CRISIS_RESPONSE_TEXT = (
    "Nimesikia maumivu katika maneno yako, na jambo hili ni muhimu sana kwetu. 🤍\n\n"
    "Tafadhali ongea na mtu wa sasa hivi:\n"
    "• Kenya Suicide Prevention Hotline: **1199** (bure, saa 24)\n"
    "• Emergency: **999** au **112**\n"
    "• Befwenu / nasiketembea nawe — you are not alone.\n\n"
    "Msaada wa binadamu unapatikana sasa hivi. Unaweza kupiga 1199 sasa?\n"
    "(I've paused our chat so a trained counselor can see this urgently.)"
)


def detect_crisis(text: str) -> bool:
    if not text:
        return False
    normalized = text.lower()
    return any(rx.search(normalized) for rx in _COMPILED)
