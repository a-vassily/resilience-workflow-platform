from __future__ import annotations

import io
import json
from datetime import datetime, timezone

from app.common.minio_client import get_minio_client
from app.common.repository import (
    get_incident,
    get_latest_ai_response_for_incident,
    list_remediation_actions,
    list_review_actions,
    set_incident_pack_ref,
)

BUCKET = 'artifacts'


def assemble_incident_pack(incident_id: str) -> str:
    """
    Collects all governed state for an incident, serialises it as a structured
    dossier, stores it in MinIO, writes the reference back to the incident
    record, and returns the artifact reference string.
    """
    incident = get_incident(incident_id)
    if not incident:
        raise ValueError(f'Incident {incident_id} not found')

    enrichment = get_latest_ai_response_for_incident(incident_id)
    review_actions = list_review_actions(incident_id)
    remediation_actions = list_remediation_actions(incident_id)

    ai_section = _build_ai_section(enrichment)
    workflow_state = incident.get('workflow_state') or {}

    pack = {
        'pack_version': 'v1',
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'incident': {
            'incident_id': incident['incident_id'],
            'status': incident.get('status'),
            'service_name': incident.get('service_name'),
            'critical_service': incident.get('critical_service'),
            'owner': incident.get('owner'),
            'vendor_name': incident.get('vendor_name'),
            'draft_severity': incident.get('draft_severity'),
            'confidence_score': incident.get('confidence_score'),
            'final_classification': incident.get('final_classification'),
            'decision_maker': incident.get('decision_maker'),
            'decision_notes': incident.get('decision_notes'),
            'created_at': incident.get('created_at'),
            'updated_at': incident.get('updated_at'),
        },
        'correlation': {
            'rule_hits': incident.get('rule_hits', []),
            'confidence_score': incident.get('confidence_score'),
            'threshold_flags': incident.get('threshold_flags', {}),
        },
        'business_context': incident.get('business_context', {}),
        'classification_support': incident.get('classification_support', {}),
        'workflow': {
            'assigned_to': workflow_state.get('assigned_to'),
            'review_due_at': incident.get('review_due_at') or workflow_state.get('review_due_at'),
            'initial_report_due_at': (
                incident.get('initial_report_due_at') or workflow_state.get('initial_report_due_at')
            ),
        },
        'event_timeline': [
            {
                'event_id': e.get('event_id'),
                'event_type': e.get('event_type'),
                'source_type': e.get('source_type'),
                'event_timestamp': e.get('event_timestamp'),
                'severity': e.get('severity'),
                'link_reason': e.get('link_reason'),
            }
            for e in (incident.get('linked_events') or [])
        ],
        'ai_enrichment': ai_section,
        'review_actions': review_actions,
        'remediation_actions': remediation_actions,
        'artifacts': {
            'enrichment_ref': incident.get('enrichment_ref'),
            'reporting_pack_ref': incident.get('reporting_pack_ref'),
        },
    }

    pack_bytes = json.dumps(pack, ensure_ascii=False, indent=2).encode('utf-8')
    timestamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    object_name = f'incident-packs/{incident_id}/pack-{timestamp}.json'

    client = get_minio_client()
    client.put_object(
        BUCKET,
        object_name,
        io.BytesIO(pack_bytes),
        length=len(pack_bytes),
        content_type='application/json',
    )

    ref = f'minio://{BUCKET}/{object_name}'
    set_incident_pack_ref(incident_id, ref)
    return ref


def fetch_incident_pack(incident_pack_ref: str) -> dict:
    """Retrieves a previously stored incident pack from MinIO by its ref string."""
    object_name = incident_pack_ref.removeprefix(f'minio://{BUCKET}/')
    client = get_minio_client()
    response = client.get_object(BUCKET, object_name)
    try:
        return json.loads(response.read())
    finally:
        response.close()
        response.release_conn()


def _build_ai_section(enrichment: dict | None) -> dict | None:
    if not enrichment:
        return None
    body = enrichment.get('response_body') or {}
    return {
        'request_id': enrichment.get('request_id'),
        'model_id': enrichment.get('model_id'),
        'route_used': enrichment.get('route_used'),
        'requested_at': enrichment.get('requested_at'),
        'schema_valid': enrichment.get('schema_valid'),
        'summary': body.get('summary'),
        'root_cause_hypotheses': body.get('root_cause_hypotheses', []),
        'likely_business_impact': body.get('likely_business_impact'),
        'review_memo': body.get('review_memo'),
        'uncertainties': body.get('uncertainties', []),
    }