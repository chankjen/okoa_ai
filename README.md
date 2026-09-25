# OKOA AI 🌱

**Anonymous mental-health support for Kenyan youth — right inside WhatsApp.**

OKOA (Swahili: *to save / to rescue*) is an AI-powered companion that meets young people
where they already are. No app download, no account signup, no name required. Users chat in
English, Swahili, or Sheng; a safety-first AI responds; and trained human counselors are one
tap away whenever risk is detected.

> **Crisis? Call 1199** (Kenya Red Cross toll-free helpline) or text "mtu" to the OKOA line
> at any time to reach a human.

---

## Why OKOA

- **~1 in 4** Kenyan youth report symptoms of anxiety or depression; fewer than **10%** ever
  access professional care.
- Stigma, cost, and scarcity of counselors (~50 psychiatrists for 50M people) keep help out
  of reach.
- WhatsApp is near-universal — but existing chatbots ask for identity, which is exactly what
  a stigmatized user fears most.

**OKOA's answer:** zero-identity, text-only, always-available first-line support with a
human safety net.

## Core Principles

| Principle | How we deliver it |
|---|---|
| **Anonymity by design** | Phone numbers live only in an AES-256-GCM encrypted vault keyed to a random UUID. Only the UUID flows through the system. Blind-index lookup never decrypts. |
| **Safety before conversation** | Every message passes a multilingual risk engine *before* any reply is generated — including pre-consent messages. A crisis never waits on a consent form. |
| **Human-in-the-loop** | High-risk conversations escalate to a counselor dashboard with priority queueing, SLA tracking (<2 min median first response), and one-click bot handover. |
| **Fail closed** | If the LLM, translation, or classifier service is down, OKOA degrades to safe static responses — it never free-styles in a crisis. |
| **Kenya DPA 2019 compliance** | Explicit opt-in (`NDIPO`), instant opt-out + data wipe (`STOP`), tamper-evident audit trail, aggregated-only analytics (k-anonymity floors). |

## Features

- 💬 **Conversational companion** — grounded, empathetic replies from a localized Llama-3
  model behind hard guardrails (no medical advice, no hallucinated resources; RAG-grounded
  against a curated directory).
- 🌍 **Multilingual** — English · Kiswahili · Sheng detection and auto-adaptation per user,
  with localized prompts and crisis lexicons.
- 🚨 **Risk & escalation** — weighted composite scoring (crisis >85 intercepts instantly),
  negation-aware lexicon, transformer classifier contract, WhatsApp/SMS alerts to on-duty
  counselors.
- 🧑‍⚕️ **Counselor dashboard API** — JWT-authenticated queue, claim/resolve/mute, anonymized
  conversation view, direct-reply while in handover mode, WebSocket live updates.
- 🔁 **Proactive check-ins** — consent-gated daily cadence ladder with quiet hours and
  mood-tracking quick replies.
- 📍 **Resource matching** — curated directory of real Kenyan services (helplines, county
  facilities, NGOs), free-first ordering, secure UUID-based referrals with outcome tracking.
- 📊 **Anonymized trends** — offline clustering + topic taxonomy feeding an aggregate-only
  heatmap dashboard and research exports (IRB-friendly, no individual data leaves).
- 🔒 **Audit everything** — SHA-256 hash-chained, append-only audit log with tamper
  verification and CSV export.

## Architecture (high level)

```
WhatsApp Cloud API ⇄ /webhooks/whatsapp (HMAC-SHA256 verified, async ingest)
        │
        ▼
 Identity Vault ── AES-256-GCM phone↔UUID mapping (blind index lookup)
        │
        ▼
 Pipeline:  consent gate → risk engine (block ≥ threshold) → language detect
           → memory (rolling window + summaries) → LLM companion → output guard
        │                        │
        ▼                        ▼
 Escalation Service        Counselor Dashboard API (+ WebSocket push)
        │
   Postgres (SQLAlchemy + Alembic) · Redis (sessions, dedupe, scheduler job store)
```

Full design: [`OKOA_TRD.md`](OKOA_TRD.md) · Product spec: [`OKOA_PRD.md`](OKOA_PRD.md) ·
Concept: [`OKOA_AI_CONCEPT_NOTE.md`](OKOA_AI_CONCEPT_NOTE.md) · Delivery plan:
[`OKOA_AI_ROADMAP.md`](OKOA_AI_ROADMAP.md)

## Repository layout

```
├── OKOA_AI_CONCEPT_NOTE.md   # Vision & problem framing
├── OKOA_PRD.md               # Product requirements (epics, user stories, KPIs)
├── OKOA_TRD.md               # Technical requirements & architecture
├── OKOA_AI_ROADMAP.md        # Phased delivery roadmap (Phase 0–8)
├── docs/runbooks/            # Operational runbooks
└── backend/
    ├── app/
    │   ├── api/              # Webhooks, counselor dashboard, ops endpoints, WebSocket
    │   ├── auth/             # Counselor JWT + bcrypt authentication
    │   ├── core/             # Config (pydantic-settings), structured JSON logging w/ PII scrubbing
    │   ├── db/               # SQLAlchemy models & session management
    │   ├── safety/           # Lexicon, risk engine, escalation, audit chain, evaluation harness
    │   ├── services/         # Pipeline, identity, WhatsApp client, notifications, session store
    │   ├── vault/            # AES-256-GCM crypto + blind-index identity vault
    │   └── main.py           # FastAPI application factory
    ├── alembic/              # DB migrations
    ├── tests/                # Pytest suite (incl. red-team safety scenarios)
    ├── scripts/              # Utility scripts (seed, eval, drills)
    ├── requirements.txt
    └── .env.example          # Environment template — copy to .env, never commit secrets
```

## Quick start

**Prereqs:** Python 3.12+, Docker (for Postgres + Redis), WhatsApp Cloud API credentials
(Meta developer portal).

```bash
# 1. Install dependencies
cd backend
pip install -r requirements.txt

# 2. Configure environment
cp .env.example .env
# Generate the vault master key and paste into VAULT_MASTER_KEY:
python -c "import os,base64;print(base64.b64encode(os.urandom(32)).decode())"
# Fill in WHATSAPP_* values from your Meta app (see docs/runbooks/)

# 3. Start infrastructure (Postgres + Redis)
docker compose up -d db redis

# 4. Apply migrations
alembic upgrade head

# 5. Run the API
uvicorn app.main:app --reload --port 8000
```

Verify it's alive:

```bash
curl localhost:8000/health     # liveness
curl localhost:8000/ready      # DB + Redis dependency checks
curl localhost:8000/metrics    # Prometheus text format
```

Expose the webhook locally for Meta testing: `ngrok http 8000`, then set the callback URL to
`https://<tunnel>/webhooks/whatsapp` with your `WHATSAPP_VERIFY_TOKEN`.

### Running tests

```bash
cd backend
pytest                      # full suite (uses SQLite + fakeredis; no infra needed)
python -m app.safety.evaluate   # risk-engine recall/precision on the eval dataset
```

## Configuration

All settings load from environment variables (see [`backend/.env.example`](backend/.env.example)).
Key groups:

| Group | Variables | Notes |
|---|---|---|
| Storage | `DATABASE_URL`, `REDIS_URL`, `SESSION_TTL_SECONDS` | Redis optional — pipeline degrades to DB-only |
| Vault | `VAULT_MASTER_KEY` | base64-encoded 32-byte AES key; rotation supported |
| WhatsApp | `WHATSAPP_APP_SECRET`, `WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_VERIFY_TOKEN` | Cloud API v20.0 |
| Safety | `ENABLE_RISK_ENGINE`, `CRISIS_THRESHOLD_PCT` (85), `DISTRESS_THRESHOLD_PCT` (45) | score above crisis threshold intercepts instantly; conservative defaults |
| Auth | `JWT_SECRET` | counselor dashboard tokens |
| Ops | `ENABLE_CRISIS_KEYWORD_FALLBACK` | always-on static keyword backstop |

⚠️ **Never commit `.env` or real credentials.** CI includes secret scanning.

## Security & privacy model

- **Transport**: TLS termination at reverse proxy; webhook payloads HMAC-verified over raw
  bytes with constant-time comparison and timestamp tolerance.
- **At rest**: phone numbers encrypted (AES-256-GCM, UUID bound as additional authenticated
  data); `STOP` tombstones the user and hard-deletes ciphertext (DPA right-to-erasure).
- **Logs**: structured JSON with automatic PII scrubbing — no plaintext MSISDNs, ever.
- **Webhook replay protection**: Redis SETNX idempotency on `wa_message_id` (exactly-once
  processing despite Meta retries).
- **Dashboard**: bcrypt-hashed credentials, short-TTL JWTs, handover-mode enforcement before
  counselor replies, all actions written to the chained audit log.
- **Analytics**: aggregate-only exports with k-anonymity floors; no single-user data leaves
  the platform.

## Operations

Runbooks in [`docs/runbooks/`](docs/runbooks): webhook debugging, key rotation, incident
escalation, counselor SOP dry-runs (tracked against the <2-min response KPI via
`ResponseDrill`). Metrics exposed at `/metrics` cover pipeline latency, interception counts,
queue ages, and send failures.

## Roadmap status

Phases 0–7 implemented (gateway, identity vault, risk engine, counselor loop, LLM companion,
retention & resources, multilingual adaptation, analytics/research platform). See
[`OKOA_AI_ROADMAP.md`](OKOA_AI_ROADMAP.md) for phase-by-phase detail and exit criteria.

**In progress / next:** pilot launch hardening — staged rollout, red-team gate sign-off,
production GPU serving for the fine-tuned model, real-device E2E validation with staging
WhatsApp credentials.

## Contributing

1. Branch from `main`; reference the roadmap phase in PR titles (e.g. `[P2] tighten negation window`).
2. All changes require tests; safety-critical code paths need a red-team scenario added to
   `tests/data/`.
3. `pytest` and lint must pass in CI. Never weaken a threshold without clinical advisor sign-off.

## License

See [LICENSE](LICENSE).

---

*Built with ❤️ for Kenya's youth. If you or someone you know is in crisis, call **1199**
(Kenya Red Cross, toll-free, 24/7).*
