# Gap Analysis and Recommendations
## Demo Implementation vs. Reference White Paper

**Reference document:** `docs/it_risk_resilience_LinkedIn.docx`
**Architecture and runbook:** `docs/architecture-and-runbook.md`
**Analysis date:** 2026-04-17

---

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
| Incident pack assembly | Not implemented |
| Draft reporting support | Not implemented |
| Outbound ITSM synchronization | Not implemented |
| Approval gates and timer enforcement | Not implemented |
| Time-window joins in correlation | Not implemented |
| Event de-duplication and noise suppression | Not implemented |
| Prompt masking and redaction | Not implemented |
| Audit log population | Not implemented |
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

## 5. What has not been implemented

### 5.1 Incident pack assembly service

The paper specifies a dedicated Incident Pack service that assembles a structured dossier from the authoritative incident record, linked source events, AI enrichment outputs, dependency and ownership context, and workflow metadata. The `incident_pack_ref` column exists in the schema and `incident_artifacts` table is defined, but no service produces or stores an incident pack artifact. The control API exposes the raw incident data rather than a governed, assembled dossier.

### 5.2 Draft reporting support

The paper requires draft initial, intermediate, and final reporting packages from day one. The schema has a `reporting_pack_ref` column and a `reports` MinIO bucket, but no reporting service or reporting artifact generation is implemented. This is called out as a phase-one deliverable in section 17 of the paper.

### 5.3 Remediation tracking

The `remediation_actions` table is fully defined with the fields the paper specifies: title, description, owner, due date, dependency note, status, closure evidence reference, and lessons learned. However, no API endpoints, no UI section, and no repository functions exist for creating or managing remediation actions. Incident detail in the UI shows no remediation state.

### 5.4 Approval gates and timer enforcement

The paper specifies explicit approval points for classification, initial reporting package, intermediate and final reporting packages, and closure. Once an incident is marked reportable, timers for required next steps should activate. The `review_due_at` and `initial_report_due_at` fields are populated in candidate incidents, but there is no enforcement mechanism. Status transitions can be made by any caller without approval checks.

### 5.5 Event de-duplication and noise suppression

The schema includes a unique index on `(source_system, external_event_id)` in `raw_events`, but `external_event_id` is never populated by the ingest repository function. Re-ingesting the same sample files would create duplicate raw events and, after re-running the workers, duplicate canonical events and potentially duplicate candidate incidents. No suppression logic is present in the correlator for repetitive noise patterns.

### 5.6 Prompt masking and redaction

The paper requires that the outbound prompt be masked or redacted where required, with a redaction manifest stored alongside the prompt. The schema includes a `redaction_manifest` column in `ai_enrichment_requests`, but it is always stored as an empty object. No masking logic is applied to prompt content.

### 5.7 Initiating service identity

The paper specifies that the initiating service identity should be persisted alongside every inference request. The `ai_enrichment_requests` schema includes an `initiating_service_identity` column, but the intelligence service always inserts `null` rather than the calling service's identity.

### 5.8 Audit log population

The `audit_log` table is defined in the schema and is part of the paper's traceability model, but no application code writes to it. Significant state transitions (incident creation, status change, enrichment, review action) are not logged to the audit table.

### 5.9 Idempotent event ingestion

The paper requires idempotent event ingestion and explicit replay handling. The unique constraint on `raw_events` is present but unused because `external_event_id` is never set. The `insert_raw_event` repository function does not attempt an upsert and would raise a database error rather than idempotently accepting a duplicate.

### 5.10 Risk context as a direct correlation signal

The normalizer correctly processes risk-context payloads and upserts service context. However, the correlator uses service context only to check the `critical_service` flag in rule conditions. The paper describes a "risk amplification from structured risk context" pattern as one of the day-one rule types. No rule uses the presence of a `risk_context_update` event as a direct correlation signal that amplifies the confidence or classification of a co-occurring incident.

### 5.11 Infrastructure-level concerns (acknowledged as out of scope for demo)

The following items are referenced in the paper but are correctly deferred for a demo prototype:
- Kafka and Flink event backbone (replaced by batch workers)
- Elasticsearch for incident search and retrieval (replaced by PostgreSQL)
- Temporal for durable workflow and timer state
- Kubernetes with namespace segregation
- Centralized secret management and rotation
- Immutable or append-controlled evidence bundles
- Service-to-service authentication and encrypted internal traffic

---

## 6. Recommendations to better express the paper's concepts

The recommendations below are ordered from highest to lowest impact on demo fidelity. Each is scoped to what can be added to the existing codebase without replacing the current architecture.

### 6.1 Add remediation tracking endpoints and UI section

**Why:** Remediation tracking is a phase-one deliverable in the paper and one of the four governed outputs. Its absence makes the demo incomplete as a resilience platform rather than a monitoring tool.

**How:** Add repository functions (`insert_remediation_action`, `list_remediation_actions`, `update_remediation_action`) operating on the existing `remediation_actions` table. Add endpoints to `app/control_api/main.py`:
- `POST /incidents/{incident_id}/remediation` — create a remediation action
- `GET /incidents/{incident_id}/remediation` — list actions for an incident
- `PATCH /incidents/{incident_id}/remediation/{remediation_id}` — update status or closure evidence

Add a remediation section to the review UI that lists open actions and allows the reviewer to mark them closed. No new infrastructure is needed.

### 6.2 Implement an incident pack assembler

**Why:** The paper's primary output for human reviewers is a decision-ready incident pack, not a raw database record. Without this, the demo shows data but not the packaged product the paper describes.

**How:** Add a function `assemble_incident_pack(incident_id)` in `app/control_api/` or as a dedicated module. It should collect: incident record, linked canonical events with timestamps, AI enrichment output, business context (service, owner, dependencies, threshold flags), workflow state (deadlines, assigned reviewer), and review actions. Serialize this as a JSON artifact, store it in the MinIO `artifacts` bucket, and write the object reference back to `candidate_incidents.incident_pack_ref`. Trigger pack assembly automatically when a reviewer requests enrichment or when status advances past `candidate`. Expose the pack reference in the incident detail API response.

### 6.3 Add a minimal reporting lane

**Why:** The paper states that the first release should produce a reviewable reporting draft. The `reports` MinIO bucket and `reporting_pack_ref` column are unused placeholders.

**How:** Add a `POST /incidents/{incident_id}/reporting/draft` endpoint to the control API. The handler should assemble a draft report JSON containing incident identity, classification support fields, AI enrichment summary, threshold flags, and open uncertainties. Store the artifact in the MinIO `reports` bucket and write the reference to `candidate_incidents.reporting_pack_ref`. Mark the incident status as `reporting_draft_available` or advance it to `classified_reportable` only after a draft exists. Add a "Generate report draft" button to the UI that calls this endpoint and shows the draft.

### 6.4 Wire up the audit log

**Why:** Traceability is a core design principle in the paper. The `audit_log` table exists but is never written to, which means the demo has no audit trail.

**How:** Add a helper `insert_audit_event(entity_type, entity_id, action_type, actor, details)` in `app/common/repository.py`. Call it from the control API at every significant transition: incident creation (in the correlator), status change, review action, enrichment trigger, and incident pack or report draft generation. This requires no schema changes and minimal code — roughly one line per state-changing action.

### 6.5 Populate `external_event_id` to enable idempotent ingestion

**Why:** The paper requires idempotent event ingestion. The uniqueness constraint is already in the schema but is bypassed because `external_event_id` is never set.

**How:** In `app/common/repository.py`, update `insert_raw_event` to accept and store `external_event_id`. In `app/ingest_api/main.py`, extract a natural event identifier from the inbound payload (for example `offense_id` for QRadar, `event_id` for IAM payloads, `vendor_event_id` for vendor events, `alert_id` for telemetry alerts, `risk_ref` for risk context). Pass this value to the repository function and handle the unique constraint violation as an idempotent no-op rather than an error.

### 6.6 Add time-window awareness to the correlator

**Why:** The paper specifies time-window joins as part of correlation. Without this, events from very different time windows can be incorrectly grouped, and the correlator cannot model "three failures within 15 minutes" patterns.

**How:** Add a `time_window_minutes` field to each rule in `correlation_rules.yaml`. In `_matches()` in `app/correlator_worker/main.py`, when the rule specifies a time window, filter the candidate group to events whose `event_timestamp` falls within that window relative to the most recent event in the group. This does not require changing the grouping logic, only the rule evaluation step. For rules without a time window, current behavior is preserved.

### 6.7 Add a risk-amplification correlation rule

**Why:** The paper explicitly names "risk amplification from structured risk context" as one of the day-one rule patterns. The current rule pack omits it.

**How:** Add a rule `RISK_CONTEXT_AMPLIFIED_SIGNAL` to `rules/correlation_rules.yaml` that matches when a `risk_context_update` event is present in the same service group alongside any security or identity event. In the correlator's `_matches()`, add a condition type `source_type_present` that checks whether an event of a specific `source_type` exists in the group. This surfaces the case where a freshly ingested risk-context record (for example a new IRM finding) combines with an in-flight security event on the same service.

### 6.8 Implement initiating service identity in enrichment requests

**Why:** The paper requires this for every inference request. The column is in the schema. The demo currently stores `null`.

**How:** In `app/intelligence_service/main.py`, set `initiating_service_identity = 'intelligence-service'` in the `insert_ai_request` call. If the call was proxied through the control API, the control API should forward a `X-Calling-Service` header and the intelligence service should read it. This is a two-line change.

### 6.9 Expand reference data to cover all demo services

**Why:** The intelligence service retrieves context by service name. Currently `runbooks.json`, `prior_incidents.json`, and `risk_context.json` have entries only for `portfolio-api`. The correlator produces incidents for `client-reporting` and `nav-batch` in the demo, but those services receive no retrieved context during enrichment.

**How:** Add entries for `client-reporting` and `nav-batch` to each reference file. For runbooks, add one paragraph-length excerpt per service describing its operational profile. For prior incidents, add one or two illustrative references per service. For risk context, add a risk ref linking each service to its criticality and dependencies. This improves the quality of the AI output for Scenarios B, C, and D described in the runbook without any code changes.

### 6.10 Add a formal prompt masking placeholder

**Why:** The paper requires masking or redaction of prompt content where required, with a manifest stored. The `redaction_manifest` column is already in the schema.

**How:** Add a `_apply_redaction(prompt_package)` function in `app/intelligence_service/main.py` that, for this demo, simply returns the prompt unchanged and a manifest of `{"redacted_fields": [], "policy": "demo-no-redaction"}`. Store this manifest in `ai_enrichment_requests.redaction_manifest`. This makes the audit trail complete and demonstrates the architecture of the redaction step even without actual masking rules.

---

## 7. What would take the demo beyond feasibility showcase to prototype

The following additions would move the codebase from a feasibility showcase to a governed prototype that could support a real business review:

- A Temporal-backed workflow engine replacing the current status-field approach, providing durable state, timer enforcement, and explicit approval gates for each lifecycle transition.
- An outbound Jira or ServiceNow adapter that pushes incident state and remediation actions to an enterprise workflow tool, demonstrating ITSM neutrality.
- A schema validation step at ingestion that rejects or quarantines malformed payloads rather than inferring source types heuristically.
- A read-only audit log UI section showing all state transitions for a given incident from creation to closure.
- A basic role model in the control API distinguishing between incident reviewers who can record notes and approvers who can advance reporting-lane statuses.

These are not required for the current demo goal but would be necessary if the prototype were presented to a technology or risk committee as the basis for a production investment decision.