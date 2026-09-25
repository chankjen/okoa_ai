"""OKOA safety layer — Phase 2 Risk & Sentiment Engine (roadmap 2.1–2.5).

Every inbound message is scored here BEFORE it can reach any generative path.
Design bias: RECALL FIRST on the Crisis class — false negatives are
unacceptable (Concept Note KPI ≥ 85% high-risk detection).
"""
