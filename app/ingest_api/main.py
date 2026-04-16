from __future__ import annotations

import io
import json
from datetime import datetime, timezone

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from app.common.minio_client import get_minio_client
from app.common.repository import insert_raw_event
from app.common.schemas import IngestEventRequest

app = FastAPI(title='Resilience Ingest API', version='0.2.0')
app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)


@app.get('/health')
def health() -> dict[str, str]:
    return {'status': 'ok'}


@app.post('/ingest/event')
def ingest_event(request: IngestEventRequest) -> dict[str, str]:
    payload_bytes = json.dumps(request.payload).encode('utf-8')
    evidence_pointer = _store_raw_payload(request.source_system, payload_bytes)
    raw_event_id = insert_raw_event(request.source_system, request.source_type, request.payload, evidence_pointer)
    return {'raw_event_id': raw_event_id, 'evidence_pointer': evidence_pointer}


@app.post('/ingest/file')
async def ingest_file(file: UploadFile = File(...)) -> dict[str, str]:
    try:
        payload = json.loads((await file.read()).decode('utf-8'))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f'Invalid JSON: {exc}') from exc

    source_system = payload.get('system') or payload.get('tool') or payload.get('vendor_name') or file.filename.split('_')[0]
    source_type = _guess_source_type(payload)
    payload_bytes = json.dumps(payload).encode('utf-8')
    evidence_pointer = _store_raw_payload(source_system, payload_bytes)
    raw_event_id = insert_raw_event(source_system, source_type, payload, evidence_pointer)
    return {'raw_event_id': raw_event_id, 'source_system': source_system, 'source_type': source_type}


@app.post('/adapters/jira/webhook')
def ingest_jira_webhook(payload: dict) -> dict[str, str]:
    issue = payload.get('issue', {})
    mapped = {
        'alert_id': issue.get('key', 'JIRA-UNKNOWN'),
        'timestamp': payload.get('timestamp') or datetime.now(timezone.utc).isoformat(),
        'tool': 'jira',
        'alert_name': payload.get('webhookEvent', 'jira_issue_event'),
        'service': ((issue.get('fields') or {}).get('customfield_service')) or ((issue.get('fields') or {}).get('project') or {}).get('key'),
        'asset': None,
        'metric': 'ticket_signal',
        'current_value': 1,
        'threshold': 1,
        'unit': 'count',
        'labels': {
            'priority': ((issue.get('fields') or {}).get('priority') or {}).get('name'),
            'status': ((issue.get('fields') or {}).get('status') or {}).get('name'),
            'issue_type': ((issue.get('fields') or {}).get('issuetype') or {}).get('name'),
        },
    }
    evidence_pointer = _store_raw_payload('jira', json.dumps(payload).encode('utf-8'))
    raw_event_id = insert_raw_event('jira', 'telemetry_alert', mapped, evidence_pointer)
    return {'raw_event_id': raw_event_id, 'mapped_source_type': 'telemetry_alert'}


@app.post('/adapters/servicenow/event')
def ingest_servicenow_event(payload: dict) -> dict[str, str]:
    mapped = {
        'event_id': payload.get('sys_id', 'SNOW-UNKNOWN'),
        'timestamp': payload.get('opened_at') or datetime.now(timezone.utc).isoformat(),
        'system': 'servicenow',
        'event_type': 'incident_ticket_signal',
        'account': payload.get('caller_id') or 'unknown',
        'target_asset': payload.get('cmdb_ci'),
        'service': payload.get('business_service') or payload.get('u_service_name'),
        'failure_count': 1,
        'time_window_minutes': 60,
        'risk_score': 55 if payload.get('priority') in ['2', '3'] else 75,
        'short_description': payload.get('short_description'),
        'state': payload.get('state'),
    }
    evidence_pointer = _store_raw_payload('servicenow', json.dumps(payload).encode('utf-8'))
    raw_event_id = insert_raw_event('servicenow', 'identity_event', mapped, evidence_pointer)
    return {'raw_event_id': raw_event_id, 'mapped_source_type': 'identity_event'}


def _guess_source_type(payload: dict) -> str:
    if 'offense_id' in payload:
        return 'security_event'
    if payload.get('system') == 'cyberark' or 'failure_count' in payload:
        return 'identity_event'
    if 'vendor_event_id' in payload or 'impacted_services' in payload:
        return 'vendor_event'
    if 'alert_id' in payload or payload.get('tool') == 'grafana':
        return 'telemetry_alert'
    if 'risk_ref' in payload:
        return 'risk_context'
    return 'unknown'


def _store_raw_payload(source_system: str, payload_bytes: bytes) -> str:
    client = get_minio_client()
    object_name = f"{source_system}/{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    client.put_object('raw-events', object_name, io.BytesIO(payload_bytes), length=len(payload_bytes), content_type='application/json')
    return f'minio://raw-events/{object_name}'
