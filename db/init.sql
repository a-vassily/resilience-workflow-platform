-- ============================================
-- Resilience Prototype - PostgreSQL Init Script
-- ============================================

-- Recommended extensions
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ============================================
-- 1. Raw events
-- ============================================
CREATE TABLE IF NOT EXISTS raw_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_system VARCHAR(100) NOT NULL,
    source_type VARCHAR(100) NOT NULL,
    external_event_id VARCHAR(255),
    payload JSONB NOT NULL,
    payload_hash VARCHAR(128),
    evidence_pointer TEXT,
    ingest_status VARCHAR(50) NOT NULL DEFAULT 'received',
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    normalized_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_raw_events_source_system ON raw_events(source_system);
CREATE INDEX IF NOT EXISTS idx_raw_events_source_type ON raw_events(source_type);
CREATE INDEX IF NOT EXISTS idx_raw_events_received_at ON raw_events(received_at);
CREATE INDEX IF NOT EXISTS idx_raw_events_ingest_status ON raw_events(ingest_status);

-- Optional uniqueness guard if external ids are reliable
CREATE UNIQUE INDEX IF NOT EXISTS uq_raw_events_source_external
ON raw_events(source_system, external_event_id)
WHERE external_event_id IS NOT NULL;

-- ============================================
-- 2. Canonical events
-- ============================================
CREATE TABLE IF NOT EXISTS canonical_events (
    event_id VARCHAR(255) PRIMARY KEY,
    raw_event_id UUID REFERENCES raw_events(id) ON DELETE SET NULL,
    source_system VARCHAR(100) NOT NULL,
    source_type VARCHAR(100) NOT NULL,
    event_type VARCHAR(150) NOT NULL,
    event_timestamp TIMESTAMPTZ NOT NULL,
    severity VARCHAR(50),
    affected_asset VARCHAR(255),
    linked_service VARCHAR(255),
    vendor_reference VARCHAR(255),
    evidence_pointer TEXT,
    enrichment_tags JSONB NOT NULL DEFAULT '[]'::jsonb,
    ingesting_adapter VARCHAR(150),
    normalized_payload JSONB NOT NULL,
    correlation_status VARCHAR(50) NOT NULL DEFAULT 'new',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_canonical_events_timestamp ON canonical_events(event_timestamp);
CREATE INDEX IF NOT EXISTS idx_canonical_events_service ON canonical_events(linked_service);
CREATE INDEX IF NOT EXISTS idx_canonical_events_asset ON canonical_events(affected_asset);
CREATE INDEX IF NOT EXISTS idx_canonical_events_vendor ON canonical_events(vendor_reference);
CREATE INDEX IF NOT EXISTS idx_canonical_events_status ON canonical_events(correlation_status);
CREATE INDEX IF NOT EXISTS idx_canonical_events_event_type ON canonical_events(event_type);

-- ============================================
-- 3. Reference context for services / risk / ownership
-- ============================================
CREATE TABLE IF NOT EXISTS service_context (
    service_name VARCHAR(255) PRIMARY KEY,
    critical_service BOOLEAN NOT NULL DEFAULT FALSE,
    owner VARCHAR(255),
    business_process VARCHAR(255),
    data_classification VARCHAR(100),
    regulatory_relevance JSONB NOT NULL DEFAULT '[]'::jsonb,
    rto_minutes INTEGER,
    dependencies JSONB NOT NULL DEFAULT '[]'::jsonb,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_service_context_critical ON service_context(critical_service);

CREATE TABLE IF NOT EXISTS risk_context_refs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    risk_ref VARCHAR(255) UNIQUE NOT NULL,
    service_name VARCHAR(255) REFERENCES service_context(service_name) ON DELETE CASCADE,
    risk_payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_risk_context_service ON risk_context_refs(service_name);

-- ============================================
-- 4. Candidate incidents
-- ============================================
CREATE TABLE IF NOT EXISTS candidate_incidents (
    incident_id VARCHAR(255) PRIMARY KEY,
    status VARCHAR(50) NOT NULL DEFAULT 'candidate',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    confidence_score NUMERIC(5,4),
    rule_hits JSONB NOT NULL DEFAULT '[]'::jsonb,

    service_name VARCHAR(255),
    critical_service BOOLEAN NOT NULL DEFAULT FALSE,
    owner VARCHAR(255),
    vendor_name VARCHAR(255),

    threshold_flags JSONB NOT NULL DEFAULT '{}'::jsonb,
    draft_severity VARCHAR(50),

    review_due_at TIMESTAMPTZ,
    initial_report_due_at TIMESTAMPTZ,

    incident_pack_ref TEXT,
    enrichment_ref TEXT,
    reporting_pack_ref TEXT,
    remediation_ref TEXT,

    final_classification VARCHAR(100),
    decision_maker VARCHAR(255),
    decision_notes TEXT,

    business_context JSONB NOT NULL DEFAULT '{}'::jsonb,
    classification_support JSONB NOT NULL DEFAULT '{}'::jsonb,
    workflow_state JSONB NOT NULL DEFAULT '{}'::jsonb,
    incident_payload JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_candidate_incidents_status ON candidate_incidents(status);
CREATE INDEX IF NOT EXISTS idx_candidate_incidents_service ON candidate_incidents(service_name);
CREATE INDEX IF NOT EXISTS idx_candidate_incidents_created_at ON candidate_incidents(created_at);
CREATE INDEX IF NOT EXISTS idx_candidate_incidents_review_due_at ON candidate_incidents(review_due_at);

-- ============================================
-- 5. Incident-to-event links
-- ============================================
CREATE TABLE IF NOT EXISTS incident_event_links (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    incident_id VARCHAR(255) NOT NULL REFERENCES candidate_incidents(incident_id) ON DELETE CASCADE,
    event_id VARCHAR(255) NOT NULL REFERENCES canonical_events(event_id) ON DELETE CASCADE,
    link_reason VARCHAR(255),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (incident_id, event_id)
);

CREATE INDEX IF NOT EXISTS idx_incident_event_links_incident ON incident_event_links(incident_id);
CREATE INDEX IF NOT EXISTS idx_incident_event_links_event ON incident_event_links(event_id);

-- ============================================
-- 6. Incident artifacts
-- ============================================
CREATE TABLE IF NOT EXISTS incident_artifacts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    incident_id VARCHAR(255) NOT NULL REFERENCES candidate_incidents(incident_id) ON DELETE CASCADE,
    artifact_type VARCHAR(100) NOT NULL,
    artifact_ref TEXT NOT NULL,
    artifact_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_by VARCHAR(255),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_incident_artifacts_incident ON incident_artifacts(incident_id);
CREATE INDEX IF NOT EXISTS idx_incident_artifacts_type ON incident_artifacts(artifact_type);

-- ============================================
-- 7. AI enrichment requests / responses
-- ============================================
CREATE TABLE IF NOT EXISTS ai_enrichment_requests (
    request_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    incident_id VARCHAR(255) NOT NULL REFERENCES candidate_incidents(incident_id) ON DELETE CASCADE,
    workload_type VARCHAR(100) NOT NULL DEFAULT 'incident_enrichment',
    route_used VARCHAR(100),
    model_id VARCHAR(255),
    prompt_template_version VARCHAR(100),
    retrieval_refs JSONB NOT NULL DEFAULT '[]'::jsonb,
    redaction_manifest JSONB NOT NULL DEFAULT '{}'::jsonb,
    outbound_prompt_hash VARCHAR(128),
    outbound_prompt JSONB,
    initiating_service_identity VARCHAR(255),
    requested_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ai_requests_incident ON ai_enrichment_requests(incident_id);
CREATE INDEX IF NOT EXISTS idx_ai_requests_requested_at ON ai_enrichment_requests(requested_at);

CREATE TABLE IF NOT EXISTS ai_enrichment_responses (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    request_id UUID NOT NULL REFERENCES ai_enrichment_requests(request_id) ON DELETE CASCADE,
    response_body JSONB,
    schema_valid BOOLEAN NOT NULL DEFAULT FALSE,
    validation_errors JSONB NOT NULL DEFAULT '[]'::jsonb,
    latency_ms INTEGER,
    token_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_ai_response_request ON ai_enrichment_responses(request_id);

-- ============================================
-- 8. Review actions / human decisions
-- ============================================
CREATE TABLE IF NOT EXISTS review_actions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    incident_id VARCHAR(255) NOT NULL REFERENCES candidate_incidents(incident_id) ON DELETE CASCADE,
    action_type VARCHAR(100) NOT NULL,
    actor VARCHAR(255) NOT NULL,
    action_notes TEXT,
    old_status VARCHAR(50),
    new_status VARCHAR(50),
    action_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_review_actions_incident ON review_actions(incident_id);
CREATE INDEX IF NOT EXISTS idx_review_actions_created_at ON review_actions(created_at);

-- ============================================
-- 9. Remediation actions
-- ============================================
CREATE TABLE IF NOT EXISTS remediation_actions (
    remediation_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    incident_id VARCHAR(255) NOT NULL REFERENCES candidate_incidents(incident_id) ON DELETE CASCADE,
    title VARCHAR(255) NOT NULL,
    description TEXT,
    owner VARCHAR(255),
    due_date TIMESTAMPTZ,
    status VARCHAR(50) NOT NULL DEFAULT 'open',
    dependency_note TEXT,
    closure_evidence_ref TEXT,
    lessons_learned TEXT,
    action_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_remediation_incident ON remediation_actions(incident_id);
CREATE INDEX IF NOT EXISTS idx_remediation_status ON remediation_actions(status);
CREATE INDEX IF NOT EXISTS idx_remediation_due_date ON remediation_actions(due_date);

-- ============================================
-- 10. Audit log
-- ============================================
CREATE TABLE IF NOT EXISTS audit_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_type VARCHAR(100) NOT NULL,
    entity_id VARCHAR(255) NOT NULL,
    action_type VARCHAR(100) NOT NULL,
    actor VARCHAR(255),
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_audit_log_entity ON audit_log(entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_audit_log_created_at ON audit_log(created_at);

-- ============================================
-- 11. Updated-at trigger
-- ============================================
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_canonical_events_updated_at ON canonical_events;
CREATE TRIGGER trg_canonical_events_updated_at
BEFORE UPDATE ON canonical_events
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS trg_candidate_incidents_updated_at ON candidate_incidents;
CREATE TRIGGER trg_candidate_incidents_updated_at
BEFORE UPDATE ON candidate_incidents
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS trg_service_context_updated_at ON service_context;
CREATE TRIGGER trg_service_context_updated_at
BEFORE UPDATE ON service_context
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS trg_remediation_actions_updated_at ON remediation_actions;
CREATE TRIGGER trg_remediation_actions_updated_at
BEFORE UPDATE ON remediation_actions
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

-- ============================================
-- 12. Seed service context example
-- ============================================
INSERT INTO service_context (
    service_name,
    critical_service,
    owner,
    business_process,
    data_classification,
    regulatory_relevance,
    rto_minutes,
    dependencies,
    metadata
)
VALUES (
    'portfolio-api',
    TRUE,
    'application_support_team',
    'portfolio_management',
    'confidential',
    '["operational_resilience", "incident_reporting"]'::jsonb,
    60,
    '["market-data-gateway", "iam-core", "postgres-prod"]'::jsonb,
    '{"environment":"prototype"}'::jsonb
)
ON CONFLICT (service_name) DO NOTHING;