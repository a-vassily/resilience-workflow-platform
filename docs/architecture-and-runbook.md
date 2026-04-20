# Resilience Platform Architecture and Runbook

This document describes the current repository architecture, the end-to-end data flow, and the exact steps to run the prototype locally.

THIS IS A PROOF OF CONCEPT TO RUN LOCALLY. DO NOT RUN ON A SERVER, DO NOT EXPOSE THE URLs ON THE NETWORK OR THE INTERNET. 
YOU WILL BE HACKED. YOU HAVE BEEN WARNED. 

## 1. What this project is

This repository is a local prototype for a resilience workflow platform that:

- ingests operational, security, and context signals from multiple sources,
- stores raw evidence separately from normalized workflow state,
- normalizes source-specific payloads into a canonical event model,
- correlates related events into candidate incidents using deterministic YAML rules,
- enriches incidents with bounded LLM-generated analysis,
- supports human review, status transitions, and classification through a lightweight UI,
- assembles versioned incident packs (structured JSON dossiers) for each incident,
- generates draft regulatory reports with explicit AI-draft disclaimers,
- tracks remediation actions with status, owner, and closure evidence,
- records a full audit trail of every system and human action.

The design keeps authoritative state in PostgreSQL. AI output is advisory, stored separately, and does not make final decisions.

What is included
Docker Compose for infrastructure only
SQL schema for canonical events, candidate incidents, AI enrichment, and review actions
FastAPI ingest API with adapter stubs for Jira and ServiceNow style payloads
FastAPI control API with a lightweight browser review UI
Normalizer worker
Correlator worker
Intelligence service
Sample source-event JSON files
Project-stored PyCharm run configurations under .run/
Reference context files
Initial correlation rules


## 2. Architecture overview

The system is split into application services, shared support code, and local infrastructure.

### Application services

#### `app/ingest_api/main.py`

FastAPI service responsible for collecting source events.

Main responsibilities:

- receive uploaded JSON files and direct event payloads,
- infer a source type for uploaded files using `_guess_source_type`,
- persist the raw payload to MinIO (`raw-events` bucket),
- create a `raw_events` row in PostgreSQL,
- expose adapter-style endpoints for Jira and ServiceNow shaped payloads that map source-native structures before storage.

Main endpoints:

- `GET /health`
- `POST /ingest/event` — direct JSON ingestion with explicit source_type
- `POST /ingest/file` — file upload with automatic source_type inference
- `POST /adapters/jira/webhook` — maps Jira webhook to telemetry_alert shape
- `POST /adapters/servicenow/event` — maps ServiceNow incident to identity_event shape

Source type inference (`_guess_source_type`) detects:

| Payload field present | Inferred source_type |
|---|---|
| `offense_id` | `security_event` |
| `system == 'cyberark'` or `failure_count` | `identity_event` |
| `vendor_event_id` or `impacted_services` | `vendor_event` |
| `alert_id` or `tool == 'grafana'` | `telemetry_alert` |
| `risk_ref` | `risk_context` |
| (none of the above) | `unknown` |

Jira and ServiceNow payloads do not match the file inference rules and must be submitted to the dedicated adapter endpoints.

#### `app/normalizer_worker/main.py`

One-shot worker that transforms raw events into canonical events.

Main responsibilities:

- read unprocessed rows from `raw_events` (where `ingest_status = 'received'`),
- map source-specific payloads into the canonical event shape per source type,
- insert rows into `canonical_events`,
- upsert service context when the source type is `risk_context`,
- mark raw rows as normalized.

The worker processes events with per-event exception handling: if a single event fails normalization (for example, an unknown source type), the error is printed, that event is skipped, and processing continues with the next row. The failed raw event is still marked as normalized to avoid infinite retry.

**Supported source types and resulting event_type values:**

| source_type | Payload indicator | Canonical event_type |
|---|---|---|
| `security_event` | `exfil` in event_name or category | `data_exfiltration_alert` |
| `security_event` | `break glass` in event_name | `break_glass_account_used` |
| `security_event` | `admin session` in event_name | `suspicious_admin_session` |
| `security_event` | `malware` in event_name | `malware_detected` |
| `security_event` | (fallthrough) | `failed_privileged_access` |
| `identity_event` | `impossible_travel_admin_login` | `impossible_travel_admin_login` |
| `identity_event` | `pam_break_glass_used` | `break_glass_account_used` |
| `identity_event` | `privileged_group_change` | `privileged_group_change` |
| `identity_event` | `incident_ticket_signal` | `incident_ticket_signal` |
| `identity_event` | (fallthrough) | `repeated_failed_privileged_access` |
| `vendor_event` | status `outage/down/unavailable` | `vendor_outage` |
| `vendor_event` | status `latency/degraded-performance` | `vendor_latency_degradation` |
| `vendor_event` | status `sla_breach` | `vendor_sla_breach` |
| `vendor_event` | (fallthrough) | `vendor_degradation` |
| `telemetry_alert` | restart in name/metric | `pod_restart_storm` |
| `telemetry_alert` | cpu in name or metric | `cpu_saturation` |
| `telemetry_alert` | memory in name or metric | `memory_pressure` |
| `telemetry_alert` | synthetic in name | `synthetic_check_failure` |
| `telemetry_alert` | latency in name or metric | `latency_spike` |
| `telemetry_alert` | queue in name or metric | `queue_backlog_high` |
| `telemetry_alert` | batch in name or metric | `batch_job_failure` |
| `telemetry_alert` | disk in name or metric | `disk_full_risk` |
| `telemetry_alert` | backup in name or metric | `backup_failure` |
| `telemetry_alert` | (fallthrough) | `service_error_rate_high` |
| `risk_context` | (always) | `risk_context_update` |

Primary entry points:

- module function: `app.normalizer_worker.main.run_once`
- script: `scripts/run_normalizer.py`

#### `app/correlator_worker/main.py`

One-shot worker that groups canonical events and applies deterministic rules.

Main responsibilities:

- read canonical events with `correlation_status = 'new'`,
- group events by linked_service (falling back to affected_asset, then vendor_reference),
- load rules from `rules/correlation_rules.yaml`,
- evaluate each rule's conditions against each service group,
- create `candidate_incidents` and `incident_event_links` for matching groups,
- emit an `incident.created` audit event for each created incident,
- mark processed canonical events as correlated.

**Threshold flags** computed for each incident (used in UI and incident pack):

| Flag | Condition |
|---|---|
| `critical_service_impact` | service context has `critical_service = true` |
| `unauthorized_access_indicator` | any event in group is of type: `failed_privileged_access`, `repeated_failed_privileged_access`, `impossible_travel_admin_login`, `data_exfiltration_alert`, `break_glass_account_used`, `suspicious_admin_session`, `malware_detected`, `privileged_group_change` |
| `multi_signal_pattern` | group has 2 or more events |
| `vendor_dependency_issue` | any event has `source_type = vendor_event` |
| `platform_instability` | any event is of type `pod_restart_storm`, `cpu_saturation`, or `memory_pressure` |

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
- store prompt/request metadata and response metadata in PostgreSQL,
- emit an `incident.enrichment.completed` audit event,
- return the enrichment result.

Main endpoints:

- `GET /health`
- `POST /incidents/{incident_id}/enrich`

Important behavior:

- if the model call fails or the response is not valid JSON, the service returns a deterministic fallback payload so the demo flow can continue,
- prompt integrity is tracked via SHA-256 hash stored alongside the request metadata,
- the prompt is structured with six fixed sections: system, task, incident_context, retrieved_evidence, constraints, output_schema.

#### `app/control_api/main.py`

FastAPI service for review workflows, UI orchestration, and the full incident lifecycle.

Main responsibilities:

- list and return incidents,
- record review actions (with automatic audit event emission),
- update incident status (with automatic audit event emission and auto-triggered pack assembly),
- proxy enrichment requests to the intelligence service (with automatic audit event emission and auto-triggered pack assembly after enrichment),
- assemble and proxy incident packs and report drafts,
- manage remediation actions,
- serve the browser UI.

Main endpoints:

| Endpoint | Purpose |
|---|---|
| `GET /health` | liveness check |
| `GET /incidents` | list all incidents |
| `GET /incidents/{id}` | incident detail |
| `POST /incidents/{id}/review` | record a review action; auto-audits |
| `POST /incidents/{id}/status` | update status; auto-audits; auto-assembles pack |
| `POST /incidents/{id}/enrich` | proxy to intelligence service; auto-audits; auto-assembles pack |
| `POST /incidents/{id}/pack` | assemble incident pack on demand |
| `GET /incidents/{id}/pack` | retrieve current incident pack from MinIO |
| `POST /incidents/{id}/reporting/draft` | generate report draft |
| `GET /incidents/{id}/reporting/draft` | retrieve current report draft from MinIO |
| `POST /incidents/{id}/remediation` | create a remediation action |
| `GET /incidents/{id}/remediation` | list all remediation actions |
| `PATCH /incidents/{id}/remediation/{rem_id}` | update remediation status or closure evidence |
| `GET /incidents/{id}/audit` | list all audit events for the incident |
| `GET /ui` | serve the browser UI |

The UI (embedded in `main.py`) loads incident detail in a single `Promise.all` call, fetching remediation actions, the current incident pack, the current report draft, and the full audit log in parallel.

#### `app/control_api/incident_pack.py`

Module responsible for assembling incident packs.

Main responsibilities:

- collect the incident record, all linked canonical events, the latest enrichment, all review actions, and all remediation actions,
- build a versioned JSON dossier with a `generated_at` timestamp,
- store the pack in MinIO at `artifacts/incident-packs/{incident_id}/pack-{timestamp}.json`,
- update `incident_pack_ref` on the incident record,
- return the MinIO object reference.

The pack includes structured sections: `incident`, `events`, `enrichment_summary`, `review_actions`, `remediation_actions`, `generated_at`.

`fetch_incident_pack(ref)` retrieves a pack from MinIO by its reference string.

#### `app/control_api/reporting.py`

Module responsible for generating draft regulatory report documents.

Main responsibilities:

- build a structured draft with: classification_state, incident_summary, event_timeline, ai_draft_narrative (with its own disclaimer), evidence_references, review_actions_summary, remediation_summary, open_items, pending_approvals,
- store the draft in MinIO at `reports/incident-reports/{incident_id}/draft-{timestamp}.json`,
- update `reporting_pack_ref` on the incident record,
- return the MinIO object reference.

Every report draft carries a `DISCLAIMER` header and every AI-generated narrative section carries its own nested disclaimer. The open_items section is computed programmatically from incident state: missing classification, missing review actions, outstanding AI uncertainties, open remediations, and missing enrichment are all surfaced as explicit blockers.

### Shared application layer

Shared code lives under `app/common/`.

- `config.py`: environment-driven settings
- `db.py`: SQLAlchemy engine and session factory
- `repository.py`: SQL helpers used by the APIs and workers (see below for new functions)
- `llm.py`: helper functions for LM Studio request and response handling
- `minio_client.py`: MinIO client factory
- `schemas.py`: Pydantic v2 models for request payloads

New repository functions added since the initial implementation:

| Function | Purpose |
|---|---|
| `insert_audit_event(entity_type, entity_id, action_type, actor, details)` | Insert a timestamped audit record |
| `list_audit_events(entity_type, entity_id)` | Retrieve audit history for an entity |
| `insert_remediation_action(incident_id, title, description, owner, due_date, dependency_note)` | Create a remediation action, returns UUID |
| `list_remediation_actions(incident_id)` | List all remediation actions for an incident |
| `update_remediation_action(remediation_id, status, closure_evidence_ref, lessons_learned)` | Update remediation status |
| `set_incident_pack_ref(incident_id, ref)` | Store MinIO reference to the current incident pack |
| `set_reporting_pack_ref(incident_id, ref)` | Store MinIO reference to the current report draft |

New schemas in `app/common/schemas.py`:

- `RemediationCreateRequest`: title, description, owner, due_date, dependency_note
- `RemediationUpdateRequest`: status, closure_evidence_ref, lessons_learned (all optional)

### Local infrastructure

`docker-compose.yml` starts:

- PostgreSQL (port 5432)
- MinIO (API port 9000, console port 9001)

LM Studio is expected to run locally outside Docker.

## 3. End-to-end data flow

The full processing flow is:

1. A source event is submitted to the ingest API via `/ingest/file`, `/ingest/event`, or a dedicated adapter endpoint.
2. The raw payload is written to MinIO in the `raw-events` bucket; an evidence pointer (MinIO URI) is returned.
3. Metadata, source type, and the evidence pointer are inserted into `raw_events`.
4. The normalizer reads rows where `ingest_status = 'received'`.
5. Each raw payload is transformed into a canonical event and stored in `canonical_events`; errors are caught per-event and logged without crashing the batch.
6. If the payload is a `risk_context` update, service context is also upserted into `service_context`.
7. The correlator loads canonical events where `correlation_status = 'new'`.
8. Events are grouped by linked_service and checked against the 14 YAML correlation rules.
9. Matching groups create rows in `candidate_incidents` and `incident_event_links`; an `incident.created` audit event is written.
10. The control API exposes incidents for human review.
11. An operator triggers AI enrichment via the UI or API; the intelligence service assembles a prompt, calls LM Studio, validates the response, persists it, and emits an `incident.enrichment.completed` audit event.
12. After enrichment, the control API auto-assembles an incident pack and stores it in MinIO.
13. Human review actions and status transitions are recorded in `review_actions`; each action emits an audit event.
14. Status transitions also auto-trigger incident pack re-assembly.
15. Operators can generate a report draft on demand; the draft is stored in MinIO and its reference is saved on the incident.
16. Remediation actions can be created, listed, and updated with status, closure evidence, and lessons learned.
17. The full audit trail for any incident is available via `GET /incidents/{id}/audit`.

## 4. Storage model

### PostgreSQL

PostgreSQL is the system of record for operational workflow state.

Key tables defined in `db/init.sql`:

- `raw_events`: original source payload metadata and ingest state
- `canonical_events`: normalized event records with event_type, source_type, severity, linked_service, enrichment_tags
- `service_context`: service ownership, dependency, criticality context (upserted from risk_context payloads)
- `risk_context_refs`: risk-context reference payloads
- `candidate_incidents`: correlated incident candidates with full payload, threshold_flags, rule_hits, and lifecycle state
- `incident_event_links`: links between incidents and contributing canonical events
- `incident_artifacts`: references to stored incident artifacts
- `ai_enrichment_requests`: prompt hash, model, route, and timing metadata
- `ai_enrichment_responses`: model outputs, schema validation state, latency
- `review_actions`: human review and status-change activity
- `remediation_actions`: remediation follow-up records with owner, due date, status, closure evidence, and lessons learned
- `audit_log`: generic timestamped audit trail (entity_type, entity_id, action_type, actor, details)

The schema also defines `updated_at` triggers for key tables and seeds an example `service_context` row for `portfolio-api`.

**Incident status lifecycle:**

```
candidate → triage_pending → under_review → classified_internal
                                          → classified_reportable → reported_initial
                                                                  → reported_intermediate
                                                                  → reported_final
                                                                  → remediation_open → closed
```

### MinIO

MinIO stores larger payloads and externalized evidence.

Buckets and active usage:

| Bucket | Contents |
|---|---|
| `raw-events` | Raw ingested payloads, one file per ingest call (`{source_system}/{timestamp}.json`) |
| `artifacts` | Incident packs at `artifacts/incident-packs/{incident_id}/pack-{timestamp}.json` |
| `reports` | Report drafts at `reports/incident-reports/{incident_id}/draft-{timestamp}.json` |
| `prompts` | LLM prompt packages written by the intelligence service |

All MinIO reads use `.close()` and `.release_conn()` after streaming to avoid connection leaks.

### Reference data

The intelligence service reads local reference files from `reference/`:

| File | Entries | Purpose |
|---|---|---|
| `services.json` | 6 services | Service metadata, vendor, RTO, dependencies, escalation contact |
| `runbooks.json` | 6 runbooks | Service-specific first-response guidance for correlation |
| `risk_context.json` | 6 entries | Risk ratings, data classification, known risk notes |
| `prior_incidents.json` | 14 entries | Historical incidents used as retrieval context |

Services currently covered: `portfolio-api`, `client-reporting`, `nav-batch`, `iam-core`, `trade-booking`, `compliance-api`.

## 5. Correlation rules

Rules are loaded at runtime from `rules/correlation_rules.yaml`. There are currently 14 rules.

### Rule inventory

| Rule ID | Confidence | Severity | Description |
|---|---|---|---|
| `PRIV_ACCESS_CRITICAL_SERVICE` | 0.78 | high | Failed + repeated privileged access on critical service, 2+ distinct sources |
| `VENDOR_OUTAGE_WITH_SYNTHETIC_FAILURE` | 0.86 | critical | Vendor outage + synthetic check failure, vendor present |
| `VENDOR_DEGRADATION_WITH_SERVICE_ERRORS` | 0.74 | high | Vendor degradation + service error rate high, same service |
| `PLATFORM_INSTABILITY_CRITICAL_WORKLOAD` | 0.82 | high | Pod restart storm + capacity stress on critical service |
| `BREAK_GLASS_ADMIN_ACTIVITY` | 0.84 | critical | Break-glass or suspicious admin activity, 2+ sources, high severity |
| `EXFILTRATION_WITH_IDENTITY_ANOMALY` | 0.90 | critical | Data exfiltration + impossible travel, same service, 2+ sources |
| `STORAGE_CAPACITY_AND_BACKUP_FAILURE` | 0.79 | high | Disk full risk + backup failure or service errors |
| `BATCH_PROCESSING_DEGRADATION` | 0.68 | medium | Batch job failure + queue backlog or latency |
| `REPEATED_WEAK_SIGNAL_BURST` | 0.64 | medium | 3+ weak signals of the same type on the same service |
| `RISK_CONTEXT_AMPLIFIED_CYBER` | 0.72 | high | Risk context record + security/identity signal, same service |
| `RISK_CONTEXT_AMPLIFIED_OPERATIONAL` | 0.61 | medium | Risk context record + telemetry/vendor signal, same service |
| `PRIVILEGED_ACCESS_WITH_IMPOSSIBLE_TRAVEL` | 0.87 | critical | Privileged access failure + impossible travel on same service — credential compromise indicator |
| `VENDOR_OUTAGE_WITH_SERVICE_ERRORS` | 0.83 | high | Vendor outage or degradation + service error rate high, vendor reference present |
| `MALWARE_ON_CRITICAL_SERVICE` | 0.88 | critical | Malware detected on any asset of a critical service — single-signal rule |

### Supported condition types in `_matches()`

| Condition key | Semantics |
|---|---|
| `event_type_all` | All listed event_types must be present |
| `event_type_any` | At least one listed event_type must be present |
| `event_type_count_at_least` | N or more distinct event_types from a list must be present |
| `critical_service` | Service context must have `critical_service = true` |
| `tag_any` | At least one enrichment tag from the list must be present |
| `source_type_any` | At least one event with a matching source_type must be present |
| `same_linked_service` | All events must share a single linked_service value |
| `vendor_present` | At least one event must have a non-null vendor_reference |
| `min_distinct_sources` | At least N distinct source_type values must be represented |
| `severity_at_least` | The highest severity in the group must meet or exceed the threshold |

Multiple conditions of the same type in the `all` list are AND-ed (each condition dict is evaluated independently).

## 6. Sample data

50 sample JSON files across 7 source directories.

| Directory | Count | Events covered |
|---|---|---|
| `sample_data/qradar/` | 8 | Exfiltration, malware, failed logins, admin sessions across portfolio-api, trade-booking, compliance-api, iam-core |
| `sample_data/iam/` | 7 | Repeated failures, impossible travel, break-glass, privileged group change across portfolio-api, iam-core, trade-booking, compliance-api |
| `sample_data/telemetry/` | 19 | Error rates, latency, CPU, memory, disk, backup, queue, batch, outbound traffic across all 6 services |
| `sample_data/vendor/` | 6 | Degradation and outage from market-data-gateway, statement-renderer-saas, pricing-feed-hub, trade-execution-gateway, external-data-feed, identity-verification-svc |
| `sample_data/risk/` | 6 | Risk context for all 6 services (RISK-001 through RISK-006) |
| `sample_data/jira/` | 2 | ITSM incident signals for portfolio-api and trade-booking |
| `sample_data/servicenow/` | 2 | ITSM incident signals for portfolio-api and compliance-api |

### Expected incident candidates from the full sample dataset

After seeding and running both workers, the following candidate incidents should be created:

| Service | Expected rule hits |
|---|---|
| `portfolio-api` | `PRIV_ACCESS_CRITICAL_SERVICE`, `MALWARE_ON_CRITICAL_SERVICE`, `VENDOR_DEGRADATION_WITH_SERVICE_ERRORS`, `RISK_CONTEXT_AMPLIFIED_CYBER` |
| `client-reporting` | `VENDOR_OUTAGE_WITH_SYNTHETIC_FAILURE`, `EXFILTRATION_WITH_IDENTITY_ANOMALY`, `STORAGE_CAPACITY_AND_BACKUP_FAILURE` |
| `nav-batch` | `BATCH_PROCESSING_DEGRADATION`, `PLATFORM_INSTABILITY_CRITICAL_WORKLOAD` |
| `iam-core` | `BREAK_GLASS_ADMIN_ACTIVITY`, `RISK_CONTEXT_AMPLIFIED_CYBER` |
| `trade-booking` | `EXFILTRATION_WITH_IDENTITY_ANOMALY`, `PRIVILEGED_ACCESS_WITH_IMPOSSIBLE_TRAVEL`, `VENDOR_OUTAGE_WITH_SERVICE_ERRORS` |
| `compliance-api` | `PRIV_ACCESS_CRITICAL_SERVICE`, `VENDOR_OUTAGE_WITH_SERVICE_ERRORS`, `RISK_CONTEXT_AMPLIFIED_OPERATIONAL` |

Actual groupings depend on the events present in each batch and may merge or split depending on ingest order.

## 7. Runtime characteristics

This prototype intentionally favors clarity over production complexity.

Current characteristics:

- synchronous request handling,
- one-shot workers rather than long-running schedulers (loop mode available via env vars),
- SQL written through repository helper functions using raw `text()` queries,
- local JSON files used as retrieval context for AI prompts,
- a lightweight single-page review UI embedded in `control_api/main.py`,
- deterministic fallback output when the LLM route is unavailable,
- per-event exception handling in the normalizer so one bad event does not crash the batch,
- all audit trail writes are wrapped in try/except so audit failures do not affect primary operations.

## 8. Prerequisites

Prepare the following before running the demo:

- Python 3.11+
- Docker Desktop
- LM Studio with the local server enabled
- optional: PyCharm, if you want to use the committed run configurations

## 9. Environment and configuration

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

## 10. How to run the project

### Option A: Recommended bootstrap flow
Start by creating a virtual environment with your favourite tool and switch to it. 
Then: 
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

## 11. LM Studio setup

Before using the intelligence service, ensure:

- **LM Studio is open** — the application requires a running local LM Studio instance,
- **local server is enabled** — the `/v1/chat/completions` endpoint must be accessible,
- **the model is loaded** — select the model in LM Studio before testing,
- **model name matches configuration** — the `LLM_MODEL` environment variable must exactly match the model identifier exposed by LM Studio,
- **route matches the implementation** — verify `LLM_CHAT_PATH` is set correctly.

For the current PyCharm run configurations, the effective path is:

- `LLM_CHAT_PATH=/v1/chat/completions`

If you are relying only on `.env`, review the note in the previous section before starting the intelligence service.

## 12. Start the application processes

### PyCharm path

The repository includes committed run configurations for:

- `Ingest API`
- `Control API`
- `Intelligence Service`

These are stored under `.run/`.

Recommended start order:

1. `Ingest API`
2. `Intelligence Service`
3. `Control API`

For the workers, run the scripts manually from PyCharm or a terminal:

- `python scripts/run_normalizer.py`
- `python scripts/run_correlator.py`

### Terminal path

Open separate terminals.

Start the ingest API:

```powershell
python -m uvicorn app.ingest_api.main:app --reload --port 8000
```

Start the intelligence service (explicitly override the path to match the Chat Completions implementation):

```powershell
$env:LLM_CHAT_PATH='/v1/chat/completions'; python -m uvicorn app.intelligence_service.main:app --reload --port 8001
```

Start the control API:

```powershell
python -m uvicorn app.control_api.main:app --reload --port 8002
```

### UI automation helpers

The control API UI exposes helper buttons (`Reset & seed`, `Reset, seed & enrich`, `Run AI enrichment`) that script the recommended demo flow. 
Use them after the APIs are running if you prefer a guided experience instead of manual `curl` commands.

## 13. Ingest sample data

Once the ingest API is running, load the sample JSON payloads.

### Fastest option

```powershell
python scripts/seed_sample_data.py
```

This script routes each file to the correct endpoint automatically:

- files from `sample_data/jira/` are posted to `/adapters/jira/webhook`,
- files from `sample_data/servicenow/` are posted to `/adapters/servicenow/event`,
- all other files are posted to `/ingest/file`.

### Manual file uploads

Run the POST `/ingest/file` endpoint for source files directly. Jira and ServiceNow files must use their dedicated adapter endpoints.

```powershell
curl.exe -X POST "http://localhost:8000/ingest/file" -F "file=@sample_data/qradar/qradar_event_001.json"
curl.exe -X POST "http://localhost:8000/ingest/file" -F "file=@sample_data/iam/iam_event_001.json"
curl.exe -X POST "http://localhost:8000/ingest/file" -F "file=@sample_data/vendor/vendor_event_001.json"
curl.exe -X POST "http://localhost:8000/ingest/file" -F "file=@sample_data/telemetry/telemetry_event_001.json"
curl.exe -X POST "http://localhost:8000/ingest/file" -F "file=@sample_data/risk/risk_context_001.json"
```

For Jira and ServiceNow files, use the adapter endpoints:

```powershell
curl.exe -X POST "http://localhost:8000/adapters/jira/webhook" -H "Content-Type: application/json" --data-binary "@sample_data/jira/jira_webhook_001.json"
curl.exe -X POST "http://localhost:8000/adapters/servicenow/event" -H "Content-Type: application/json" --data-binary "@sample_data/servicenow/servicenow_incident_001.json"
```

## 14. Run the workers

The normalizer and correlator are one-shot jobs.

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
- the sample dataset should produce candidate incidents for all 6 services.

A quick health check after each worker run:

```powershell
docker exec -it resilience_postgres psql -U resilience -d resilience -c "SELECT id, source_system, source_type, ingest_status FROM raw_events ORDER BY created_at DESC;"
```

```powershell
docker exec -it resilience_postgres psql -U resilience -d resilience -c "SELECT event_id, source_type, event_type, linked_service, correlation_status FROM canonical_events ORDER BY created_at DESC;"
```

```powershell
docker exec -it resilience_postgres psql -U resilience -d resilience -c "SELECT incident_id, status, service_name, confidence_score, rule_hits FROM candidate_incidents ORDER BY created_at DESC;"
```

### Optional continuous loop mode

The normalizer and correlator support continuous polling for new work.

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

With `MAX_CYCLES=0`, the worker loops indefinitely. Set a positive integer to limit cycles.

## 15. Review, enrich, and manage incidents

Useful URLs:

- ingest API docs: `http://localhost:8000/docs`
- intelligence service docs: `http://localhost:8001/docs`
- control API docs: `http://localhost:8002/docs`
- review UI: `http://localhost:8002/ui`

### From the UI

For each incident, the UI provides:

- incident detail and business context,
- threshold flags (critical service, unauthorized access, vendor dependency, platform instability, multi-signal),
- AI enrichment trigger and enrichment output display,
- review action form (approve / escalate / dismiss) and review action history,
- status transition controls,
- incident pack generation and display,
- report draft generation and display with AI disclaimer banner,
- remediation action creation form, action list with status-coloured cards, and update controls,
- full audit log timeline with icon-coded event types.

### Trigger enrichment

Via the control API proxy (recommended — also auto-triggers pack assembly):

```powershell
curl.exe -X POST "http://localhost:8002/incidents/<incident_id>/enrich" -H "accept: application/json"
```

Or directly to the intelligence service:

```powershell
curl.exe -X POST "http://localhost:8001/incidents/<incident_id>/enrich" -H "accept: application/json"
```

### Generate and retrieve an incident pack

```powershell
curl.exe -X POST "http://localhost:8002/incidents/<incident_id>/pack" -H "accept: application/json"
curl.exe -X GET "http://localhost:8002/incidents/<incident_id>/pack" -H "accept: application/json"
```

### Generate and retrieve a report draft

```powershell
curl.exe -X POST "http://localhost:8002/incidents/<incident_id>/reporting/draft" -H "accept: application/json"
curl.exe -X GET "http://localhost:8002/incidents/<incident_id>/reporting/draft" -H "accept: application/json"
```

### Create a remediation action

```powershell
curl.exe -X POST "http://localhost:8002/incidents/<incident_id>/remediation" -H "Content-Type: application/json" -d "{\"title\": \"Rotate trade-admin credentials\", \"description\": \"Immediately rotate all credentials for trade-admin account\", \"owner\": \"security-team\", \"due_date\": \"2026-04-24\"}"
```

### View audit trail

```powershell
curl.exe -X GET "http://localhost:8002/incidents/<incident_id>/audit" -H "accept: application/json"
```

## 16. Verification queries

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

### Remediation actions

```powershell
docker exec -it resilience_postgres psql -U resilience -d resilience -c "SELECT id, incident_id, title, owner, status, due_date FROM remediation_actions ORDER BY created_at DESC;"
```

### Audit log

```powershell
docker exec -it resilience_postgres psql -U resilience -d resilience -c "SELECT entity_id, action_type, actor, occurred_at FROM audit_log ORDER BY occurred_at DESC LIMIT 30;"
```

## 17. Recommended demo sequence

A clean demo sequence is:

1. Start infrastructure.
2. Start the three APIs (ingest, intelligence, control).
3. Run `python scripts/seed_sample_data.py` or use the UI `Reset & seed` button.
4. Run `python scripts/run_normalizer.py`.
5. Run `python scripts/run_correlator.py`.
6. Open the review UI at `http://localhost:8002/ui`.
7. Show the incident list — multiple incidents from different services.
8. Open a high-severity incident and trigger AI enrichment.
9. Show the enrichment output, then generate an incident pack.
10. Walk through a review action and status transition.
11. Generate a report draft; show the AI disclaimer banner.
12. Create a remediation action; show the audit log timeline.
13. Show the PostgreSQL tables to demonstrate authoritative state.

## 18. Troubleshooting

### No incidents appear in the UI

Check that:

- sample files were ingested successfully (`raw_events` has rows),
- `python scripts/run_normalizer.py` completed successfully (`canonical_events` has rows),
- `python scripts/run_correlator.py` completed successfully (`candidate_incidents` has rows),
- risk context files were ingested before the correlator ran (the `MALWARE_ON_CRITICAL_SERVICE` and `PRIV_ACCESS_CRITICAL_SERVICE` rules require `critical_service = true` in service context, which is populated from risk_context payloads).

### Intelligence service fails to enrich

Check that:

- LM Studio is open,
- the local server is enabled,
- the configured model is loaded,
- `LLM_MODEL` matches the model identifier exposed by LM Studio,
- `LLM_CHAT_PATH` is set to `/v1/chat/completions`.

The service returns a deterministic fallback when LM Studio is unavailable, so the demo can continue through pack assembly, report draft, and remediation even without a working LLM.

### Incident pack or report draft not appearing in UI

Check that:

- the `artifacts` and `reports` MinIO buckets exist (run `python scripts/setup_minio.py` if needed),
- enrichment completed before the pack was requested (the pack is auto-assembled after enrichment; manual assembly via the Pack button also works).

### MinIO errors during ingest

Check that:

- Docker containers are running,
- `scripts/setup_minio.py` completed successfully,
- MinIO is reachable at `http://localhost:9000`,
- expected buckets exist: `raw-events`, `artifacts`, `prompts`, `reports`.

MinIO console: `http://localhost:9001` — credentials: `minio` / `minio12345`.

### Normalizer skips events

If the normalizer prints `skipping raw event ... (source_type=unknown)`, an event was ingested with an unrecognised source type. This typically means a Jira or ServiceNow file was uploaded via `/ingest/file` instead of the dedicated adapter endpoints. Re-run `python scripts/seed_sample_data.py` — it routes jira/servicenow files to the correct endpoints automatically.

### Database schema problems

If the schema is missing or stale, rebuild the environment:

```powershell
.\scripts\bootstrap.ps1 -RebuildDb
```

## 19. Repository map

| Path | Contents |
|---|---|
| `app/common/` | config, DB engine, repository helpers, schemas, MinIO client, LLM helpers |
| `app/ingest_api/` | ingest API with file upload and adapter endpoints |
| `app/normalizer_worker/` | raw-to-canonical normalization with per-event error handling |
| `app/correlator_worker/` | rule-based correlation, threshold flag computation, audit emission |
| `app/intelligence_service/` | AI enrichment service with prompt assembly and fallback |
| `app/control_api/main.py` | control API, review workflow, status transitions, UI |
| `app/control_api/incident_pack.py` | incident pack assembler (MinIO artifact) |
| `app/control_api/reporting.py` | report draft assembler (MinIO artifact) with disclaimers |
| `db/init.sql` | PostgreSQL schema, indexes, triggers, seed data |
| `rules/correlation_rules.yaml` | 14 correlation rules |
| `reference/` | 4 reference files covering 6 services (prompt retrieval context) |
| `sample_data/` | 50 demo payloads across 7 source types |
| `scripts/seed_sample_data.py` | seeds all sample data, routing jira/servicenow to adapters |
| `scripts/run_normalizer.py` | normalizer entry point |
| `scripts/run_correlator.py` | correlator entry point |
| `scripts/bootstrap.ps1` | full environment bootstrap |
| `.run/` | committed PyCharm run configurations for the three APIs |

## 20. Current limits and future direction

This repository is intentionally minimal. It is optimized for local inspection and demo flow rather than production hardening.

Notable current limits:

- no time-window awareness in the correlator — events are grouped from the current unprocessed batch only, not across a sliding time window (Rec 6.6 from the gap analysis, not yet implemented),
- no scheduler or queue for the workers; loop mode is available but not supervised,
- local-file reference retrieval rather than a vector retrieval service,
- Jira and ServiceNow payloads submitted via `/ingest/file` will be skipped by the normalizer (they must use the dedicated adapter endpoints),
- mixed LM Studio configuration paths that should be standardized,
- no prompt masking or PII scrubbing before LLM submission.

Even with those constraints, the repository demonstrates the complete resilience workflow: ingest evidence, normalize, correlate deterministically, enrich with bounded AI, review with human authority, track remediation, assemble incident packs, draft regulatory reports, and maintain a full audit trail.
