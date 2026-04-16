

1. Because the schema changed, recreate the database volume before testing:
docker compose down -v
docker compose up -d

Also reset Minio by running scripts/setup_minio.py to create the buckets etc.

In PyCharm -- 
start:
Ingest API
Control API
Intelligence Service
run:
scripts/seed_sample_data.py
scripts/run_normalizer.py
scripts/run_correlator.py

Then Verify: 
docker exec -it resilience_postgres psql -U resilience -d resilience -c "SELECT id, source_system, source_type, ingest_status FROM raw_events ORDER BY created_at DESC;"
docker exec -it resilience_postgres psql -U resilience -d resilience -c "SELECT event_id, source_type, event_type, linked_service, correlation_status FROM canonical_events ORDER BY created_at DESC;"
docker exec -it resilience_postgres psql -U resilience -d resilience -c "SELECT incident_id, status, service_name, confidence_score, rule_hits FROM candidate_incidents ORDER BY created_at DESC;"


How to run:

The normalizer and correlator are written as one-shot jobs, not long-running workers. They do one pass, print the count, and exit.

You can see it from the runner scripts:

run_normalizer.py calls run_once()
run_correlator.py calls run_once()

So if there is no raw data yet, they will exit immediately with 0.


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

Then run:

run_normalizer.py
run_correlator.py
Easiest way to ingest the sample data

Open:

http://localhost:8000/docs

Use POST /ingest/file and upload these sample files one by one:

sample_data/qradar/qradar_event_001.json
sample_data/iam/iam_event_001.json
sample_data/vendor/vendor_event_001.json
sample_data/telemetry/telemetry_event_001.json
sample_data/risk/risk_context_001.json

After that:

run run_normalizer.py
then run run_correlator.py

At that point they should actually do work.

What you should expect

After ingesting the five files:

normalizer should process raw events into canonical events
correlator should group them by service
it should create at least one candidate incident for portfolio-api


How to verify quickly
Check raw events in PostgreSQL

SELECT id, source_system, source_type, ingest_status, received_at
FROM raw_events
ORDER BY received_at DESC;

SELECT event_id, source_type, event_type, linked_service, correlation_status
FROM canonical_events
ORDER BY created_at DESC;

SELECT incident_id, status, service_name, confidence_score, rule_hits
FROM candidate_incidents
ORDER BY created_at DESC;

If you want command-line ingestion instead of Swagger

From the project root in PowerShell:
curl.exe -X POST "http://localhost:8000/ingest/file" -F "file=@sample_data/qradar/qradar_event_001.json"
curl.exe -X POST "http://localhost:8000/ingest/file" -F "file=@sample_data/iam/iam_event_001.json"
curl.exe -X POST "http://localhost:8000/ingest/file" -F "file=@sample_data/vendor/vendor_event_001.json"
curl.exe -X POST "http://localhost:8000/ingest/file" -F "file=@sample_data/telemetry/telemetry_event_001.json"
curl.exe -X POST "http://localhost:8000/ingest/file" -F "file=@sample_data/risk/risk_context_001.json"

Then run the worker scripts again.


--------
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

Open:

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

7. One more useful addition

A good next improvement is a single Python seed script that loads:

reference/services.json
reference/risk_context.json
sample_data/*.json

into PostgreSQL and MinIO automatically so the first demo run is immediate.