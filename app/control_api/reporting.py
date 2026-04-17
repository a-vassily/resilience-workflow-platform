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
    set_reporting_pack_ref,
)

BUCKET = 'reports'
DISCLAIMER = (
    'DRAFT — AI-assisted content. Every field requires human review and approval. '
    'This document must not be submitted to any regulator or external party without '
    'explicit human classification, sign-off, and approval of final content.'
)


def assemble_report_draft(incident_id: str) -> str:
    """
    Assembles a regulator-ready draft reporting package for human review.
    Stores it in MinIO reports bucket, writes the ref to the incident record,
    and returns the artifact reference string.
    """
    incident = get_incident(incident_id)
    if not incident:
        raise ValueError(f'Incident {incident_id} not found')

    enrichment = get_latest_ai_response_for_incident(incident_id)
    review_actions = list_review_actions(incident_id)
    remediation_actions = list_remediation_actions(incident_id)

    ai_body = (enrichment or {}).get('response_body') or {}
    threshold_flags = incident.get('threshold_flags') or {}
    business_context = incident.get('business_context') or {}
    workflow_state = incident.get('workflow_state') or {}
    classification_support = incident.get('classification_support') or {}

    open_items = _derive_open_items(incident, ai_body, review_actions, remediation_actions)

    report = {
        'report_version': 'draft-v1',
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'disclaimer': DISCLAIMER,
        'classification_state': {
            'incident_id': incident['incident_id'],
            'current_status': incident.get('status'),
            'final_classification': incident.get('final_classification'),
            'draft_severity': incident.get('draft_severity'),
            'decision_maker': incident.get('decision_maker'),
            'decision_notes': incident.get('decision_notes'),
            'reportable_determination': _reportable_determination(incident),
            'threshold_flags': threshold_flags,
            'regulatory_relevance': business_context.get('regulatory_relevance', []),
        },
        'incident_summary': {
            'service_name': incident.get('service_name'),
            'critical_service': incident.get('critical_service'),
            'owner': incident.get('owner'),
            'vendor_name': incident.get('vendor_name'),
            'rule_hits': incident.get('rule_hits', []),
            'confidence_score': incident.get('confidence_score'),
            'event_count': len(incident.get('linked_events') or []),
            'detection_timestamp': incident.get('created_at'),
            'review_due_at': incident.get('review_due_at') or workflow_state.get('review_due_at'),
            'initial_report_due_at': (
                incident.get('initial_report_due_at') or workflow_state.get('initial_report_due_at')
            ),
            'matched_rule_count': classification_support.get('matched_rule_count'),
        },
        'event_timeline': [
            {
                'event_id': e.get('event_id'),
                'event_type': e.get('event_type'),
                'source_type': e.get('source_type'),
                'event_timestamp': e.get('event_timestamp'),
                'severity': e.get('severity'),
            }
            for e in (incident.get('linked_events') or [])
        ],
        'ai_draft_narrative': {
            'disclaimer': 'AI-generated. Must be reviewed and rewritten or confirmed by a human before any submission.',
            'summary': ai_body.get('summary'),
            'likely_business_impact': ai_body.get('likely_business_impact'),
            'root_cause_hypotheses': ai_body.get('root_cause_hypotheses', []),
            'uncertainties': ai_body.get('uncertainties', []),
            'review_memo': ai_body.get('review_memo'),
            'enrichment_model': (enrichment or {}).get('model_id'),
            'enrichment_schema_valid': (enrichment or {}).get('schema_valid'),
        },
        'evidence_references': {
            'enrichment_ref': incident.get('enrichment_ref'),
            'incident_pack_ref': incident.get('incident_pack_ref'),
            'source_event_refs': incident.get('source_event_refs', []),
        },
        'review_actions_summary': [
            {
                'action_type': a.get('action_type'),
                'actor': a.get('actor'),
                'notes': a.get('action_notes'),
                'created_at': a.get('created_at'),
            }
            for a in review_actions
        ],
        'remediation_summary': {
            'total': len(remediation_actions),
            'open': sum(1 for a in remediation_actions if a.get('status') == 'open'),
            'in_progress': sum(1 for a in remediation_actions if a.get('status') == 'in_progress'),
            'closed': sum(1 for a in remediation_actions if a.get('status') == 'closed'),
        },
        'open_items': open_items,
        'pending_approvals': _pending_approvals(incident),
    }

    report_bytes = json.dumps(report, ensure_ascii=False, indent=2).encode('utf-8')
    timestamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    object_name = f'incident-reports/{incident_id}/draft-{timestamp}.json'

    client = get_minio_client()
    client.put_object(
        BUCKET,
        object_name,
        io.BytesIO(report_bytes),
        length=len(report_bytes),
        content_type='application/json',
    )

    ref = f'minio://{BUCKET}/{object_name}'
    set_reporting_pack_ref(incident_id, ref)
    return ref


def fetch_report_draft(reporting_pack_ref: str) -> dict:
    """Retrieves a previously stored report draft from MinIO by its ref string."""
    object_name = reporting_pack_ref.removeprefix(f'minio://{BUCKET}/')
    client = get_minio_client()
    response = client.get_object(BUCKET, object_name)
    try:
        return json.loads(response.read())
    finally:
        response.close()
        response.release_conn()


def _reportable_determination(incident: dict) -> str:
    if incident.get('status') == 'classified_reportable':
        return 'CLASSIFIED AS REPORTABLE — pending formal approval and submission'
    if incident.get('final_classification'):
        return f'Classified: {incident["final_classification"]} — review reportability determination'
    return 'PENDING — human classification decision required'


def _derive_open_items(
    incident: dict,
    ai_body: dict,
    review_actions: list[dict],
    remediation_actions: list[dict],
) -> list[str]:
    items: list[str] = []

    if not incident.get('final_classification'):
        items.append('Final classification not yet determined — human decision required')

    if incident.get('status') not in {'classified_reportable', 'reported_initial', 'reported_intermediate', 'reported_final'}:
        items.append('Incident not yet classified as reportable — reviewer must confirm or reject regulatory relevance')

    if not review_actions:
        items.append('No formal human review actions recorded — at least one review note is expected before reporting')

    uncertainties = ai_body.get('uncertainties') or []
    for u in uncertainties:
        if u:
            items.append(f'AI uncertainty to resolve: {u}')

    open_rem = [a for a in remediation_actions if a.get('status') in {'open', 'in_progress'}]
    if open_rem:
        items.append(f'{len(open_rem)} remediation action(s) still open or in progress')

    if not incident.get('enrichment_ref'):
        items.append('No AI enrichment on record — consider running enrichment before finalising the report')

    if not items:
        items.append('No blocking open items identified — proceed to human review and approval')

    return items


def _pending_approvals(incident: dict) -> list[str]:
    approvals: list[str] = []
    status = incident.get('status', '')

    approvals.append('Human review and confirmation of all AI-generated narrative text')
    approvals.append('Final classification approval by incident commander or designated approver')

    if status in {'classified_reportable', 'reported_initial', 'reported_intermediate', 'reported_final'}:
        approvals.append('Initial reporting package approval before submission')
        approvals.append('Verification that all evidence references are correct and complete')

    approvals.append('Compliance or risk sign-off on regulatory relevance determination')
    return approvals