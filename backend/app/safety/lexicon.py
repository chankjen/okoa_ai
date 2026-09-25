"""Label taxonomy & crisis lexicon v1 (roadmap 2.1 / 2.2 keyword fallback).

Three-class scheme: Safe / Distressed / Crisis.

Weighted term lists in English, Swahili and Sheng. A counselor-reviewed
edition is required before production threshold changes (roadmap 0.2);
meanwhile this lexicon doubles as the instant crisis-blocklist fallback
that ships ahead of the transformer model (explicitly sanctioned by 2.2).

Scoring notes
-------------
* ``CRISIS_TERMS`` entries carry a base weight; plan/method words
  ("kuna mpango", "pills", "kwa mara ya mwisho") add an intent boost.
* ``NEGATION_CUES`` within a short window *before* a crisis term dampen
  its weight ("sikitaka kuisha", "haendi kujiua", "never ... myself").
* ``DISTRESS_TERMS`` feed the Distressed class (supportive track, no
  interception) so repeated downward trends can later feed analytics
  (Phase 5.3) without ever blocking the user unfairly.
"""
from __future__ import annotations

import re

# --- Crisis tier -------------------------------------------------------------
# (pattern, weight 0..100)
CRISIS_TERMS: list[tuple[str, float]] = [
    # English
    (r"kill(ing)?\s+(myself?|them?selves?)", 95),
    (r"commit\s+suicide", 95),
    (r"\bsuicidal\b", 90),
    (r"want(ed)?\s+to\s+die\b", 85),
    (r"end(ing)?\s+my\s+life", 90),
    (r"take\s+my\s+(own\s+)?life", 90),
    (r"no\s+reason\s+to\s+live", 80),
    (r"wish(i)?\s+(that\s+)?i\s+(were|was)\s+dead", 75),
    (r"(cant|can\s?not|cannot)\s+go\s+on", 60),
    (r"hurt(ing)?\s+myself", 70),
    (r"cut(ting)?\s+myself", 70),
    (r"overdose", 80),
    # Swahili
    (r"na(taka|hitaji)\s+kuisha", 90),        # nataka kuisha
    (r"nataka\s+kuishi?", 85),
    (r"ni?(ta|me|na)?jiua", 90),              # nitajiua / nimejiua
    (r"u?(ku|a)?jishe", 85),
    (r"siwezi\s+kuendelea", 75),
    (r"maisha\s+yamekwisha", 80),
    (r"nimechoka\s+kuishi?", 75),             # contrast: "nimechoka sana" is distress-only
    (r"kifo\s+ni\s+suluhisho", 90),
    (r"kujiua\b", 90),
    (r"jinai\b", 85),
    (r"kunyong'ona", 80),
    # Sheng / slang variants
    (r"nataka\s+kufa\b", 85),
    (r"nitakufa\b", 80),
    (r"nikufa\b", 75),
    (r"kuhamia?\s+binguni", 70),              # "move to heaven" euphemism
    (r"kulemba\s+maisha", 70),
    (r"sina\s+baada", 65),                    # "there's nothing after / no point"
]

# Intent/plan amplifiers — each adds points when present anywhere in message.
PLAN_TERMS: list[str] = [
    # English
    r"\bplan(ned)?\b", r"\bmethod\b", r"\btonight\b", r"\bby\s+(the\s+)?morning\b",
    r"swallow(ing)?\s+(pills?|medicine)", r"\bgoodbye\s+letter\b", r"\balready\s+bought\b",
    # Swahili / Sheng
    r"mpango\s+wangu", r"\bkuna\s+mpango\b", r"dawa\s+(za\s+kupamba|mwingi)",
    r"\bletle\s+mwisho\b", r"\bkwa\s+mara\s+ya\s+mwisho\b", r"\blala\s+na\s+sisite\b",
]

# Negation cues searched in a window BEFORE a crisis term.
NEGATION_CUES: list[str] = [
    r"\bnever\b", r"\bwon'?t\b", r"\bnot\b", r"\bavoid", r"fear\s+of\b",
    r"\bsikitaka\b", r"\bsitaka\b", r"\bhatendi\b", r"\bhawezi\s+kujiua\b",
    r"\bsi\s+kweli\b", r"\bmzaha\b",
]

# --- Distress tier (supportive, never intercepted) ---------------------------
DISTRESS_TERMS: list[str] = [
    # English
    r"\banxious(ly)?\b", r"\bdepressed\b", r"\boverwhelmed\b", r"\bpanic\b",
    r"\bcan'?t\s+sleep\b", r"\bhopeless\b", r"\bworthless\b", r"\bso\s+much\s+pain\b",
    r"\bstress(ed)?\b", r"\blonely\b", r"\bcrying\b", r"\bcraving\b", r"\brelapse\b",
    # Swahili / Sheng
    r"\bnimechoka\b", r"\bnina\s+huzuni\b", r"\bhuzuni\b", r"\bnivumishiwi\b",
    r"\bnaogopa\b", r"\bsinanjali\b", r"\bmaumivu\b", r"\bmsongo\s+mko\b",
    r"\bnaondoka\s+peke\b", r"\bvuma\b", r"\bniko\s+chini\b",
    r"\busingizi\b", r"\bmtizo\b",
]

_COMPILED_CRISIS: list[tuple[re.Pattern[str], float]] = [
    (re.compile(p, re.IGNORECASE), w) for p, w in CRISIS_TERMS
]
_COMPILED_PLAN: list[re.Pattern[str]] = [re.compile(p, re.IGNORECASE) for p in PLAN_TERMS]
_COMPILED_NEG: list[re.Pattern[str]] = [re.compile(p, re.IGNORECASE) for p in NEGATION_CUES]
_COMPILED_DISTRESS: list[re.Pattern[str]] = [re.compile(p, re.IGNORECASE) for p in DISTRESS_TERMS]

# Window (chars before match start) in which negation cues dampen a crisis hit.
NEGATION_WINDOW = 40


def find_crisis_hits(text: str) -> list[dict]:
    """Return weighted crisis-term hits with negation context flags."""
    hits: list[dict] = []
    low = text.lower()
    for rx, weight in _COMPILED_CRISIS:
        for m in rx.finditer(low):
            start = max(0, m.start() - NEGATION_WINDOW)
            window = low[start : m.start()]
            negated = any(nrx.search(window) for nrx in _COMPILED_NEG)
            hits.append(
                {
                    "term": m.group(0),
                    "weight": weight,
                    "negated": negated,
                    "pos": m.start(),
                }
            )
    return hits


def find_plan_hits(text: str) -> list[str]:
    low = text.lower()
    return sorted({m.group(0) for rx in _COMPILED_PLAN for m in rx.finditer(low)})


def find_distress_hits(text: str) -> list[str]:
    low = text.lower()
    return sorted({m.group(0) for rx in _COMPILED_DISTRESS for m in rx.finditer(low)})


# Bare "want/hope to die" forms — used only when no negation cue is present.
_LOOSE_CISIS = [re.compile(p, re.IGNORECASE) for p in [
    r"\bwant\s+to\s+die\b",
    r"\bhope\s+(that\s+)?i\s+die\b",
    r"\bwish\s+i\s+(was|were)\s+dead\b",
]]


def detect_crisis(text: str) -> bool:
    """Backwards-compatible boolean API (used by the Phase 1 keyword fallback).

    Delegates to the weighted engine with a conservative threshold so recall
    stays at or above the old regex blocklist behaviour.
    """
    from app.safety.risk_engine import score_message

    return score_message(text).label.value == "crisis"
