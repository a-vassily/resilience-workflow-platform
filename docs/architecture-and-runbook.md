# Resilience Platform Architecture and Runbook

This document describes the current repository architecture, the end-to-end data flow, and the exact steps to run the prototype locally.

## 1. What this project is

This repository is a local prototype for a resilience workflow platform that:

- ingests operational, security, and context signals from multiple sources,
- stores raw evidence separately from normalized workflow state,
- normalizes source-specific payloads into a canonical event model,
- correlates related events into candidate incidents,
- enriches incidents with bounded LLM-generated analysis,
- supports human review and status transitions through a lightweight UI.

The design keeps authoritative state in PostgreSQL. AI output is advisory, stored separately, and does not make final decisions.

## 2. Architecture overview

The system is split into application services, shared support code, and local infrastructure.

### Application services

#### `app/ingest_api/main.py`

FastAPI service responsible for collecting source events.

Main responsibilities:

- receive uploaded JSON files and direct event payloads,
- infer a source type for uploaded files,
- persist the raw payload to MinIO,
- create a `raw_events` row in PostgreSQL,
- expose adapter-style endpoints for Jira and ServiceNow shaped payloads.

Main endpoints:

- `GET /health`
- `POST /ingest/event`
- `POST /ingest/file`
- `POST /adapters/jira/webhook`
- `POST /adapters/servicenow/event`

#### `app/normalizer_worker/main.py`

One-shot worker that transforms raw events into canonical events.

Main responsibilities:

- read unprocessed rows from `raw_events`,
- map source-specific payloads into the canonical event shape,
- insert rows into `canonical_events`,
- upsert service context when the source type is `risk_context`,
- mark raw rows as normalized.

Primary entry points:

- module function: `app.normalizer_worker.main.run_once`
- script: `scripts/run_normalizer.py`

#### `app/correlator_worker/main.py`

One-shot worker that groups canonical events and applies deterministic rules.

Main responsibilities:

- read canonical events with `correlation_status = 'new'`,
- group events by linked service or vendor reference,
- load rules from `rules/correlation_rules.yaml`,
- create `candidate_incidents` when a rule matches,
- create `incident_event_links`,
- mark processed canonical events as correlated.

Primary entry points:

- module function: `app.correlator_worker.main.run_once`
- script: `scripts/run_correlator.py`

#### `app/intelligence_service/main.py`

FastAPI service that performs AI enrichment for an incident.

Main responsibilities:

- load incident state from PostgreSQL,
- assemble a prompt package using incident data and local reference material,
- call LM Studio through an OpenAI-compatible endpoint,
- validate the response shape,
- store prompt/request metadata and response metadata,
- return the enrichment result.

Main endpoints:

- `GET /health`
- `POST /incidents/{incident_id}/enrich`

Important behavior:

- if the model call fails or the response is not valid JSON, the service returns a deterministic fallback payload so the demo flow can continue,
- the current service implementation sends a Chat Completions-style payload.

#### `app/control_api/main.py`

FastAPI service used for review workflows and UI orchestration.

Main responsibilities:

- list incidents,
- return incident detail,
- record review actions,
- update incident status,
- proxy enrichment requests to the intelligence service,
- serve the browser UI.

Main endpoints:

- `GET /health`
- `GET /incidents`
- `GET /incidents/{incident_id}`
- `POST /incidents/{incident_id}/review`
- `POST /incidents/{incident_id}/status`
- `POST /incidents/{incident_id}/enrich`
- `GET /ui`

The UI is a static page at `app/control_api/static/index.html`.

### Shared application layer

Shared code lives under `app/common/`.

- `config.py`: environment-driven settings
- `db.py`: SQLAlchemy engine and session factory
- `repository.py`: SQL helpers used by the APIs and workers
- `llm.py`: helper functions for LM Studio request and response handling
- `minio_client.py`: MinIO client factory
- `schemas.py`: Pydantic models for request payloads

### Local infrastructure

`docker-compose.yml` currently starts only:

- PostgreSQL
- MinIO

LM Studio is expected to run locally outside Docker.

## 3. End-to-end data flow

The main processing flow is:

1. A source event is submitted to the ingest API.
2. The raw payload is written to MinIO in the `raw-events` bucket.
3. Metadata plus the evidence pointer are inserted into `raw_events`.
4. The normalizer reads rows where `ingest_status = 'received'`.
5. Each raw payload is transformed into a canonical event and stored in `canonical_events`.
6. If the payload is a risk-context update, service context is also upserted.
7. The correlator loads canonical events where `correlation_status = 'new'`.
8. Events are grouped and checked against the YAML correlation rules.
9. Matching groups create rows in `candidate_incidents` and `incident_event_links`.
10. The control API exposes the incident for review.
11. The intelligence service can enrich the incident and persist AI request and response metadata.
12. Human review actions and status transitions are recorded in `review_actions`.

## 4. Storage model

### PostgreSQL

PostgreSQL is the system of record for operational workflow state.

Key tables defined in `db/init.sql`:

- `raw_events`: original source payload metadata and ingest state
- `canonical_events`: normalized event records
- `service_context`: service ownership, dependency, and criticality context
- `risk_context_refs`: risk-context reference payloads
- `candidate_incidents`: correlated incident candidates
- `incident_event_links`: links between incidents and contributing events
- `incident_artifacts`: references to stored incident artifacts
- `ai_enrichment_requests`: prompt and request metadata
- `ai_enrichment_responses`: model outputs, validation state, and latency
- `review_actions`: human review and status-change activity
- `remediation_actions`: remediation follow-up records
- `audit_log`: generic audit trail table

The schema also defines `updated_at` triggers for key tables and seeds an example `service_context` row for `portfolio-api`.

### MinIO

MinIO stores larger payloads and externalized evidence.

Expected buckets:

- `raw-events`
- `artifacts`
- `prompts`
- `reports`

In the current codebase, the ingest API actively writes raw uploaded payloads to `raw-events`.

### Reference data

The intelligence service reads local reference files from `reference/`:

- `runbooks.json`
- `prior_incidents.json`
- `risk_context.json`
- `services.json`

These act as lightweight retrieval context for prompt construction.

## 5. Runtime characteristics

This prototype intentionally favors clarity over production complexity.

Current characteristics:

- synchronous request handling,
- one-shot workers rather than long-running schedulers,
- SQL written through repository helper functions,
- local JSON files used as retrieval context,
- a lightweight static review UI,
- deterministic fallback output when the LLM route is unavailable.

## 6. Prerequisites

Prepare the following before running the demo:

- Python 3.11+
- Docker Desktop
- LM Studio with the local server enabled
- optional: PyCharm, if you want to use the committed run configurations

## 7. Environment and configuration

The application loads settings from `.env` via `app/common/config.py`.

Important settings:

- `DATABASE_URL`
- `MINIO_ENDPOINT`
- `MINIO_ROOT_USER`
- `MINIO_ROOT_PASSWORD`
- `MINIO_SECURE`
- `LLM_BASE_URL`
- `LLM_CHAT_PATH`
- `LLM_MODEL`
- `INTELLIGENCE_SERVICE_URL`
- `INGEST_API_URL`
- `CONTROL_API_URL`

### Important note about LM Studio configuration

The repository currently contains two LM Studio patterns:

- `.env` defaults `LLM_CHAT_PATH` to `/v1/responses`,
- the committed PyCharm run configurations override `LLM_CHAT_PATH` to `/v1/chat/completions`.

That matters because `app/intelligence_service/main.py` currently sends a Chat Completions-style payload. For the smoothest local run, either:

- start the APIs using the provided PyCharm run configurations, or
- override `LLM_CHAT_PATH=/v1/chat/completions` when starting the intelligence service manually.

## 8. How to run the project

### Option A: Recommended bootstrap flow

From the repository root in PowerShell:

```powershell
.\scripts\bootstrap.ps1
```

If you want a clean infrastructure and database rebuild:

```powershell
.\scripts\bootstrap.ps1 -RebuildDb
```

What `scripts/bootstrap.ps1` does:

- checks Docker availability,
- starts PostgreSQL and MinIO with Docker Compose,
- installs Python dependencies using `python.exe`,
- applies `db/init.sql` to PostgreSQL,
- creates MinIO buckets,
- runs environment checks.

### Option B: Manual infrastructure start

If you do not want to use the bootstrap script:

```powershell
docker compose up -d
```

Then apply the schema if needed and create the MinIO buckets using:

```powershell
python scripts/setup_minio.py
```

### Resetting the PostgreSQL volume

If the Docker volume backing PostgreSQL gets into a bad state, recreate it before re-running the bootstrap flow:

```powershell
docker compose down -v
docker compose up -d
```

Follow that with either the bootstrap script or the manual schema + MinIO setup described above so the database and buckets are reinitialized.

### Verify infrastructure health

After Docker comes up, confirm that PostgreSQL and MinIO are healthy before continuing:

```powershell
docker compose ps
```

You should see both services in the `Up` state with the expected ports (`5432`, `9000`, `9001`) published. To double-check the schema, run:

```powershell
docker exec -it resilience_postgres psql -U resilience -d resilience -c "\dt"
```

All tables listed in the storage model section should appear; if they do not, rerun the bootstrap script with `-RebuildDb`.

## 9. LM Studio setup

Before using the intelligence service, ensure:

- **LM Studio is open** - the application requires a running local LM Studio instance,
- **local server is enabled** - the `/v1/chat/completions` endpoint must be accessible,
- **the model is loaded** - select the model in LM Studio before testing,
- **model name matches configuration** - the `LLM_MODEL` environment variable must exactly match the model identifier exposed by LM Studio,
- **route matches the implementation** - verify `LLM_CHAT_PATH` is set correctly (see below).

For the current PyCharm run configurations, the effective path is:

- `LLM_CHAT_PATH=/v1/chat/completions`

If you are relying only on `.env`, review the note in the previous section before starting the intelligence service.

If LM Studio exposes a slightly different model identifier than expected, update the `.env` value accordingly.

## 10. Start the application processes

### PyCharm path

The repository currently includes committed run configurations for:

- `Ingest API`
- `Control API`
- `Intelligence Service`

These are stored under `.run/`.

Recommended start order:

1. `Ingest API`
2. `Control API`
3. `Intelligence Service`

For the workers, run the scripts manually from PyCharm or a terminal:

- `python scripts/run_normalizer.py`
- `python scripts/run_correlator.py`

### Terminal path

Open separate terminals.

Start the ingest API:

```powershell
python -m uvicorn app.ingest_api.main:app --reload --port 8000
```

Start the intelligence service. To match the current implementation, explicitly override the path:

```powershell
$env:LLM_CHAT_PATH='/v1/chat/completions'; python -m uvicorn app.intelligence_service.main:app --reload --port 8001
```

Start the control API:

```powershell
python -m uvicorn app.control_api.main:app --reload --port 8002
```

### UI automation helpers

The control API UI exposes helper buttons (`Reset & seed`, `Reset, seed & enrich`, `Run AI enrichment`) that script the recommended demo flow. Use them after the APIs are running if you prefer a guided experience instead of manual `curl` commands.

## 11. Ingest sample data

Once the ingest API is running, load the sample JSON payloads.

### Fastest option

```powershell
python scripts/seed_sample_data.py
```

This script runs the same ingestion sequence as the UI helper buttons and is the best way to reset the demo for a live walkthrough.

### Manual file uploads

Run the POST `/ingest/file` endpoint for each sample payload if you want granular control over the sequence:

```powershell
curl.exe -X POST "http://localhost:8000/ingest/file" -F "file=@sample_data/qradar/qradar_event_001.json"
```

```powershell
curl.exe -X POST "http://localhost:8000/ingest/file" -F "file=@sample_data/iam/iam_event_001.json"
```

```powershell
curl.exe -X POST "http://localhost:8000/ingest/file" -F "file=@sample_data/vendor/vendor_event_001.json"
```

```powershell
curl.exe -X POST "http://localhost:8000/ingest/file" -F "file=@sample_data/telemetry/telemetry_event_001.json"
```

```powershell
curl.exe -X POST "http://localhost:8000/ingest/file" -F "file=@sample_data/risk/risk_context_001.json"
```

### Optional adapter tests

If you prefer source-shaped payloads instead of file uploads, hit the adapter endpoints directly:

```powershell
curl.exe -X POST "http://localhost:8000/adapters/jira/webhook" -H "Content-Type: application/json" --data-binary "@sample_data/jira/jira_webhook_001.json"
```

```powershell
curl.exe -X POST "http://localhost:8000/adapters/servicenow/event" -H "Content-Type: application/json" --data-binary "@sample_data/servicenow/servicenow_incident_001.json"
```

These help demonstrate the adapter-specific logic and can be mixed with the file-ingest approach.

## 12. Run the workers

The normalizer and correlator are one-shot jobs. They do one pass and then exit.

Run the normalizer:

```powershell
python scripts/run_normalizer.py
```

Run the correlator:

```powershell
python scripts/run_correlator.py
```

Expected outcome:

- raw events move to canonical events,
- canonical events are grouped and correlated,
- the sample data should produce at least one candidate incident for `portfolio-api`.

A quick health check after each worker run is to query the relevant tables:

```powershell
docker exec -it resilience_postgres psql -U resilience -d resilience -c "SELECT id, source_system, source_type, ingest_status FROM raw_events ORDER BY created_at DESC;"
```

```powershell
docker exec -it resilience_postgres psql -U resilience -d resilience -c "SELECT event_id, source_type, event_type, linked_service, correlation_status FROM canonical_events ORDER BY created_at DESC;"
```

```powershell
docker exec -it resilience_postgres psql -U resilience -d resilience -c "SELECT incident_id, status, service_name, confidence_score, rule_hits FROM candidate_incidents ORDER BY created_at DESC;"
```

These mirror the demo queries in the ReadMe and give immediate confirmation that each stage completed before moving on.

### Optional continuous loop mode

The normalizer and correlator support continuous polling for new work. To enable loop mode, set these environment variables before running the scripts:

**Normalizer loop mode:**
```
NORMALIZER_LOOP=true
NORMALIZER_POLL_SECONDS=5
NORMALIZER_MAX_CYCLES=0
```

**Correlator loop mode:**
```
CORRELATOR_LOOP=true
CORRELATOR_POLL_SECONDS=5
CORRELATOR_MAX_CYCLES=0
```

With `MAX_CYCLES=0`, the worker will loop indefinitely. Set a positive integer to limit the number of cycles. These settings allow the workers to continuously monitor for new events and correlations rather than running a single pass.

If you enable loop mode, keep an eye on terminal output; each cycle prints the number of processed rows so you can stop the worker once new data dries up.

## 13. Review and enrich incidents

Useful URLs:

- ingest API docs: `http://localhost:8000/docs`
- intelligence service docs: `http://localhost:8001/docs`
- control API docs: `http://localhost:8002/docs`
- review UI: `http://localhost:8002/ui`

From the UI you can:

- inspect incident details,
- view business context,
- trigger AI enrichment,
- record review actions,
- update incident status.

You can also trigger enrichment directly.

Call the intelligence service:

```powershell
curl.exe -X POST "http://localhost:8001/incidents/<incident_id>/enrich" -H "accept: application/json"
```

Or use the control API proxy:

```powershell
curl.exe -X POST "http://localhost:8002/incidents/<incident_id>/enrich" -H "accept: application/json"
```

## 14. Verification queries

Use these PostgreSQL commands to inspect each stage of the pipeline.

Tip: keep these queries handy during a demo so you can pivot quickly between raw events, canonical events, and incidents when someone asks "where do you see that in the data?"

### Raw events

```powershell
docker exec -it resilience_postgres psql -U resilience -d resilience -c "SELECT id, source_system, source_type, ingest_status, created_at FROM raw_events ORDER BY created_at DESC;"
```

### Canonical events

```powershell
docker exec -it resilience_postgres psql -U resilience -d resilience -c "SELECT event_id, source_type, event_type, linked_service, correlation_status FROM canonical_events ORDER BY created_at DESC;"
```

### Candidate incidents

```powershell
docker exec -it resilience_postgres psql -U resilience -d resilience -c "SELECT incident_id, status, service_name, confidence_score, rule_hits FROM candidate_incidents ORDER BY created_at DESC;"
```

### AI enrichment requests

```powershell
docker exec -it resilience_postgres psql -U resilience -d resilience -c "SELECT request_id, incident_id, route_used, model_id, requested_at FROM ai_enrichment_requests ORDER BY requested_at DESC;"
```

### AI enrichment responses

```powershell
docker exec -it resilience_postgres psql -U resilience -d resilience -c "SELECT request_id, schema_valid, latency_ms, created_at FROM ai_enrichment_responses ORDER BY created_at DESC;"
```

## 15. Recommended demo sequence

A clean demo sequence is:

1. start infrastructure,
2. start the three APIs,
3. ingest the sample files or run the UI helper buttons (`Reset & seed`, `Reset, seed & enrich`),
4. run the normalizer,
5. run the correlator,
6. open the review UI,
7. trigger AI enrichment for a created incident (either through the UI button or `curl`),
8. show the database records for each stage.

A concise narrative for the demo is:

- multi-source signals are ingested,
- the platform normalizes them into a canonical model,
- deterministic rules create a candidate incident,
- the intelligence layer is invoked after that point,
- PostgreSQL remains authoritative,
- AI assists the operator but does not decide.

## 16. Normalization design patterns

This prototype uses a simplified approach where source-to-canonical mapping is hardcoded in the normalizer. In a production system, normalization should follow a three-layer architecture to remain scalable:

### Layer 1: Source adapters

Each adapter knows how to parse one family of inputs:

- QRadar
- Microsoft Sentinel
- PAM/IAM logs
- Prometheus/Grafana alerts
- cloud audit logs
- vendor status feeds
- ServiceNow/Jira webhooks

### Layer 2: Canonical event taxonomy

Every parsed event is mapped into a stable internal vocabulary. Rather than creating hundreds of vendor-specific classifications, use a standard set like:

- `authentication.failure`
- `privileged_access.failure_burst`
- `service.availability.degraded`
- `dependency.vendor.outage`
- `backup.job.failed`
- `endpoint.data_exfiltration_suspected`

This internal taxonomy should be small and stable across operational domain changes.

### Layer 3: Correlation rules

Correlation rules operate on the canonical taxonomy, not on raw vendor payloads. Rules match patterns in the standardized event vocabulary and create incident candidates.

The advantage of this separation is that vendor payload changes do not cascade into correlation logic. You adjust adapters, not rules.

### Standards and references

Consider adopting standards such as:

- **Elastic Common Schema (ECS)**: defines common names and categories for security events across tools (event.kind, event.category, event.type, event.outcome),
- **OpenTelemetry semantic conventions**: defines common names for telemetry attributes across logs, traces, and metrics.

These standards reduce the operational overhead of maintaining a custom taxonomy.

## 17. Demo scenarios and talking points

The `ReadMe.txt` file includes an expanded incident catalog and runbook narrative. The highlights below consolidate those talking points alongside the concrete operator actions needed for each scenario.

The system is designed to be presented as a controlled demonstration of how deterministic correlation and bounded AI assistance support operational resilience.

### Core message

"The platform produces candidate incidents, not automatic final incidents. Deterministic rules create the control object. AI is invoked after correlation, not before. The AI output is advisory and bounded. The authoritative state remains in PostgreSQL. The architecture supports operational resilience, auditability, and replay."

### Scenario A: Privileged access anomaly on a critical service

**Service**: `portfolio-api`

**Signals**:
- failed privileged access attempt
- repeated failed privileged access attempts
- critical service context (portfolio-api is marked as critical)

**Expected rule hits**: `PRIV_ACCESS_CRITICAL_SERVICE`

**What to say**: "This is the simplest but important case: the platform identifies a privileged-access anomaly affecting a critical service. The point is not just detecting a login problem; it is recognizing that this affects a business-critical service and should enter an incident workflow."

**How to demo**:
- use the UI `Reset & seed` flow or re-run `python scripts/seed_sample_data.py`
- run `python scripts/run_normalizer.py`
- run `python scripts/run_correlator.py`
- open the incident detail for `portfolio-api` and trigger enrichment

**After triggering enrichment, highlight**:
- short incident summary
- plausible hypotheses
- explicit uncertainty
- review memo

**Key message**: "AI is helping package the case, not deciding whether it is reportable."

### Scenario B: Vendor degradation affecting service availability

**Service**: `client-reporting`

**Signals**:
- vendor degradation or outage
- synthetic failure on the service
- service error rate or latency degradation

**Expected rule hits**: `VENDOR_DEGRADATION_WITH_SERVICE_ERRORS` (and possibly additional service degradation rules)

**How to demo**:
- follow the same reset/seed + worker steps
- open the `client-reporting` incident with multiple rule hits
- emphasize that the incident aggregates vendor, telemetry, and application signals

**What to say**: "This is more realistic operationally. No single alert is enough. A vendor issue alone may be noise; a synthetic failure alone may be local; an application error rate alone may be ambiguous. Together they form a credible candidate incident."

**What to highlight**:
- multi-source fusion from different operational domains
- service-level context is visible in the incident
- vendor relationship is visible in the incident details
- richer rule hits than a single alert-based system would produce

**Key message**: "The platform is useful because it identifies combinations of weak evidence."

### Scenario C: Cyber plus operational degradation

**Service**: `client-reporting`

**Signals**:
- suspicious identity signal or data exfiltration event
- operational degradation on the same service
- both signals target or affect the same service context

**Expected rule hits**: compound rule matching cyber + operational signals on the same service

**How to demo**:
- reuse the seeded dataset; the expanded sample set now includes identity anomalies
- from the UI, open the `client-reporting` incident that shows both cyber and operational rule hits
- highlight the rule_hits list to illustrate compound detection

**What to say**: "This is where the architecture becomes more valuable. The platform can recognize that a cyber signal and a service degradation signal are not independent. It creates a single candidate incident that is more meaningful than two disconnected alerts."

**What to highlight**:
- multiple rule hits on one incident
- same-service correlation ensures related signals are grouped
- improved incident packaging for review teams

**Key message**: "The value is not just detection speed; it is better incident framing."

### Scenario D: Batch/platform instability

**Service**: `nav-batch`

**Signals**:
- restart storm or platform instability signal
- queue backlog
- batch delay or batch job failure

**Expected rule hits**: `PLATFORM_INSTABILITY_RESTART_STORM` and `BATCH_DEGRADATION_QUEUE_BACKLOG`

**How to demo**:
- after the standard ingest/worker steps, filter for `nav-batch` in the UI
- point out that the incident mixes platform telemetry with batch metrics
- emphasize that this is a non-cyber reliability scenario

**What to say**: "This shows the platform is not limited to security-style incidents. It can also correlate platform instability and workload degradation in a non-interactive service."

**What to highlight**:
- another service type (batch vs. API)
- non-cyber operational case (platform reliability vs. security)
- multiple signals tied to service context

**Key message**: "The design is cross-domain, not SIEM-centric."

### Recommended presentation order

Present incidents in this order for maximum impact:

1. Reset and seed the database (show a fresh run)
2. Show raw events (highlight multi-source ingestion)
3. Show canonical events (explain normalization)
4. Show three candidate incidents from the sample data
5. Open Scenario A (portfolio-api) and trigger enrichment
6. Show the AI enrichment output
7. Open Scenario B (client-reporting) and show multi-rule incident
8. Open Scenario D (nav-batch) and show non-cyber operational incident

This progression demonstrates:
- simple case → compound operational/vendor case → richer cross-domain case
- signal fusion → normalization → correlation → enrichment
- deterministic control throughout

### Best demo closing line

"This prototype is not trying to automate incident authority. It is showing how to combine deterministic control, structured evidence, and bounded AI assistance into a resilience-oriented operating model."

## 18. Troubleshooting

### No incidents appear in the UI

Check that:

- sample files were ingested successfully,
- `python scripts/run_normalizer.py` completed successfully,
- `python scripts/run_correlator.py` completed successfully,
- rows exist in `candidate_incidents`.

### Intelligence service fails to enrich

Check that:

- LM Studio is open,
- the local server is enabled,
- the configured model is loaded,
- `LLM_MODEL` matches the model identifier exposed by LM Studio,
- the route matches the request style being used,
- if you started the intelligence service manually, `LLM_CHAT_PATH` is set appropriately.

### MinIO errors during ingest

Check that:

- Docker containers are running,
- `scripts/setup_minio.py` completed successfully,
- MinIO is reachable at `http://localhost:9000`,
- the `raw-events` bucket exists.

To verify MinIO manually:

- **MinIO API**: open `http://localhost:9000`
- **MinIO Console**: open `http://localhost:9001`
- **Credentials**: user: `minio`, password: `minio12345`
- **Expected buckets**: `raw-events`, `artifacts`, `prompts`, `reports`

### Database schema problems

If the schema is missing or stale, rebuild the environment:

```powershell
.\scripts\bootstrap.ps1 -RebuildDb
```

## 19. Repository map

Useful locations:

- `app/common/` - shared config, DB, repository, schemas, MinIO, and LM Studio helpers
- `app/ingest_api/` - ingest API
- `app/normalizer_worker/` - raw-to-canonical normalization logic
- `app/correlator_worker/` - rule-based correlation logic
- `app/intelligence_service/` - AI enrichment service
- `app/control_api/` - control API and review UI
- `db/init.sql` - PostgreSQL schema, indexes, triggers, and seed data
- `rules/correlation_rules.yaml` - correlation rules
- `reference/` - prompt reference material
- `sample_data/` - demo payloads
- `scripts/` - bootstrap, setup, seeding, and worker entry points
- `.run/` - committed PyCharm run configurations for the three APIs

## 20. Current limits and future direction

This repository is intentionally minimal. It is optimized for local inspection and demo flow rather than production hardening.

Notable current limits:

- no scheduler or queue for the workers,
- limited UI functionality,
- local-file reference retrieval rather than a retrieval service,
- minimal validation around source-specific payloads,
- mixed LM Studio configuration paths that should be standardized later.

Even with those constraints, the repository already demonstrates the core resilience workflow: ingest, normalize, correlate, enrich, and review.
