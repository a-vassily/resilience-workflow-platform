# Resilience Platform Starter Codebase

This starter project implements a local prototype of the IT risk monitoring and resilience platform described in your design document. Python services are intended to run from **PyCharm**. **Docker Desktop** hosts only the infrastructure and model services: PostgreSQL, MinIO, and Ollama.

## What is included

- Docker Compose for infrastructure only
- SQL schema for canonical events, candidate incidents, AI enrichment, and review actions
- FastAPI ingest API with adapter stubs for Jira and ServiceNow style payloads
- FastAPI control API with a lightweight browser review UI
- Normalizer worker
- Correlator worker
- Intelligence service
- Sample source-event JSON files
- Project-stored PyCharm run configurations under `.run/`
- Reference context files
- Initial correlation rules

## Architecture cut-down

Infrastructure in Docker:
- PostgreSQL
- MinIO
- Ollama

Run from PyCharm:
- `app.ingest_api.main`
- `app.control_api.main`
- `app.normalizer_worker.main`
- `app.correlator_worker.main`
- `app.intelligence_service.main`

## 1. Setup

### Prerequisites
- Windows with WSL2 enabled
- Docker Desktop
- Python 3.11
- PyCharm Professional or Community

### Start infrastructure
```bash
copy .env.example .env
docker compose up -d
```

Optional: pull a small model into Ollama.
```bash
docker exec -it resilience_ollama ollama pull qwen2.5:7b-instruct
```

### Create Python environment
```bash
python -m venv .venv
.venv\Scriptsctivate
pip install -r requirements.txt
```

## 2. PyCharm project setup

Open the repository root in PyCharm.

### Interpreter
Set the project interpreter to `.venv`.

### Environment variables
Load variables from `.env` or configure them in each run configuration.

Minimum set:
- `DATABASE_URL`
- `MINIO_ENDPOINT`
- `MINIO_ACCESS_KEY`
- `MINIO_SECRET_KEY`
- `MINIO_SECURE`
- `OLLAMA_BASE_URL`
- `OLLAMA_MODEL`
- `INTELLIGENCE_SERVICE_URL`

### Run configurations
The project already contains project-stored run configurations in `.run/`.
If PyCharm does not immediately pick them up, open one once and save it as a project file.

Included run targets:
- Ingest API
- Control API
- Intelligence Service
- Normalizer Worker
- Correlator Worker

## 3. First run

1. Start Docker infrastructure.
2. Start `Ingest API` from PyCharm.
3. Start `Control API` from PyCharm.
4. Start `Intelligence Service` from PyCharm.
5. Run `Normalizer Worker` once.
6. Run `Correlator Worker` once.

### Ingest sample files
Use Swagger at:
- http://localhost:8000/docs
- http://localhost:8002/docs
- http://localhost:8001/docs
- Review UI: http://localhost:8002/ui

Examples using PowerShell:
```powershell
Invoke-RestMethod -Uri http://localhost:8000/ingest/file -Method Post -InFile sample_data/qradar/qradar_event_001.json -ContentType 'application/json'
Invoke-RestMethod -Uri http://localhost:8000/ingest/file -Method Post -InFile sample_data/iam/iam_event_001.json -ContentType 'application/json'
Invoke-RestMethod -Uri http://localhost:8000/ingest/file -Method Post -InFile sample_data/vendor/vendor_event_001.json -ContentType 'application/json'
Invoke-RestMethod -Uri http://localhost:8000/ingest/file -Method Post -InFile sample_data/telemetry/telemetry_event_001.json -ContentType 'application/json'
Invoke-RestMethod -Uri http://localhost:8000/ingest/file -Method Post -InFile sample_data/risk/risk_context_001.json -ContentType 'application/json'
```

### Optional adapter stub tests
```powershell
Invoke-RestMethod -Uri http://localhost:8000/adapters/jira/webhook -Method Post -InFile sample_data/jira/jira_webhook_001.json -ContentType 'application/json'
Invoke-RestMethod -Uri http://localhost:8000/adapters/servicenow/event -Method Post -InFile sample_data/servicenow/servicenow_incident_001.json -ContentType 'application/json'
```

## 4. End-to-end test flow

1. POST sample source events to `/ingest/file` or use the adapter endpoints.
2. Run the normalizer.
3. Run the correlator.
4. Open `http://localhost:8002/ui`.
5. Trigger AI enrichment from the UI or `POST /incidents/{incident_id}/enrich`.
6. Record review actions and move the incident through local status changes.

## 5. Notes

- This project keeps **authoritative incident state** in PostgreSQL.
- AI outputs are advisory only and stored separately.
- Large evidence artifacts are stored in MinIO and linked by reference.
- The workers are intentionally simple and synchronous for prototype clarity.
- The UI is intentionally lightweight and meant only for operator demonstration.

## 6. Next likely extensions

- add a proper report-pack generator
- add a neutral remediation action service
- add background scheduling or a queue
- replace local stubs with real Jira or ServiceNow integrations
- add Elasticsearch for evidence search
