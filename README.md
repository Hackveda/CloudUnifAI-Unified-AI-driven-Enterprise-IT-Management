# CloudUnifAI — Unified AI-driven Enterprise IT Management

An executable, educational **safe-simulation pilot** that centralizes incident signals from enterprise tooling, groups signals by service, explains likely root causes and presents approval-gated *simulated* remediation.

Based on [The Talent Grid project explanation](https://thetalentgrid.co.in/os/interview/studio/?public_explanation=25&token=efb71017a94e2cbd2cf8a4a51adba5ed7c90e2141a3b83bfb795308c1ece0b68).

## Functional scope

- Canonical event ingestion for metric, alert, log, deployment, ticket, change and billing events
- Dedupe based on provided external IDs or content/time hash
- Event-to-incident correlation by service (deliberately simplified for pilot)
- Explainable rule-based diagnosis with heuristic confidence; **not a trained AI model**
- Incident evidence timelines, risk-ranked playbook suggestions and operator resolution
- Dual-operator approval gate and **simulated** playbook outcome; no production mutations
- Persistent SQLite storage, audit trail, FinOps anomaly signal counts and overview metrics
- Mobile-friendly HTML dashboard and OpenAPI docs
- Synthetic end-to-end incident generator, reproducible tests, Docker deployment

### Reference architecture

```text
Prometheus/CloudWatch/ServiceNow/Billing (simulated adapters via events API)
                       |
               Canonical event API
                       |
             SQLite persisted events
                       |
         Service-based correlation engine
                       |
       Deterministic RCA / confidence rules
                       |
       Incident API + dashboard + FinOps
                       |
        Request -> Separate approval gate
                       |
             SIMULATED playbook only
                       |
                Immutable* audit log
```

*Audit records are append-only in the application API; SQLite administrators can modify the database. Use external tamper-evident logging in production.*

This is a pilot slice of the proposed architecture, **not** Kafka ingestion, a CMDB knowledge graph, ML training, enterprise SSO/RBAC, full tenant isolation, actual integrations, or live cloud remediation. For deployment, add those components before connecting production systems.

## Quick start: Docker

```bash
git clone https://github.com/Hackveda/CloudUnifAI-Unified-AI-driven-Enterprise-IT-Management.git
cd CloudUnifAI-Unified-AI-driven-Enterprise-IT-Management
git switch feature/cloudunifai-operational-mvp
cp .env.example .env
# Edit .env and replace API key with strong random secret
docker compose up --build -d
```

Open **http://localhost:8000**, enter the API key set in `.env`, click **Connect**, then **Generate incident scenario**. API explorer: http://localhost:8000/docs.

Generate a secret: `python3 -c "import secrets; print(secrets.token_urlsafe(36))"`.

### Quick start: local Python 3.12

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export CLOUDUNIFAI_API_KEY="$(python -c 'import secrets;print(secrets.token_urlsafe(36))')"
export CLOUDUNIFAI_DB="./cloudunifai.db"
uvicorn app.main:app --reload
```

## API example

```bash
export CLOUDUNIFAI_API_KEY=YOUR_SECRET
curl -X POST http://localhost:8000/api/demo -H "X-API-Key: $CLOUDUNIFAI_API_KEY"
curl http://localhost:8000/api/incidents -H "X-API-Key: $CLOUDUNIFAI_API_KEY"
```

To ingest an event:

```bash
curl -X POST http://localhost:8000/api/events \
 -H "Content-Type: application/json" -H "X-API-Key: $CLOUDUNIFAI_API_KEY" \
 -d '{"source":"prometheus","service":"orders-api","kind":"alert","severity":"high","message":"CPU saturation and latency spike","external_id":"prom-456"}'
```

The application returns incident IDs; choose one in the dashboard to inspect evidence, request a simulated action, and approve it using a **different** operator ID. The operator name is a demonstrator input, **not verified identity**.

## Tests

```bash
python -m pytest -q
```

## Security and production boundaries

Do not expose the API to the public Internet without TLS, network restrictions and an identity-aware proxy. The shared API key is demo authentication, not secure enterprise RBAC. The demo UI holds the key only in page memory, not local storage. Approval actors are unverified strings in this pilot. No cloud permissions are needed; no actions run on AWS, Azure or real ITSM systems. Database path must be writable. Use managed PostgreSQL, observability, encrypted secrets, OAuth/OIDC, tenant isolation, proper incident time windows, webhooks/adapters, idempotent job workers, retries/dead-letter handling, and approved, reversible change execution before production.

Pilot KPIs to measure: MTTD, MTTR, alert compression per incident, precision of diagnosis, simulated approval cycle time, and cloud cost anomaly detection. No ROI or reliability improvements are claimed without observed production baselines.

## Implementation roadmap

1. Add connector adapters with contract tests, secrets vault, Kafka/Redpanda stream bus and schema registry.
2. Add topology/CMDB correlation and configurable time windows; separate unrelated incidents on the same service.
3. Add anomaly detection and supervised RCA models with evaluation, drift monitoring and model registry.
4. Add real SSO/RBAC, approval attestations, isolated execution worker, policy engine, canary checks, rollback and tamper-evident audit.
5. Add production FinOps ingestion, spend forecasts, SLA metrics, OTel traces, dashboards and load/chaos tests.
