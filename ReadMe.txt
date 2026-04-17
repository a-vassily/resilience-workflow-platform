
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
automatically:
Open http://localhost:8002/ui

restart the Control API, Intelligence Service, Normalizer, and Correlator
open http://localhost:8002/ui
use:
Reset & seed
Reset, seed & enrich
Run AI enrichment

Optional loop mode:

normalizer:
NORMALIZER_LOOP=true
NORMALIZER_POLL_SECONDS=5
NORMALIZER_MAX_CYCLES=0
correlator:
CORRELATOR_LOOP=true
CORRELATOR_POLL_SECONDS=5
CORRELATOR_MAX_CYCLES=0

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

V2 IMPROVEMENT
expanded the scenarios into several service-specific incident patterns and updated the correlator so the richer rule file is actually meaningful, rather than just longer YAML.

What changed:

added a broader, more realistic sample set across multiple services
expanded correlation_rules.yaml from 3 basic rules to 9 richer rules
upgraded the normalizer so it recognizes more event types from the sample payloads
upgraded the correlator so it can evaluate more expressive rule conditions and keep multiple matched rule hits on one incident

New scenarios now included:

privileged access anomaly on portfolio-api
vendor outage + synthetic failure on client-reporting
data exfiltration + impossible-travel identity anomaly on client-reporting
storage saturation + backup failure on client-reporting
platform instability + restart storm on nav-batch
batch degradation + queue backlog on nav-batch
break-glass / suspicious admin activity on iam-core

Rule engine improvements:

supports event_type_all
supports event_type_any
supports event_type_count_at_least
supports source_type_any
supports min_distinct_sources
supports severity_at_least
keeps all matched rules instead of stopping at the first match

Important practical note:
because the new sample set is much richer, client-reporting and nav-batch can now generate incidents with multiple rule_hits, which is more realistic for a compound incident pattern.



Start with the operating model message: the platform ingests heterogeneous signals, normalizes them, correlates weak signals into candidate incidents, and only then invokes AI for bounded analytical support. Deterministic control remains in the platform; AI does not classify or decide.

Demo flow
1. Reset and seed

Use the UI or reset/seed endpoint so the audience sees a fresh run.

What to say:
“We start from an empty state, load a realistic mix of operational, security, vendor, and risk-context signals, and replay the same process every time.”

2. Show raw ingestion

Open the review UI or DB view and show that multiple source systems are present:

SIEM / security
IAM / PAM
telemetry / synthetic monitoring
vendor health
risk context

What to say:
“The system is not looking at one alert source. It is fusing signals from several operational domains.”

3. Show normalization

Show canonical events.

What to say:
“Each source-specific record is converted into a common canonical event model. That is what makes downstream correlation and evidence handling consistent.”

Recommended incident scenarios to present
Scenario A — Privileged access anomaly on a critical service

Service: portfolio-api

Signals:

failed privileged access
repeated failed privileged access
critical service context

Expected rule hits:

PRIV_ACCESS_CRITICAL_SERVICE

What to say:
“This is the simplest but important case: the platform identifies a privileged-access anomaly affecting a critical service. The point is not just detecting a login problem; it is recognizing that this affects a business-critical service and should enter an incident workflow.”

Then trigger enrichment.

What to highlight in the AI output:

short incident summary
plausible hypotheses
explicit uncertainty
review memo

Key message:
“AI is helping package the case, not deciding whether it is reportable.”

Scenario B — Vendor degradation affecting business service availability

Service: client-reporting

Signals:

vendor degradation or outage
synthetic failure
service error or latency degradation

Expected rule hits:

VENDOR_DEGRADATION_WITH_SERVICE_ERRORS
possibly a broader service degradation rule as well

What to say:
“This is more realistic operationally. No single alert is enough. A vendor issue alone may be noise; a synthetic failure alone may be local; an application error rate alone may be ambiguous.
 Together they form a credible candidate incident.”

What to highlight:

multi-source fusion
service-level context
vendor relationship visible in the incident
richer rule hits than a single alert-based system

Key message:
“The platform is useful because it identifies combinations of weak evidence.”

Scenario C — Cyber plus operational degradation

Service: client-reporting

Signals:

suspicious identity signal or exfiltration-style event
operational degradation on the same service

Expected rule hits:

cyber + operational compound rule

What to say:
“This is where the architecture becomes more valuable.
The platform can recognize that a cyber signal and a service degradation signal are not independent.
It creates a single candidate incident that is more meaningful than two disconnected alerts.”

What to highlight:

multiple rule hits on one incident
same-service correlation
improved incident packaging for review teams

Key message:
“The value is not just detection speed; it is better incident framing.”

Scenario D — Batch/platform instability

Service: nav-batch

Signals:

restart storm / platform instability
queue backlog
batch delay or batch failure

Expected rule hits:

platform instability / restart storm
batch degradation / queue backlog

What to say:
“This shows the platform is not limited to security-style incidents. It can also correlate platform instability and workload degradation in a non-interactive service.”

What to highlight:

another service type
non-cyber operational case
multiple signals tied to service context

Key message:
“The design is cross-domain, not SIEM-centric.”

Suggested presentation order

Use this order:

reset and seed
show raw events
show canonical events
show 3 candidate incidents
open portfolio-api
trigger enrichment
show AI output
open client-reporting
show multi-rule incident
open nav-batch
show non-cyber operational incident

This gives a strong progression:

simple case
compound operational/vendor case
richer cross-domain case
What to emphasize throughout

Repeat these points:

“The platform produces candidate incidents, not automatic final incidents.”
“Deterministic rules create the control object.”
“AI is invoked after correlation, not before.”
“The AI output is advisory and bounded.”
“The authoritative state remains in PostgreSQL.”
“The architecture supports operational resilience, auditability, and replay.”

Best demo queries to keep ready
Show incidents
SELECT incident_id, status, service_name, confidence_score, rule_hits
FROM candidate_incidents
ORDER BY created_at DESC;
Show linked events for one incident
SELECT l.incident_id, l.event_id, c.source_type, c.event_type, c.linked_service, c.event_timestamp
FROM incident_event_links l
JOIN canonical_events c ON l.event_id = c.event_id
WHERE l.incident_id = 'YOUR_INCIDENT_ID'
ORDER BY c.event_timestamp;
Show enrichment requests
SELECT request_id, incident_id, route_used, model_id, requested_at
FROM ai_enrichment_requests
ORDER BY requested_at DESC;
Show enrichment responses
SELECT request_id, schema_valid, latency_ms, created_at
FROM ai_enrichment_responses
ORDER BY created_at DESC;
A good closing line

“This prototype is not trying to automate incident authority. It is showing how to combine deterministic control, structured evidence, and bounded AI assistance into a resilience-oriented operating model.”




-----------------------
In a real environment, you do not hardcode hundreds or thousands of source-specific event classifications inside one giant normalize_raw_event() function. That works for a demo, but it does not scale operationally or organizationally.

The real pattern is to separate three things:

transport and shape normalization
event taxonomy mapping
correlation and detection logic

That separation is the key.

What the normalizer should do in a real system

The normalizer should be thin. Its job is mainly to:

parse the source payload
validate required fields
map obvious source fields into a common envelope
attach source metadata
map the event into a canonical taxonomy
persist both the canonical form and the original raw payload

In other words, the normalizer should not contain all enterprise detection logic. It should mostly turn source-native records into a standard event vocabulary.

That is exactly the kind of problem standards such as Elastic Common Schema (ECS) and OpenTelemetry semantic conventions were created to solve: common names and categories for event/log/telemetry data across tools and platforms. ECS explicitly uses categorization fields such as event.kind, event.category, event.type, and event.outcome to classify “what the event is,” independent of which source produced it. OpenTelemetry semantic conventions similarly define common names and meanings for telemetry attributes across logs, traces, and metrics.

What this means practically

Instead of hardcoding:

QRadar failed login → failed_privileged_access
CyberArk burst → repeated_failed_privileged_access
Grafana alert X → service_error_rate_high

directly in Python branches forever, you usually move the source-to-canonical mapping into mapping tables or adapter configs.

A more realistic structure is:

Layer 1 — source adapters

Each adapter knows how to parse one family of inputs:

QRadar
Microsoft Sentinel
PAM/IAM logs
Prometheus/Grafana alerts
cloud audit logs
vendor status feeds
ServiceNow/Jira webhooks
Layer 2 — canonical event taxonomy

Every parsed event is mapped into a standard internal vocabulary such as:

authentication.failure
privileged_access.failure_burst
service.availability.degraded
dependency.vendor.outage
backup.job.failed
endpoint.data_exfiltration_suspected

That internal vocabulary should be stable and much smaller than the raw source universe.

Layer 3 — correlation rules

Correlation rules should operate on the canonical taxonomy, not on raw vendor payloads.

That is the scalable design.