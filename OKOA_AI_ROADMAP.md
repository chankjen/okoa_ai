# OKOA AI — Development Roadmap

**Version:** 1.0 | **Date:** 24 September 2026
**Source Documents:** `OKOA_AI_CONCEPT_NOTE.md`, `OKOA_PRD.md` (v1.0 MVP), `OKOA_TRD.md` (v1.0 MVP)
**Owner:** ZeTA (PM / Lead Engineer)

---

## 0. How to Read This Roadmap

The roadmap is organized into **8 phases**, each broken into small, independently implementable steps. Every step lists:
- **Task(s)** to implement
- **Deliverable** (what "done" looks like)
- **Exit criteria** before moving to the next phase

Phases 0–3 constitute the **walking skeleton MVP** (a user can chat, get a safe reply, and escalate in crisis). Phases 4–6 complete PRD v1.0 scope. Phase 7 covers post-MVP scale per TRD §6. Estimated durations assume a team of 2–4 engineers + 1 ML engineer + clinical advisor; adjust as needed.

### Traceability Map (PRD Epics → Roadmap Phases)

| PRD Epic | Feature | Roadmap Phase |
|---|---|---|
| Epic 1: Onboarding & Anonymity | WhatsApp opt-in, anonymous UUID | Phase 1, Phase 2 |
| Epic 2: Conversational Support | Llama 3 localized agent | Phase 4 |
| Epic 3: Check-ins & Mood Tracking | Daily prompts, quick replies | Phase 5 |
| Epic 4: Safety & Escalation | Risk engine, dashboard, helpline | Phase 3, Phase 5 |
| Epic 5: Resource Matching | Location-based directory | Phase 6 |
| NFRs: Privacy / Latency / Accessibility | Encrypted vault, <5s responses, text-only | Phase 2, 4, 7 |
| Compliance: Kenya DPA 2019 | Data wipe command, audit trails | Phase 6 |

---

## Phase 0 — Foundations & Pre-Work (Weeks 1–2)

> Goal: Legal, clinical, and infrastructural groundwork so engineering never blocks on approvals.

- [ ] **0.1 Meta Business & WhatsApp access** — Register Meta Business account, apply for WhatsApp Business API (Cloud API), obtain test number + webhook verification token. *Note: sandbox testing first; production template approval takes days — start now.*
- [ ] **0.2 Clinical governance** — Recruit counselor partner(s)/NGO (Dr. Ochieng persona). Define escalation SOPs, risk thresholds, and crisis protocol (verify helpline **1199** routing). Get system-prompt guardrails clinically reviewed.
- [ ] **0.3 Legal/compliance baseline** — Data Protection Officer registration under **Kenya Data Protection Act (2019)**, draft privacy notice (Sheng/Swahili/English), DPIA for mental-health data.
- [ ] **0.4 Repo & CI/CD bootstrap** — Monorepo layout (`backend/`, `ml/`, `dashboard/`, `infra/`), lint/test pipelines, Docker Compose local stack (FastAPI + PostgreSQL + Redis).
- [ ] **0.5 Cloud accounts & secrets** — AWS account (g5.xlarge quota request early), RDS Postgres, ElastiCache Redis, S3; secret management (AWS Secrets Manager); TLS 1.3 enforced.

**Exit criteria:** Webhook endpoint receives a real WhatsApp echo message in staging; compliance sign-off obtained; CI green.

---

## Phase 1 — WhatsApp Gateway & Anonymous Identity (Weeks 3–4)

> Goal: Zero-friction, fully anonymous messaging pipeline (Epic 1).

- [ ] **1.1 FastAPI webhook service** — Meta webhook handler: signature verification (`X-Hub-Signature-256`), URL echo challenge, async message ingestion queue.
- [ ] **1.2 Phone ↔ UUID vault** — Separate encrypted table/service mapping phone number → anonymous UUID (AES-256 at rest). **UUID is the only identifier passed downstream** (TRD §5). No PII in logs or LLM context.
- [ ] **1.3 Session store** — Redis-backed session state (last activity, conversation window); PostgreSQL schema for users (UUID only), sessions, messages.
- [ ] **1.4 Outbound messaging** — Send-text integration via Cloud API; rate-limit handling; delivery-status webhooks. Text-only payloads (NFR: low-end phones, 2G/3G).
- [ ] **1.5 Opt-in/opt-out flows** — First-message greeting, consent screen ("Karibu OKOA…"), STOP/FUTA handling stub.

**Deliverable:** A user can message the bot from WhatsApp and receive a canned reply; DB contains no plaintext phone numbers outside the vault.
**Exit criteria:** Load-test 50 concurrent webhooks without loss; vault encryption audited; opt-out verified end-to-end.

---

## Phase 2 — Risk & Sentiment Engine v1 (Weeks 5–7)

> Goal: Safety layer BEFORE any generative AI touches a message (Epic 4 core, TRD §3.2).

- [ ] **2.1 Label taxonomy & data collection** — Multi-class scheme: **Safe / Distressed / Crisis**. Collect + annotate Swahili & Sheng examples of distress idioms (e.g., *"nimechoka sana"* vs *"nataka kuisha"*). Partner with counselors for labeling reliability (target κ ≥ 0.8).
- [ ] **2.2 Baseline classifier** — Fine-tune lightweight transformer (RoBERTa class, e.g., a Swahili `xlm-roberta` variant) in PyTorch. Ship keyword/regex crisis-blocklist as an instant fallback while the model trains.
- [ ] **2.3 Risk pre-screening service** — Async inference microservice; every inbound message scored **before** reaching the LLM path. Threshold: score > 85% ⇒ Crisis route.
- [ ] **2.4 Crisis interception** — On high-risk: pause AI, immediately send static crisis response with 1199 helpline, flag session for human escalation.
- [ ] **2.5 Evaluation harness** — Held-out test set; report precision/recall per class. **Gate: ≥ 85% accuracy on high-risk detection (Concept Note KPI). Recall on Crisis class prioritized over precision — false negatives are unacceptable.**

**Deliverable:** Message → risk score → intercept-or-pass pipeline running in staging.
**Exit criteria:** End-to-end crisis simulation triggers helpline message in < 2 s; model eval report signed off by clinical advisor.

---

## Phase 3 — Counselor Dashboard & Human-in-the-Loop (Weeks 7–9, overlaps Phase 2)

> Goal: The escalation must land somewhere a human sees it within 2 minutes (PRD KPI).

- [ ] **3.1 Escalation queue** — WebSocket push (TRD §3) of flagged sessions to a counselor-facing web app; priority ordering by risk score; session claim/unclaim.
- [ ] **3.2 Dashboard UI (minimal)** — React/Next.js: live alert feed, anonymized conversation view (UUID only), one-click handover mode (bot pauses, counselor types directly), resolve/mute actions.
- [ ] **3.3 Audit trail** — Log every escalation, flag, handover, and resolution with timestamps (TRD §5 accountability requirement). Immutable append-only log.
- [ ] **3.4 Notifications** — Email/SMS/WhatsApp alert to on-duty counselor when a Crisis item enters the queue (belt-and-braces beyond the dashboard).
- [ ] **3.5 SOP dry runs** — Simulated crisis drills with counselor partners; measure time-to-escalation; iterate until **< 2 min** reliably.

**Exit criteria:** Two consecutive drill weeks hitting < 2 min median response; audit export works.

---

## Phase 4 — Conversational AI: Llama 3 Companion (Weeks 8–12)

> Goal: Empathetic, culturally fluent CBT-style dialogue (Epic 2). Build on top of the safety gate — the LLM only ever sees low/medium-risk messages.

- [ ] **4.1 Base prompt & guardrails** — System prompts (English/Swahili/Sheng variants): non-judgmental tone, CBT coping framing, **hard rules: no medication advice, no diagnosis, always defer to humans/crisis lines**. Red-team adversarial prompt suite.
- [ ] **4.2 Inference serving (untrained first)** — Deploy base Llama 3 8B via vLLM/TGI on g5.xlarge behind an internal API. Wire full flow: WhatsApp → risk gate → LLM → reply. **This is the moment the product becomes usable end-to-end.** Measure latency; target < 5 s total (NFR).
- [ ] **4.3 Dataset curation** — Assemble 10,000+ anonymized/adapted mental-health conversations in Swahili + Sheng (per TRD §4). Sources: counselor-supervised synthetic generation, licensed corpora, translated evidence-based CBT scripts. Document provenance & consent.
- [ ] **4.4 QLoRA fine-tuning** — Single-GPU QLoRA pipeline in `ml/`: training scripts, eval prompts, human evaluation rubric (empathy, cultural fluency, safety) run by counselors. A/B against base model before rollout.
- [ ] **4.5 RAG context layer** — pgvector table of vetted CBT coping strategies + resource content; retrieval injected into prompts. Include anonymized short session history only (no PII).
- [ ] **4.6 Language auto-detection** — Detect Sheng/Swahili/English per message; match reply language; keep casual register.

**Exit criteria:** Staged pilot (whitelisted testers) shows < 5 s p95 latency, zero guardrail breaches in red-team suite, counselor satisfaction ≥ 4/5 on sample transcripts.

---

## Phase 5 — Daily Check-ins, Mood Tracking & Retention Loops (Weeks 12–14)

> Goal: Epic 3 — drive the Day-7 retention KPI (65%+).

- [ ] **5.1 Scheduler service** — Celery/APScheduler cron jobs sending personalized daily check-ins ("Vipi leo?"). Respect quiet hours & opt-outs; cap message frequency.
- [ ] **5.2 Quick-reply UX** — Interactive reply buttons/list messages (WhatsApp native) with emoji/text mood options; parse selections into structured mood records.
- [ ] **5.3 Mood & trigger analytics** — Store per-UUID mood time-series; trend detection feeding the risk engine (repeated downward trend ⇒ proactive supportive nudge or counselor flag).
- [ ] **5.4 Weekly micro-surveys** — Craving/stress self-report prompts (PRD §5 clinical KPI); simple before/after reporting for pilots.
- [ ] **5.5 Recovery milestones** — "Days clean", streaks, motivational follow-ups for users in early recovery (Amina persona).

**Exit criteria:** Pilot cohort Day-7 retention measured ≥ 65%; check-in delivery reliability ≥ 99%.

---

## Phase 6 — Resource Matching & Compliance Hardening (Weeks 14–16)

> Goal: Epic 5 + full Kenya DPA compliance — bridge digital support to physical care (the Concept Note's differentiator).

- [ ] **6.1 Verified partner directory** — Seed PostgreSQL with vetted NGOs/rehabs/empowerment programs (name, services, county/geo, contact, subsidy status, verification date). Backfill workflow for adding partners.
- [ ] **6.2 Geo-matching flow** — Coarse location capture via WhatsApp interactive list (county/sub-county — **never GPS-level PII**); return top 2–3 matches with the option to remain anonymous. Track referral events (KPI: 500+ referrals Year 1).
- [ ] **6.3 Self-service data controls** — Implement **"Futa data yangu"** full-wipe command (hard delete across DB/Redis/vector store, tombstone in vault); "Maelezo" data-summary request flow.
- [ ] **6.4 Security review** — Pen test; verify AES-256 at rest, TLS 1.3 in transit, vault isolation, log scrubbing (no message content in app logs by default).
- [ ] **6.5 Documentation & ODPC filing** — Privacy policy published, records of processing maintained, DPIA finalized.

**Exit criteria:** A tester can find a nearby rehab anonymously and wipe all their data via one WhatsApp command; pen-test criticals resolved.

---

## Phase 7 — Pilot Launch → Scale (Weeks 16+, ongoing)

> Goal: Controlled public launch, then TRD §6 scale strategy.

- [ ] **7.1 Closed pilot** — 1–2 partner NGOs, ~100–300 users. Instrument all KPIs: DAU, session length, D7/D30 retention, time-to-escalation, referral conversions, weekly craving/stress deltas.
- [ ] **7.2 Feedback loop** — Weekly transcript review with counselors; retrain risk model monthly on new labeled escalations; dataset expansion flywheel.
- [ ] **7.3 Scaling infrastructure** — vLLM continuous batching / TGI, load balancer for webhook bursts (late-night/weekend peaks per TRD), auto-scaling FastAPI group, read replicas, cost dashboards ($/conversation).
- [ ] **7.4 B2B/B2G packaging** — Counselor-dashboard multi-tenancy and reporting exports for NACADA / Ministry of Health / corporate wellness pilots (Concept Note customers).
- [ ] **7.5 Regional expansion prep** — Additional languages/dialects, alternative African cloud hosting for data sovereignty if required.

**Launch gate (go/no-go):** risk model ≥ 85% high-risk accuracy · escalation < 2 min p95 · response < 5 s p95 · zero PII leakage findings · clinical SOP signed.

---

## Cross-Cutting Workstreams (run through all phases)

| Workstream | Cadence |
|---|---|
| **Clinical advisory review** of prompts, escalations, datasets | Bi-weekly |
| **Safety red-teaming** of LLM outputs | Every model change + monthly |
| **Data protection audits** (DPA 2019) | Per phase exit |
| **Sheng linguistics consulting** (tone/slang drift is fast) | Monthly |
| **Cost tracking** (GPU-hours dominate burn) | Weekly |

## Key Risks & Mitigations

1. **Crisis false negatives** → conservative thresholds, keyword fallback always-on, recall-first tuning (Phase 2.5).
2. **WhatsApp policy rejection on health content** → early Meta engagement, human-handoff messaging templates pre-approved (Phase 0.1).
3. **LLM hallucinating medical advice** → RAG-grounded replies + hard guardrails + red-team gate (Phase 4.1/4.5).
4. **Data costs / GPU quota delays** → request quotas in Week 1; QLoRA single-GPU design; quantized inference.
5. **Community trust & stigma** → anonymity by design, NGO co-branding, counselor-led pilots before open launch.

---

### Suggested Immediate Next Steps (this week)
1. Kick off **0.1** (Meta/WhatsApp application) and **0.5** (GPU quota request) — longest external lead times.
2. Stand up **0.4** repo scaffold so Phases 1–2 can start in parallel.
3. Convene clinical advisor to lock **2.1** label taxonomy and escalation SOPs.
