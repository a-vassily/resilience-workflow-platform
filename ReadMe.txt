

1. Because the schema changed, recreate the database volume before testing:
docker compose down -v
docker compose up -d


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


