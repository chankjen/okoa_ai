"""Evaluation harness (roadmap 2.5).

Held-out labelled set (JSONL: {"text", "label"}) → precision / recall / F1 per
class + overall accuracy. Gate: ≥ 85% high-risk (Crisis) detection, with
RECALL prioritised over precision — false negatives are unacceptable.

Run standalone:
    python -m app.safety.evaluate [tests/data/eval_dataset.jsonl]
Exit code 1 when the gate fails (usable in CI once Phase 7 hardens).
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

from app.db.models import RiskLabel
from app.safety.risk_engine import score_message

CLASSES = ["safe", "distressed", "crisis"]


def load_dataset(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def evaluate(rows: list[dict], crisis_threshold: float = 85.0,
             distress_threshold: float = 45.0) -> dict:
    tp: dict[str, int] = defaultdict(int)
    fp: dict[str, int] = defaultdict(int)
    fn: dict[str, int] = defaultdict(int)
    confusion: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    misses: list[dict] = []

    for row in rows:
        gold = row["label"]
        pred = score_message(
            row["text"],
            crisis_threshold=crisis_threshold,
            distress_threshold=distress_threshold,
        ).label.value
        confusion[gold][pred] += 1
        if gold == pred:
            tp[gold] += 1
        else:
            fp[pred] += 1
            fn[gold] += 1
            if gold == "crisis":
                misses.append({"text": row["text"], "predicted": pred})

    metrics = {}
    for c in CLASSES:
        p = tp[c] / (tp[c] + fp[c]) if (tp[c] + fp[c]) else 0.0
        r = tp[c] / (tp[c] + fn[c]) if (tp[c] + fn[c]) else 0.0
        f1 = 2 * p * r / (p + r) if (p + r) else 0.0
        metrics[c] = {
            "precision": round(p, 4),
            "recall": round(r, 4),
            "f1": round(f1, 4),
            "support": tp[c] + fn[c],
        }

    total = len(rows)
    correct = sum(tp[c] for c in CLASSES)
    gate_pass = metrics["crisis"]["recall"] >= 0.85
    return {
        "n": total,
        "accuracy": round(correct / total, 4) if total else 0.0,
        "per_class": metrics,
        "confusion": {g: dict(preds) for g, preds in confusion.items()},
        "crisis_false_negatives": misses,
        "gate_high_risk_recall_85": gate_pass,
    }


def main(argv: list[str]) -> int:
    path = Path(argv[1]) if len(argv) > 1 else Path("tests/data/eval_dataset.jsonl")
    report = evaluate(load_dataset(path))
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["gate_high_risk_recall_85"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
