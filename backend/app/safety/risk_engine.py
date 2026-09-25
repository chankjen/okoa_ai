"""Risk pre-screening service (roadmap 2.3 / 2.4).

score_message() is a pure function — no I/O, no awaits on the hot path — so
the pipeline can run it inline in < 1 ms today and swap in the async
transformer microservice (Phase 2 stretch) behind the same interface.

Composite score model (0..100):
    crisis_component  = max(weight of non-negated crisis hits)
                        + 8 per extra distinct hit      (cap 100)
                        * 0.35 if ALL hits negated       (negation dampener)
    plan_boost        = +12 per distinct plan/method term (cap +24)
    distress_component= min(60, 15 * number of distinct distress terms)
    history_boost     = +10 if recent messages trend distressed
                        (repeated downward trend ⇒ escalate sensitivity)

    score = clamp(crisis_component + plan_boost + 0.25*distress_component + history_boost)

Routing thresholds come from Settings (crisis > 85 ⇒ Crisis route, roadmap 2.3;
distress > 45 ⇒ supportive track). Crisis ALWAYS intercepts regardless of what
a downstream model might think — recall-first bias.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

from app.db.models import RiskLabel
from app.safety.lexicon import find_crisis_hits, find_distress_hits, find_plan_hits

MODEL_VERSION = "rules-ensemble-v1"


@dataclass
class RiskResult:
    label: RiskLabel
    score: float                       # composite 0..100
    crisis_prob: float                 # normalized subscores for audit/analytics
    distress_prob: float
    features: dict = field(default_factory=dict)
    model_version: str = MODEL_VERSION
    latency_ms: float | None = None

    @property
    def is_crisis(self) -> bool:
        return self.label is RiskLabel.crisis

    def features_json(self) -> str:
        return json.dumps(self.features, ensure_ascii=False)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["label"] = self.label.value
        return d


def _clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def score_message(
    text: str,
    *,
    crisis_threshold: float = 85.0,
    distress_threshold: float = 45.0,
    recent_distressed_count: int = 0,
) -> RiskResult:
    """Score one inbound message. Pure & synchronous."""
    if not text or not text.strip():
        return RiskResult(RiskLabel.safe, 0.0, 0.0, 0.0, {"empty": True})

    crisis_hits = find_crisis_hits(text)
    plan_hits = find_plan_hits(text)
    distress_hits = find_distress_hits(text)

    active = [h for h in crisis_hits if not h["negated"]]
    negated = [h for h in crisis_hits if h["negated"]]

    if active:
        crisis_component = max(h["weight"] for h in active)
        distinct_terms = {h["term"] for h in active}
        crisis_component += 8 * max(0, len(distinct_terms) - 1)
    elif crisis_hits:  # everything was negated — heavy dampen but don't zero out
        crisis_component = max(h["weight"] for h in negated) * 0.35
    else:
        crisis_component = 0.0

    plan_boost = min(24.0, 12.0 * len(plan_hits))
    distress_component = min(60.0, 15.0 * len(set(distress_hits)))
    history_boost = 10.0 if recent_distressed_count >= 2 else 0.0

    score = _clamp(
        crisis_component + plan_boost + 0.25 * distress_component + history_boost
    )

    if score > crisis_threshold:
        label = RiskLabel.crisis
    elif score > distress_threshold:
        label = RiskLabel.distressed
    else:
        label = RiskLabel.safe

    features = {
        "crisis_hits": [
            {"term": h["term"], "weight": h["weight"], "negated": h["negated"]}
            for h in crisis_hits
        ],
        "plan_hits": plan_hits,
        "distress_hits": distress_hits,
        "recent_distressed_count": recent_distressed_count,
        "components": {
            "crisis": round(crisis_component, 2),
            "plan": plan_boost,
            "distress": round(0.25 * distress_component, 2),
            "history": history_boost,
        },
    }

    return RiskResult(
        label=label,
        score=round(score, 2),
        crisis_prob=round(_clamp(crisis_component + plan_boost) / 100.0, 4),
        distress_prob=round(distress_component / 60.0, 4),
        features=features,
    )


async def score_message_async(text: str, **kw) -> RiskResult:
    """Async façade matching the future transformer microservice contract
    (roadmap 2.3 'async inference microservice'). Swap implementation only."""
    return score_message(text, **kw)
