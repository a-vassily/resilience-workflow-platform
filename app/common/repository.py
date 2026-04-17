from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Iterator
from uuid import UUID

from sqlalchemy import text

from app.common.db import SessionLocal


@contextmanager
def get_session() -> Iterator:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _clean_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict):
        return {k: _clean_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_clean_value(v) for v in value]
    return value


def _mapping_list(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [_clean_value(dict(r)) for r in rows]


def _mapping_one(row: dict[str, Any] | None) -> dict[str, Any] | None:
    return _clean_value(dict(row)) if row else None


def _external_event_id(payload: dict[str, Any]) -> str | None:
    return payload.get('event_id') or payload.get('offense_id') or payload.get('vendor_event_id') or payload.get('alert_id') or payload.get('risk_ref')


# -----------------------------
# Ingest / normalization
# -----------------------------
def insert_raw_event(source_system: str, source_type: str, payload: dict[str, Any], evidence_pointer: str | None) -> str:
    payload_text = json.dumps(payload, sort_keys=True)
    payload_hash = hashlib.sha256(payload_text.encode('utf-8')).hexdigest()
    with get_session() as session:
        result = session.execute(
            text(
                """
                INSERT INTO raw_events (
                    source_system, source_type, external_event_id, payload, payload_hash, evidence_pointer, ingest_status
                )
                VALUES (
                    :source_system, :source_type, :external_event_id, CAST(:payload AS jsonb), :payload_hash, :evidence_pointer, 'received'
                )
                RETURNING id
                """
            ),
            {
                'source_system': source_system,
                'source_type': source_type,
                'external_event_id': _external_event_id(payload),
                'payload': payload_text,
                'payload_hash': payload_hash,
                'evidence_pointer': evidence_pointer,
            },
        )
        return str(result.scalar_one())


def list_unprocessed_raw_events() -> list[dict[str, Any]]:
    with get_session() as session:
        rows = session.execute(
            text(
                """
                SELECT id, source_system, source_type, external_event_id, payload, evidence_pointer
                FROM raw_events
                WHERE ingest_status = 'received'
                ORDER BY created_at ASC
                """
            )
        ).mappings().all()
        return _mapping_list(rows)


def mark_raw_event_normalized(raw_event_id: str) -> None:
    with get_session() as session:
        session.execute(
            text("UPDATE raw_events SET ingest_status = 'normalized', normalized_at = now() WHERE id = :id"),
            {'id': raw_event_id},
        )


def insert_canonical_event(event: dict[str, Any], raw_event_id: str | None) -> None:
    with get_session() as session:
        session.execute(
            text(
                """
                INSERT INTO canonical_events (
                    event_id, raw_event_id, source_system, source_type, event_type, event_timestamp,
                    severity, affected_asset, linked_service, vendor_reference, evidence_pointer,
                    enrichment_tags, ingesting_adapter, normalized_payload, correlation_status
                ) VALUES (
                    :event_id, :raw_event_id, :source_system, :source_type, :event_type, :event_timestamp,
                    :severity, :affected_asset, :linked_service, :vendor_reference, :evidence_pointer,
                    CAST(:enrichment_tags AS jsonb), :ingesting_adapter, CAST(:normalized_payload AS jsonb), 'new'
                )
                ON CONFLICT (event_id) DO NOTHING
                """
            ),
            {
                'event_id': event['event_id'],
                'raw_event_id': raw_event_id,
                'source_system': event['source_system'],
                'source_type': event['source_type'],
                'event_type': event['event_type'],
                'event_timestamp': event['event_timestamp'],
                'severity': event['severity'],
                'affected_asset': event.get('affected_asset'),
                'linked_service': event.get('linked_service'),
                'vendor_reference': event.get('vendor_reference'),
                'evidence_pointer': event.get('evidence_pointer'),
                'enrichment_tags': json.dumps(event.get('enrichment_tags', [])),
                'ingesting_adapter': event['ingesting_adapter'],
                'normalized_payload': json.dumps(event),
            },
        )


def upsert_service_context(payload: dict[str, Any]) -> None:
    service_name = payload.get('service')
    if not service_name:
        return
    with get_session() as session:
        session.execute(
            text(
                """
                INSERT INTO service_context (
                    service_name, critical_service, owner, business_process, data_classification,
                    regulatory_relevance, rto_minutes, dependencies, metadata
                ) VALUES (
                    :service_name, :critical_service, :owner, :business_process, :data_classification,
                    CAST(:regulatory_relevance AS jsonb), :rto_minutes, CAST(:dependencies AS jsonb), CAST(:metadata AS jsonb)
                )
                ON CONFLICT (service_name) DO UPDATE SET
                    critical_service = EXCLUDED.critical_service,
                    owner = EXCLUDED.owner,
                    business_process = EXCLUDED.business_process,
                    data_classification = EXCLUDED.data_classification,
                    regulatory_relevance = EXCLUDED.regulatory_relevance,
                    rto_minutes = EXCLUDED.rto_minutes,
                    dependencies = EXCLUDED.dependencies,
                    metadata = EXCLUDED.metadata,
                    updated_at = now()
                """
            ),
            {
                'service_name': service_name,
                'critical_service': bool(payload.get('critical_service', False)),
                'owner': payload.get('owner'),
                'business_process': payload.get('business_process'),
                'data_classification': payload.get('data_classification'),
                'regulatory_relevance': json.dumps(payload.get('regulatory_relevance', [])),
                'rto_minutes': payload.get('rto_minutes'),
                'dependencies': json.dumps(payload.get('dependencies', [])),
                'metadata': json.dumps({'source': 'risk_context'}),
            },
        )

        risk_ref = payload.get('risk_ref')
        if risk_ref:
            session.execute(
                text(
                    """
                    INSERT INTO risk_context_refs (risk_ref, service_name, risk_payload)
                    VALUES (:risk_ref, :service_name, CAST(:risk_payload AS jsonb))
                    ON CONFLICT (risk_ref) DO UPDATE SET
                        service_name = EXCLUDED.service_name,
                        risk_payload = EXCLUDED.risk_payload
                    """
                ),
                {
                    'risk_ref': risk_ref,
                    'service_name': service_name,
                    'risk_payload': json.dumps(payload),
                },
            )


# -----------------------------
# Correlation / incidents
# -----------------------------
def list_unprocessed_canonical_events() -> list[dict[str, Any]]:
    with get_session() as session:
        rows = session.execute(
            text(
                """
                SELECT *
                FROM canonical_events
                WHERE correlation_status = 'new'
                ORDER BY event_timestamp ASC
                """
            )
        ).mappings().all()
        return _mapping_list(rows)


def get_service_context(service_name: str | None) -> dict[str, Any] | None:
    if not service_name:
        return None
    with get_session() as session:
        row = session.execute(
            text("SELECT * FROM service_context WHERE service_name = :service_name"),
            {'service_name': service_name},
        ).mappings().first()
        return _mapping_one(row)


def mark_canonical_events_processed(event_ids: list[str]) -> None:
    if not event_ids:
        return
    with get_session() as session:
        session.execute(
            text("UPDATE canonical_events SET correlation_status = 'correlated' WHERE event_id = ANY(:event_ids)"),
            {'event_ids': event_ids},
        )


def create_candidate_incident(incident: dict[str, Any]) -> None:
    with get_session() as session:
        session.execute(
            text(
                """
                INSERT INTO candidate_incidents (
                    incident_id, status, confidence_score, rule_hits,
                    service_name, critical_service, owner, vendor_name,
                    threshold_flags, draft_severity, review_due_at, initial_report_due_at,
                    business_context, classification_support, workflow_state, incident_payload,
                    incident_pack_ref, enrichment_ref, reporting_pack_ref, remediation_ref,
                    final_classification, decision_maker, decision_notes
                ) VALUES (
                    :incident_id, :status, :confidence_score, CAST(:rule_hits AS jsonb),
                    :service_name, :critical_service, :owner, :vendor_name,
                    CAST(:threshold_flags AS jsonb), :draft_severity, :review_due_at, :initial_report_due_at,
                    CAST(:business_context AS jsonb), CAST(:classification_support AS jsonb), CAST(:workflow_state AS jsonb), CAST(:incident_payload AS jsonb),
                    :incident_pack_ref, :enrichment_ref, :reporting_pack_ref, :remediation_ref,
                    :final_classification, :decision_maker, :decision_notes
                )
                ON CONFLICT (incident_id) DO NOTHING
                """
            ),
            {
                'incident_id': incident['incident_id'],
                'status': incident['status'],
                'confidence_score': incident['confidence_score'],
                'rule_hits': json.dumps(incident.get('rule_hits', [])),
                'service_name': incident.get('service_name'),
                'critical_service': incident.get('critical_service', False),
                'owner': incident.get('owner'),
                'vendor_name': incident.get('vendor_name'),
                'threshold_flags': json.dumps(incident.get('threshold_flags', {})),
                'draft_severity': incident.get('draft_severity'),
                'review_due_at': incident.get('review_due_at'),
                'initial_report_due_at': incident.get('initial_report_due_at'),
                'business_context': json.dumps(incident.get('business_context', {})),
                'classification_support': json.dumps(incident.get('classification_support', {})),
                'workflow_state': json.dumps(incident.get('workflow_state', {})),
                'incident_payload': json.dumps(incident.get('incident_payload', {})),
                'incident_pack_ref': incident.get('incident_pack_ref'),
                'enrichment_ref': incident.get('enrichment_ref'),
                'reporting_pack_ref': incident.get('reporting_pack_ref'),
                'remediation_ref': incident.get('remediation_ref'),
                'final_classification': incident.get('final_classification'),
                'decision_maker': incident.get('decision_maker'),
                'decision_notes': incident.get('decision_notes'),
            },
        )


def link_incident_events(incident_id: str, event_ids: list[str], link_reason: str = 'service_grouping') -> None:
    with get_session() as session:
        for event_id in event_ids:
            session.execute(
                text(
                    """
                    INSERT INTO incident_event_links (incident_id, event_id, link_reason)
                    VALUES (:incident_id, :event_id, :link_reason)
                    ON CONFLICT (incident_id, event_id) DO NOTHING
                    """
                ),
                {'incident_id': incident_id, 'event_id': event_id, 'link_reason': link_reason},
            )


def list_incidents() -> list[dict[str, Any]]:
    with get_session() as session:
        rows = session.execute(text('SELECT * FROM candidate_incidents ORDER BY created_at DESC')).mappings().all()
        return _mapping_list(rows)


def get_incident(incident_id: str) -> dict[str, Any] | None:
    with get_session() as session:
        row = session.execute(
            text('SELECT * FROM candidate_incidents WHERE incident_id = :incident_id'),
            {'incident_id': incident_id},
        ).mappings().first()
        incident = _mapping_one(row)
        if not incident:
            return None
        event_rows = session.execute(
            text(
                """
                SELECT l.event_id, l.link_reason, e.event_type, e.source_type, e.event_timestamp
                FROM incident_event_links l
                JOIN canonical_events e ON e.event_id = l.event_id
                WHERE l.incident_id = :incident_id
                ORDER BY e.event_timestamp ASC
                """
            ),
            {'incident_id': incident_id},
        ).mappings().all()
        incident['linked_events'] = _mapping_list(event_rows)
        incident['source_event_refs'] = [row['event_id'] for row in incident['linked_events']]
        return incident


def update_incident_status(incident_id: str, status: str, decision_payload: dict[str, Any] | None = None) -> None:
    decision_payload = decision_payload or {}
    final_classification = decision_payload.get('final_classification')
    decision_maker = decision_payload.get('decision_maker') or decision_payload.get('actor')
    decision_notes = decision_payload.get('decision_notes') or decision_payload.get('notes')
    with get_session() as session:
        session.execute(
            text(
                """
                UPDATE candidate_incidents
                SET status = :status,
                    final_classification = COALESCE(:final_classification, final_classification),
                    decision_maker = COALESCE(:decision_maker, decision_maker),
                    decision_notes = COALESCE(:decision_notes, decision_notes),
                    updated_at = now()
                WHERE incident_id = :incident_id
                """
            ),
            {
                'incident_id': incident_id,
                'status': status,
                'final_classification': final_classification,
                'decision_maker': decision_maker,
                'decision_notes': decision_notes,
            },
        )


def insert_review_action(incident_id: str, action_type: str, actor: str, notes: str | None, payload: dict[str, Any]) -> None:
    with get_session() as session:
        current = session.execute(
            text('SELECT status FROM candidate_incidents WHERE incident_id = :incident_id'),
            {'incident_id': incident_id},
        ).scalar_one_or_none()
        old_status = payload.get('old_status')
        new_status = payload.get('new_status') or payload.get('status')
        if action_type.startswith('status_change:') and not old_status:
            old_status = current
        session.execute(
            text(
                """
                INSERT INTO review_actions (incident_id, action_type, actor, action_notes, old_status, new_status, action_payload)
                VALUES (:incident_id, :action_type, :actor, :action_notes, :old_status, :new_status, CAST(:action_payload AS jsonb))
                """
            ),
            {
                'incident_id': incident_id,
                'action_type': action_type,
                'actor': actor,
                'action_notes': notes,
                'old_status': old_status,
                'new_status': new_status,
                'action_payload': json.dumps(payload),
            },
        )


def list_review_actions(incident_id: str) -> list[dict[str, Any]]:
    with get_session() as session:
        rows = session.execute(
            text('SELECT * FROM review_actions WHERE incident_id = :incident_id ORDER BY created_at DESC'),
            {'incident_id': incident_id},
        ).mappings().all()
        return _mapping_list(rows)


# -----------------------------
# Enrichment / artifacts
# -----------------------------
def insert_ai_request(
    incident_id: str,
    workload_type: str,
    model_id: str,
    prompt_template_version: str,
    prompt_hash: str,
    retrieval_refs: list[str],
    prompt_body: dict[str, Any],
    route_used: str = 'lmstudio-openai',
) -> str:
    with get_session() as session:
        request_id = session.execute(
            text(
                """
                INSERT INTO ai_enrichment_requests (
                    incident_id, workload_type, route_used, model_id, prompt_template_version,
                    retrieval_refs, redaction_manifest, outbound_prompt_hash, outbound_prompt, initiating_service_identity
                ) VALUES (
                    :incident_id, :workload_type, :route_used, :model_id, :prompt_template_version,
                    CAST(:retrieval_refs AS jsonb), CAST(:redaction_manifest AS jsonb), :outbound_prompt_hash,
                    CAST(:outbound_prompt AS jsonb), :initiating_service_identity
                )
                RETURNING request_id
                """
            ),
            {
                'incident_id': incident_id,
                'workload_type': workload_type,
                'route_used': route_used,
                'model_id': model_id,
                'prompt_template_version': prompt_template_version,
                'retrieval_refs': json.dumps(retrieval_refs),
                'redaction_manifest': json.dumps({}),
                'outbound_prompt_hash': prompt_hash,
                'outbound_prompt': json.dumps(_clean_value(prompt_body)),
                'initiating_service_identity': 'intelligence-service',
            },
        ).scalar_one()
        return str(request_id)


def insert_ai_response(
    request_id: str,
    response_body: dict[str, Any],
    schema_valid: bool,
    latency_ms: int | None,
    validation_errors: list[str] | None = None,
    token_metadata: dict[str, Any] | None = None,
) -> None:
    with get_session() as session:
        session.execute(
            text(
                """
                INSERT INTO ai_enrichment_responses (
                    request_id, response_body, schema_valid, validation_errors, latency_ms, token_metadata
                ) VALUES (
                    :request_id, CAST(:response_body AS jsonb), :schema_valid, CAST(:validation_errors AS jsonb), :latency_ms, CAST(:token_metadata AS jsonb)
                )
                ON CONFLICT (request_id) DO UPDATE SET
                    response_body = EXCLUDED.response_body,
                    schema_valid = EXCLUDED.schema_valid,
                    validation_errors = EXCLUDED.validation_errors,
                    latency_ms = EXCLUDED.latency_ms,
                    token_metadata = EXCLUDED.token_metadata
                """
            ),
            {
                'request_id': request_id,
                'response_body': json.dumps(_clean_value(response_body)),
                'schema_valid': schema_valid,
                'validation_errors': json.dumps(validation_errors or []),
                'latency_ms': latency_ms,
                'token_metadata': json.dumps(_clean_value(token_metadata or {})),
            },
        )


def set_reporting_pack_ref(incident_id: str, ref: str) -> None:
    with get_session() as session:
        session.execute(
            text(
                """
                UPDATE candidate_incidents
                SET reporting_pack_ref = :ref,
                    updated_at = now()
                WHERE incident_id = :incident_id
                """
            ),
            {'incident_id': incident_id, 'ref': ref},
        )


def set_incident_pack_ref(incident_id: str, ref: str) -> None:
    with get_session() as session:
        session.execute(
            text(
                """
                UPDATE candidate_incidents
                SET incident_pack_ref = :ref,
                    updated_at = now()
                WHERE incident_id = :incident_id
                """
            ),
            {'incident_id': incident_id, 'ref': ref},
        )


def set_latest_enrichment_ref(incident_id: str, request_id: str) -> str:
    enrichment_ref = f'ai-enrichment://{request_id}'
    with get_session() as session:
        session.execute(
            text(
                """
                UPDATE candidate_incidents
                SET enrichment_ref = :enrichment_ref,
                    updated_at = now()
                WHERE incident_id = :incident_id
                """
            ),
            {'incident_id': incident_id, 'enrichment_ref': enrichment_ref},
        )
        session.execute(
            text(
                """
                INSERT INTO incident_artifacts (incident_id, artifact_type, artifact_ref, artifact_metadata, created_by)
                VALUES (:incident_id, 'ai_enrichment_response', :artifact_ref, CAST(:artifact_metadata AS jsonb), 'intelligence-service')
                """
            ),
            {
                'incident_id': incident_id,
                'artifact_ref': enrichment_ref,
                'artifact_metadata': json.dumps({'request_id': request_id}),
            },
        )
    return enrichment_ref


def list_ai_responses_for_incident(incident_id: str) -> list[dict[str, Any]]:
    with get_session() as session:
        rows = session.execute(
            text(
                """
                SELECT r.*, q.incident_id, q.model_id, q.route_used, q.requested_at, q.request_id
                FROM ai_enrichment_responses r
                JOIN ai_enrichment_requests q ON q.request_id = r.request_id
                WHERE q.incident_id = :incident_id
                ORDER BY r.created_at DESC
                """
            ),
            {'incident_id': incident_id},
        ).mappings().all()
        return _mapping_list(rows)


def get_latest_ai_response_for_incident(incident_id: str) -> dict[str, Any] | None:
    rows = list_ai_responses_for_incident(incident_id)
    return rows[0] if rows else None


# -----------------------------
# Audit log
# -----------------------------
def insert_audit_event(
    entity_type: str,
    entity_id: str,
    action_type: str,
    actor: str | None,
    details: dict[str, Any] | None = None,
) -> None:
    with get_session() as session:
        session.execute(
            text(
                """
                INSERT INTO audit_log (entity_type, entity_id, action_type, actor, details)
                VALUES (:entity_type, :entity_id, :action_type, :actor, CAST(:details AS jsonb))
                """
            ),
            {
                'entity_type': entity_type,
                'entity_id': entity_id,
                'action_type': action_type,
                'actor': actor,
                'details': json.dumps(_clean_value(details or {})),
            },
        )


def list_audit_events(entity_type: str, entity_id: str) -> list[dict[str, Any]]:
    with get_session() as session:
        rows = session.execute(
            text(
                """
                SELECT id, entity_type, entity_id, action_type, actor, details, created_at
                FROM audit_log
                WHERE entity_type = :entity_type AND entity_id = :entity_id
                ORDER BY created_at ASC
                """
            ),
            {'entity_type': entity_type, 'entity_id': entity_id},
        ).mappings().all()
        return _mapping_list(rows)


# -----------------------------
# Remediation actions
# -----------------------------
def insert_remediation_action(
    incident_id: str,
    title: str,
    description: str | None,
    owner: str | None,
    due_date: str | None,
    dependency_note: str | None,
) -> str:
    with get_session() as session:
        result = session.execute(
            text(
                """
                INSERT INTO remediation_actions (
                    incident_id, title, description, owner, due_date, dependency_note, status
                ) VALUES (
                    :incident_id, :title, :description, :owner, :due_date, :dependency_note, 'open'
                )
                RETURNING remediation_id
                """
            ),
            {
                'incident_id': incident_id,
                'title': title,
                'description': description,
                'owner': owner,
                'due_date': due_date,
                'dependency_note': dependency_note,
            },
        )
        return str(result.scalar_one())


def list_remediation_actions(incident_id: str) -> list[dict[str, Any]]:
    with get_session() as session:
        rows = session.execute(
            text(
                """
                SELECT * FROM remediation_actions
                WHERE incident_id = :incident_id
                ORDER BY created_at ASC
                """
            ),
            {'incident_id': incident_id},
        ).mappings().all()
        return _mapping_list(rows)


def update_remediation_action(
    remediation_id: str,
    status: str | None,
    closure_evidence_ref: str | None,
    lessons_learned: str | None,
) -> None:
    with get_session() as session:
        session.execute(
            text(
                """
                UPDATE remediation_actions SET
                    status = COALESCE(:status, status),
                    closure_evidence_ref = COALESCE(:closure_evidence_ref, closure_evidence_ref),
                    lessons_learned = COALESCE(:lessons_learned, lessons_learned),
                    updated_at = now()
                WHERE remediation_id = :remediation_id
                """
            ),
            {
                'remediation_id': remediation_id,
                'status': status,
                'closure_evidence_ref': closure_evidence_ref,
                'lessons_learned': lessons_learned,
            },
        )


# -----------------------------
# Demo helpers
# -----------------------------
def clear_demo_data() -> None:
    with get_session() as session:
        for statement in [
            'DELETE FROM incident_artifacts',
            'DELETE FROM ai_enrichment_responses',
            'DELETE FROM ai_enrichment_requests',
            'DELETE FROM review_actions',
            'DELETE FROM incident_event_links',
            'DELETE FROM candidate_incidents',
            'DELETE FROM risk_context_refs',
            'DELETE FROM canonical_events',
            'DELETE FROM raw_events',
            'DELETE FROM service_context',
            'DELETE FROM audit_log',
        ]:
            session.execute(text(statement))
