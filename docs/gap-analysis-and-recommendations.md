# Gap Analysis and Recommendations

## 1. Purpose of this document

The white paper describes a target IT Risk Monitoring and Resilience Platform for a Luxembourg-regulated financial firm. This repository implements a local demo prototype intended to showcase the feasibility of that design. This document maps what has been implemented and demonstrated, what has been partially implemented, and what has not been implemented, and then provides concrete recommendations for how to evolve the existing codebase to better express the paper's concepts.

---

## 2. Summary assessment

| Area | Status |
|---|---|
| Multi-source ingestion | Implemented |
| Canonical event normalization | Implemented |
| Rule-based correlation and candidate-incident creation | Implemented |
| Bounded AI enrichment with governed prompt construction | Implemented |
| AI request and response persistence with integrity tracking | Implemented |
| Human review workflow (basic) | Implemented |
| Deterministic control principle (AI assists, rules decide) | Implemented |
| Incident lifecycle with full status set | Partial |
| Remediation tracking | Schema only |
| Event backbone (Kafka/Flink) | Not implemented (by design for demo) |
| Elasticsearch for search and retrieval | Not implemented (by design for demo) |
| Kubernetes deployment with namespace segregation | Not implemented (by design for demo) |

---

## 3. What has been implemented and demonstrated

### 3.1 Multi-source ingestion

The ingest API (`app/ingest_api/main.py`) accepts events from five source families that match the paper's day-one scope: security events (QRadar), identity and privileged-access events (IAM/CyberArk), vendor-health events, telemetry alerts (Grafana/Prometheus), and structured risk-context updates. Two adapter-style endpoints handle inbound Jira and ServiceNow payloads.

Every inbound payload is stored as a raw binary object in MinIO (`raw-events` bucket) before anything else happens. The evidence pointer is written into PostgreSQL alongside the raw event metadata. This implements the paper's "evidence first" principle at the ingestion boundary.

### 3.2 Canonical event normalization

The normalizer worker (`app/normalizer_worker/main.py`) maps each source-specific payload into a canonical event structure whose fields — `event_id`, `source_system`, `source_type`, `event_type`, `severity`, `affected_asset`, `linked_service`, `vendor_reference`, `evidence_pointer`, `enrichment_tags`, `ingesting_adapter` — match the paper's canonical event model exactly.

The normalizer covers multiple fine-grained `event_type` values within each source family: for example, security events are mapped to `failed_privileged_access`, `data_exfiltration_alert`, `break_glass_account_used`, `suspicious_admin_session`, and `malware_detected`. Identity events are similarly differentiated by `event_type` from the raw payload before normalization. This layer removes adapter-specific logic from downstream correlation, as the paper specifies.

Risk-context payloads also drive a service-context upsert, so service criticality, ownership, and dependencies are always current by the time the correlator runs.

### 3.3 Rule-based correlation and candidate-incident creation

The correlator (`app/correlator_worker/main.py`) reads canonical events with `correlation_status = 'new'`, groups them by linked service or vendor reference, and evaluates a YAML rule pack (`rules/correlation_rules.yaml`). The rule pack covers all six patterns the paper calls the disciplined day-one set:

- Privileged-access anomaly on a critical service (`PRIV_ACCESS_CRITICAL_SERVICE`)
- Vendor degradation affecting an internal service (`VENDOR_DEGRADATION_WITH_SERVICE_ERRORS`, `VENDOR_OUTAGE_WITH_SYNTHETIC_FAILURE`)
- Platform instability on a critical workload (`PLATFORM_INSTABILITY_CRITICAL_WORKLOAD`)
- Cyber signal plus operational degradation (`EXFILTRATION_WITH_IDENTITY_ANOMALY`, `BREAK_GLASS_ADMIN_ACTIVITY`)
- Repeated weak-signal burst (`REPEATED_WEAK_SIGNAL_BURST`)
- Batch processing degradation (`BATCH_PROCESSING_DEGRADATION`)
- Storage capacity and backup failure (`STORAGE_CAPACITY_AND_BACKUP_FAILURE`)

The correlator outputs candidate incidents, not authoritative incidents, and emits rule hits, confidence score, threshold flags, and contributing event references into the candidate incident record. The candidate incident schema in PostgreSQL (`candidate_incidents`) closely mirrors the authoritative incident object described in section 8.2 of the paper, including `workflow_state`, `business_context`, `classification_support`, `threshold_flags`, `draft_severity`, `review_due_at`, `initial_report_due_at`, and artifact reference columns.

### 3.4 Bounded AI enrichment with governed prompt construction

The intelligence service (`app/intelligence_service/main.py`) implements the deterministic intelligence orchestration sequence described in section 10 of the paper:

1. Reads the candidate incident from PostgreSQL.
2. Retrieves prior incidents, runbooks, and risk context from local reference files keyed by service name.
3. Assembles a bounded prompt package with all six fixed sections: system instruction, task instruction, incident context, retrieved evidence, constraints, and output schema.
4. Sends to LM Studio via the OpenAI-compatible chat completions endpoint.
5. Validates the response against a required-key schema.
6. Persists request metadata (model ID, template version, retrieval refs, prompt hash, route) and response metadata (body, schema validity, latency, token metadata).
7. Returns a deterministic fallback payload if the model route is unavailable.

The system instruction block explicitly prohibits final regulatory classification, report submission, and fact invention. The constraints block in the prompt package repeats these prohibitions in structured form. This directly implements the paper's rule that AI must not autonomously classify, submit, or suppress.

### 3.5 Prompt integrity tracking

The intelligence service computes a SHA-256 hash of the outbound prompt package and stores it in `ai_enrichment_requests.outbound_prompt_hash`. The full prompt body is also stored in `ai_enrichment_requests.outbound_prompt`. This supports the paper's requirement for prompt integrity tracking and reproducibility.

### 3.6 Human review workflow

The control API (`app/control_api/main.py`) exposes endpoints for listing incidents, retrieving incident detail, recording review actions, and updating incident status. The review UI supports the same lifecycle status values the paper describes: `candidate`, `triage_pending`, `under_review`, `classified_internal`, `classified_reportable`, `remediation_open`, and `closed`. Review actions are recorded in the `review_actions` table with actor, action type, notes, and payload. AI enrichment history is visible alongside review actions in the incident detail view.

### 3.7 Storage model alignment

The PostgreSQL schema in `db/init.sql` implements all tables mentioned in the paper's architecture:
- `raw_events`, `canonical_events`, `service_context`, `risk_context_refs`
- `candidate_incidents`, `incident_event_links`, `incident_artifacts`
- `ai_enrichment_requests`, `ai_enrichment_responses`
- `review_actions`, `remediation_actions`, `audit_log`

MinIO buckets `raw-events`, `artifacts`, `prompts`, and `reports` are provisioned, matching the paper's object storage model. `updated_at` triggers are in place for key tables.

---

## 4. What has been partially implemented

### 4.1 Incident lifecycle

The full lifecycle in the paper includes `reported_initial`, `reported_intermediate`, and `reported_final` statuses between `classified_reportable` and `closed`. These appear in the UI status dropdown but have no backend logic associated with them. There is no reporting lane, no report package assembly, and no approval gate before status can advance to a reporting state.

### 4.2 Retrieval context for AI enrichment

The paper describes four families of retrieval context: technical architecture and runtime context, business and service criticality context, operational and prior-incident context, and control and reporting templates. The demo covers operational context (prior incidents, runbooks, risk context) but does not retrieve architecture descriptions, escalation templates, or reporting templates. The reference files are also minimal: one runbook entry for `portfolio-api`, two prior incident references, and one risk context record.

### 4.3 Correlation time window

The correlator groups all canonical events with `correlation_status = 'new'` in a single batch. The paper specifies that correlation should combine time-window joins alongside event-type matching. The current implementation produces correct candidate incidents from the sample data but would not correctly handle high-velocity scenarios where events from different time windows should not be grouped together.

### 4.4 ITSM adapters

Inbound adapters for Jira and ServiceNow exist in the ingest API. The paper specifies that the platform should also synchronize incident state and remediation actions outbound to these systems through adapters, maintaining ITSM neutrality. No outbound synchronization is implemented.

---

### 5. Infrastructure-level concerns (acknowledged as out of scope for demo)

The following items are referenced in the paper but are correctly deferred for a demo prototype:
- Kafka and Flink event backbone (replaced by batch workers)
- Elasticsearch for incident search and retrieval (replaced by PostgreSQL)
- Temporal for durable workflow and timer state
- Kubernetes with namespace segregation
- Centralized secret management and rotation
- Immutable or append-controlled evidence bundles
- Service-to-service authentication and encrypted internal traffic

---



## 6. What would take the demo beyond feasibility showcase to prototype

The following additions would move the codebase from a feasibility showcase to a governed prototype that could support a real business review:

- A Temporal-backed workflow engine replacing the current status-field approach, providing durable state, timer enforcement, and explicit approval gates for each lifecycle transition.
- An outbound Jira or ServiceNow adapter that pushes incident state and remediation actions to an enterprise workflow tool, demonstrating ITSM neutrality.
- A schema validation step at ingestion that rejects or quarantines malformed payloads rather than inferring source types heuristically.
- A read-only audit log UI section showing all state transitions for a given incident from creation to closure.
- A basic role model in the control API distinguishing between incident reviewers who can record notes and approvers who can advance reporting-lane statuses.

These are not required for the current demo goal but would be necessary if the prototype were presented to a technology or risk committee as the basis for a production investment decision.
