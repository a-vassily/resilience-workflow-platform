# Resilience Platform Architecture and Runbook

This document explains how the prototype is structured, how data moves through the system, and how to run the project locally.

## 1. Purpose

This repository is a local resilience-platform prototype for:

- ingesting operational and security signals from multiple sources,
- normalizing them into a canonical event model,
- correlating related events into candidate incidents,
- enriching incidents with bounded LLM-generated analysis,
- keeping authoritative workflow state in PostgreSQL,
- exposing a lightweight review UI for human decisions.

The design deliberately separates:

- deterministic system state and workflow data, and
- advisory AI output.

PostgreSQL remains the source of truth. AI output is stored separately and does not make final decisions.

## 2. High-level architecture

The project has five main application components plus local infrastructure.

### Application services

1. `app/ingest_api/main.py`
   - FastAPI service for receiving raw events and uploaded JSON files.
   - Stores raw payloads in MinIO.
   - Records metadata and payload references in PostgreSQL.
   - Includes adapter-style endpoints for Jira and ServiceNow payloads.

2. `app/normalizer_worker/main.py`
   - One-shot worker.
   - Reads unprocessed raw events from PostgreSQL.
   - Maps each source payload to a canonical event shape.
   - Updates service context when risk-context payloads are received.

3. `app/correlator_worker/main.py`
   - One-shot worker.
   - Reads uncorrelated canonical events.
   - Groups events by service or vendor reference.
   - Applies rules from `rules/correlation_rules.yaml`.
   - Creates candidate incidents and incident-to-event links.

4. `app/intelligence_service/main.py`
   - FastAPI service for AI enrichment.
   - Builds a prompt package using incident data plus reference material.
   - Calls LM Studio through an OpenAI-compatible local endpoint.
   - Stores prompt metadata, response payloads, latency, and validation status.

5. `app/control_api/main.py`
   - FastAPI service for incident review and orchestration.
   - Lists incidents, returns incident detail, records review actions, updates status, and triggers enrichment.
   - Serves the static browser UI at `/ui`.

### Shared application layer

`app/common/` contains shared code used across services:

- `config.py`: environment-driven settings
- `db.py`: SQLAlchemy engine and session factory
- `repository.py`: database read/write helpers
- `llm.py`: LM Studio request/response helpers
- `minio_client.py`: MinIO client creation
- `schemas.py`: shared Pydantic request models

### Local infrastructure

`docker-compose.yml` starts:

- PostgreSQL
- MinIO

LM Studio is expected to run locally outside Docker.

## 3. Data flow

The core flow is:

1. A raw event is submitted to the ingest API.
2. The raw JSON is stored in MinIO under the `raw-events` bucket.
3. A record is written to `raw_events` in PostgreSQL.
4. The normalizer converts raw events into canonical events.
5. Canonical events are stored in `canonical_events`.
6. The correlator groups canonical events and applies correlation rules.
7. Matching groups become candidate incidents in `candidate_incidents`.
8. The control API exposes incidents for review.
9. The intelligence service generates bounded enrichment for a chosen incident.
10. Review actions and status changes are recorded for auditability.

## 4. Storage model

### PostgreSQL

PostgreSQL stores the authoritative operational data model.

Key tables from `db/init.sql`:

- `raw_events`: original source payload metadata and ingest status
- `canonical_events`: normalized event records
- `service_context`: business and service metadata
- `risk_context_refs`: risk-context reference payloads
- `candidate_incidents`: correlated incident candidates
- `incident_event_links`: many-to-many links between incidents and events
- `incident_artifacts`: references to incident-related files or outputs
- `ai_enrichment_requests`: prompt and request metadata
- `ai_enrichment_responses`: model outputs and validation results
- `review_actions`: human review and status-change activity
- `remediation_actions`: follow-up work items
- `audit_log`: general audit records

### MinIO

MinIO stores larger payloads and artifacts by reference.

Buckets expected by the project:

- `raw-events`
- `artifacts`
- `prompts`
- `reports`

The ingest API currently writes raw uploaded payloads to `raw-events`.

### Reference files

The intelligence service also reads local reference material from `reference/`:

- `runbooks.json`
- `prior_incidents.json`
- `risk_context.json`
- `services.json`

These files act as lightweight retrieval context for the prototype.

## 5. Runtime behavior by component

### Ingest API

Main endpoints:

- `GET /health`
- `POST /ingest/event`
- `POST /ingest/file`
- `POST /adapters/jira/webhook`
- `POST /adapters/servicenow/event`

Responsibilities:

- infer source type from uploaded payload shape,
- persist evidence in MinIO,
- create a raw-event database record.

### Normalizer worker

Main entry points:

- module function: `app.normalizer_worker.main.run_once`
- script: `scripts/run_normalizer.py`

Responsibilities:

- process rows in `raw_events` with `ingest_status = 'received'`,
- generate canonical records,
- mark raw events as normalized,
- upsert service context when source type is `risk_context`.

### Correlator worker

Main entry points:

- module function: `app.correlator_worker.main.run_once`
- script: `scripts/run_correlator.py`

Responsibilities:

- read canonical events with `correlation_status = 'new'`,
- match grouped events against YAML-defined rules,
- create incidents and event links,
- mark canonical events as correlated.

### Intelligence service

Main endpoints:

- `GET /health`
- `POST /incidents/{incident_id}/enrich`

Responsibilities:

- load incident context,
- build prompt package,
- call LM Studio,
- validate required output fields,
- store request/response metadata,
- return the generated enrichment.

If the model call fails or returns invalid JSON, the service falls back to a deterministic response so the demo can continue.

### Control API and UI

Main endpoints:

- `GET /health`
- `GET /incidents`
- `GET /incidents/{incident_id}`
- `POST /incidents/{incident_id}/review`
- `POST /incidents/{incident_id}/status`
- `POST /incidents/{incident_id}/enrich`
- `GET /ui`

Responsibilities:

- expose incidents for review,
- show enrichment and review history,
- persist review actions,
- proxy enrichment requests to the intelligence service,
- serve the browser UI in `app/control_api/static/index.html`.

## 6. Running the project locally

## Prerequisites

Install or prepare the following:

- Python 3.11+
- Docker Desktop
- LM Studio running locally
- optional: PyCharm for the included run configurations

## Environment configuration

The project reads settings from `.env`.

Important values:

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

A working local example already exists in `.env`.

## Step 1: Start infrastructure

From the repository root:

```powershell
.\scripts\bootstrap.ps1
```

If you need a clean rebuild of the database volume:

```powershell
.\scripts\bootstrap.ps1 -RebuildDb
```

What the bootstrap script does:

- verifies Docker,
- starts PostgreSQL and MinIO,
- installs Python dependencies,
- applies `db/init.sql`,
- creates MinIO buckets,
- runs environment checks.

If you prefer to start containers manually:

```powershell
docker compose up -d
```

## Step 2: Make sure LM Studio is ready

Before using the intelligence service:

- open LM Studio,
- enable the local server,
- load the selected model,
- ensure the loaded model matches `LLM_MODEL`,
- ensure the API path matches `LLM_CHAT_PATH`.

The current `.env` points to:

- base URL: `http://localhost:1234`
- path: `/v1/responses`

## Step 3: Start the APIs

You can start them from PyCharm using the project run configurations in `.run/`, or from a terminal.

### Terminal option

Open separate terminals and run:

```powershell
python -m uvicorn app.ingest_api.main:app --reload --port 8000
```

```powershell
python -m uvicorn app.intelligence_service.main:app --reload --port 8001
```

```powershell
python -m uvicorn app.control_api.main:app --reload --port 8002
```

### PyCharm option

Use these run configurations:

- `Ingest API`
- `Intelligence Service`
- `Control API`

## Step 4: Ingest sample data

Once the ingest API is running, load the sample JSON files.

Simplest option:

```powershell
python scripts/seed_sample_data.py
```

Manual option:

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

Optional adapter-style tests:

```powershell
curl.exe -X POST "http://localhost:8000/adapters/jira/webhook" -H "Content-Type: application/json" --data-binary "@sample_data/jira/jira_webhook_001.json"
```

```powershell
curl.exe -X POST "http://localhost:8000/adapters/servicenow/event" -H "Content-Type: application/json" --data-binary "@sample_data/servicenow/servicenow_incident_001.json"
```

## Step 5: Run the workers

These workers are one-shot scripts, not long-running background services.

Run the normalizer:

```powershell
python scripts/run_normalizer.py
```

Run the correlator:

```powershell
python scripts/run_correlator.py
```

Expected result:

- raw events become canonical events,
- correlated events create one or more candidate incidents,
- the sample data should produce a candidate incident for `portfolio-api`.

## Step 6: Review incidents

Open:

- ingest API docs: `http://localhost:8000/docs`
- intelligence service docs: `http://localhost:8001/docs`
- control API docs: `http://localhost:8002/docs`
- review UI: `http://localhost:8002/ui`

In the UI you can:

- list incidents,
- inspect business context,
- trigger AI enrichment,
- record review actions,
- change incident status.

## Step 7: Trigger enrichment

From the UI, click the enrichment button, or call the endpoint directly.

Example:

```powershell
curl.exe -X POST "http://localhost:8001/incidents/<incident_id>/enrich" -H "accept: application/json"
```

You can also use the control API proxy endpoint:

```powershell
curl.exe -X POST "http://localhost:8002/incidents/<incident_id>/enrich" -H "accept: application/json"
```

## 7. Verification queries

Use these PostgreSQL queries to verify each stage of the flow.

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

## 8. Suggested demo sequence

For a clean demo, run the system in this order:

1. bootstrap infrastructure,
2. start the three APIs,
3. ingest sample files,
4. run the normalizer,
5. run the correlator,
6. open the review UI,
7. trigger enrichment for a created incident,
8. show the database records for each stage.

A concise demo narrative is:

- multi-source signals are ingested,
- events are normalized into a canonical model,
- deterministic rules create a candidate incident,
- AI is invoked only after that point,
- PostgreSQL remains authoritative,
- AI assists the reviewer but does not decide.

## 9. Troubleshooting

### No incidents appear in the UI

Check that:

- sample files were ingested successfully,
- `scripts/run_normalizer.py` completed,
- `scripts/run_correlator.py` completed,
- `candidate_incidents` contains rows.

### Intelligence service fails

Check that:

- LM Studio is open,
- the local server is enabled,
- the configured model is loaded,
- `LLM_MODEL` matches the model identifier exposed by LM Studio,
- `LLM_CHAT_PATH` matches the active API route.

### MinIO errors during ingest

Check that:

- Docker containers are running,
- bucket creation succeeded,
- MinIO is reachable at `http://localhost:9000`,
- the `raw-events` bucket exists.

### Database schema issues

Re-run bootstrap with a rebuild if needed:

```powershell
.\scripts\bootstrap.ps1 -RebuildDb
```

## 10. Repository map

Useful locations:

- `app/common/` - shared config, DB, repository, schemas, LLM helpers
- `app/ingest_api/` - ingest service
- `app/normalizer_worker/` - normalization logic
- `app/correlator_worker/` - correlation logic
- `app/intelligence_service/` - enrichment service
- `app/control_api/` - control API and review UI
- `db/init.sql` - schema and seed data
- `rules/correlation_rules.yaml` - incident correlation rules
- `reference/` - retrieval/reference context for enrichment
- `sample_data/` - local demo payloads
- `scripts/` - setup, seed, and one-shot worker scripts

## 11. Current implementation characteristics

This prototype intentionally favors clarity over production complexity.

Current characteristics:

- synchronous request handling,
- one-shot workers instead of schedulers or queues,
- SQL written through repository helpers,
- local-file reference context for retrieval,
- lightweight static UI,
- deterministic fallback when model output is unavailable.

That makes the project easy to inspect, run, and demo locally while preserving the core architecture of a resilience workflow system.
