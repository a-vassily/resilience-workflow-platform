
How to setup initially

create the database volume before testing:
docker compose down -v
docker compose up -d
From the project root in PowerShell:

.\scripts\bootstrap.ps1

If you want a full rebuild of the PostgreSQL volume:

.\scripts\bootstrap.ps1 -RebuildDb
6. Small but important practical note

For LM Studio to pass the test:

LM Studio must be open
the local server must be enabled
the selected model must be loaded
the model name must match LLM_MODEL

If LM Studio exposes a slightly different model identifier, update the .env value.

How to check things are ok

(resilience-starter-codebase) PS C:\Users\v_ant\PycharmProjects\resilience_starter_codebase> docker compose ps
NAME                  IMAGE                COMMAND                  SERVICE    CREATED          STATUS          PORTS
resilience_minio      minio/minio:latest   "/usr/bin/docker-ent…"   minio      12 minutes ago   Up 12 minutes   0.0.0.0:9000-9001->9000-9001/tcp, [::]:9000-9001->9000-9001/tcp
resilience_postgres   postgres:16          "docker-entrypoint.s…"   postgres   12 minutes ago   Up 12 minutes   0.0.0.0:5432->5432/tcp, [::]:5432->5432/tcp

(resilience-starter-codebase) PS C:\Users\v_ant\PycharmProjects\resilience_starter_codebase> docker exec -it resilience_postgres psql -U resilience -d resilience -c "\dt"
                   List of relations
 Schema |          Name           | Type  |   Owner
--------+-------------------------+-------+------------
 public | ai_enrichment_requests  | table | resilience
 public | ai_enrichment_responses | table | resilience
 public | audit_log               | table | resilience
 public | candidate_incidents     | table | resilience
 public | canonical_events        | table | resilience
 public | incident_artifacts      | table | resilience
 public | incident_event_links    | table | resilience
 public | raw_events              | table | resilience
 public | remediation_actions     | table | resilience
 public | review_actions          | table | resilience
 public | risk_context_refs       | table | resilience
 public | service_context         | table | resilience
(12 rows)


How to verify MinIO
Open
MinIO API: http://localhost:9000
MinIO Console: http://localhost:9001

Log in with:
user: minio
password: minio12345

You should see these buckets:

raw-events
artifacts
prompts
reports

Recommended execution order




HOW TO RUN THE DEMO




The normalizer and correlator are written as one-shot jobs, not long-running workers. They do one pass, print the count, and exit.

-----------------------------------------------------------------------
HOW TO RUN:
You need to:

start the APIs
ingest the sample JSON files into the ingest API
run the normalizer
run the correlator
inspect the results in the control API or the database
Correct order

Start these first in PyCharm:

Ingest API
Control API
Intelligence Service

Then load the sample files into the ingest API.
From the project root in PowerShell:
curl.exe -X POST "http://localhost:8000/ingest/file" -F "file=@sample_data/qradar/qradar_event_001.json"
curl.exe -X POST "http://localhost:8000/ingest/file" -F "file=@sample_data/iam/iam_event_001.json"
curl.exe -X POST "http://localhost:8000/ingest/file" -F "file=@sample_data/vendor/vendor_event_001.json"
curl.exe -X POST "http://localhost:8000/ingest/file" -F "file=@sample_data/telemetry/telemetry_event_001.json"
curl.exe -X POST "http://localhost:8000/ingest/file" -F "file=@sample_data/risk/risk_context_001.json"
Then run:

run_normalizer.py
run_correlator.py
Easiest way to ingest the sample data




Or else Open: http://localhost:8000/docs
Use POST /ingest/file and upload these sample files one by one:

sample_data/qradar/qradar_event_001.json
sample_data/iam/iam_event_001.json
sample_data/vendor/vendor_event_001.json
sample_data/telemetry/telemetry_event_001.json
sample_data/risk/risk_context_001.json

After that:
run run_normalizer.py
then run run_correlator.py

What you should expect

After ingesting the five files:

normalizer should process raw events into canonical events
correlator should group them by service
it should create at least one candidate incident for portfolio-api


How to verify quickly
Check raw events in PostgreSQL
docker exec -it resilience_postgres psql -U resilience -d resilience -c "SELECT id, source_system, source_type, ingest_status FROM raw_events ORDER BY created_at DESC;"
docker exec -it resilience_postgres psql -U resilience -d resilience -c "SELECT event_id, source_type, event_type, linked_service, correlation_status FROM canonical_events ORDER BY created_at DESC;"
docker exec -it resilience_postgres psql -U resilience -d resilience -c "SELECT incident_id, status, service_name, confidence_score, rule_hits FROM candidate_incidents ORDER BY created_at DESC;"


Now we are at this stage:

sample events ingested
normalized
correlated
candidate incident created in PostgreSQL

The next step is to trigger the intelligence/enrichment step, which is the point where the code calls LM Studio.

docker exec -it resilience_postgres psql -U resilience -d resilience -c "SELECT request_id, incident_id, route_used, model_id, requested_at FROM ai_enrichment_requests ORDER BY requested_at DESC;"
docker exec -it resilience_postgres psql -U resilience -d resilience -c "SELECT request_id, schema_valid, latency_ms, created_at FROM ai_enrichment_responses ORDER BY created_at DESC;"
--------

curl.exe -X 'POST' 'http://localhost:8001/incidents/inc-36ca19a1-5660-4c24-8ece-c0cdb467220d/enrich' -H 'accept: application/json'

docker exec -it resilience_postgres psql -U resilience -d resilience -c "SELECT request_id, incident_id, route_used, model_id, requested_at FROM ai_enrichment_requests ORDER BY requested_at DESC;"

docker exec -it resilience_postgres psql -U resilience -d resilience -c "SELECT request_id, schema_valid, latency_ms, created_at FROM ai_enrichment_responses ORDER BY created_at DESC;"

You can now show the full flow in this order:

Raw events
SELECT id, source_system, source_type, ingest_status, created_at
FROM raw_events
ORDER BY created_at DESC;

Canonical events
SELECT event_id, source_type, event_type, linked_service, correlation_status
FROM canonical_events
ORDER BY created_at DESC;

Candidate incident
SELECT incident_id, status, service_name, confidence_score, rule_hits
FROM candidate_incidents
ORDER BY created_at DESC;

AI enrichment request
SELECT request_id, incident_id, route_used, model_id, requested_at
FROM ai_enrichment_requests
ORDER BY requested_at DESC;
AI enrichment response

SELECT request_id, schema_valid, latency_ms, created_at
FROM ai_enrichment_responses
ORDER BY created_at DESC;
Best demo narrative

A clean way to present it is:

simulated multi-source operational signals are ingested
the platform normalizes them into a canonical event model
deterministic correlation creates a candidate incident
only then the intelligence layer is invoked
the LLM produces bounded advisory output
authoritative state remains in PostgreSQL
AI assists, but does not decide

That is exactly aligned with the design principle in your document.