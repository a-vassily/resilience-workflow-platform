from __future__ import annotations

import json
from pathlib import Path

from app.common.repository import clear_demo_data, insert_raw_event, list_incidents
from app.normalizer_worker.main import _run_batch as normalize_batch
from app.correlator_worker.main import _run_batch as correlate_batch

BASE_DIR = Path(__file__).resolve().parents[2]
SAMPLE_DIR = BASE_DIR / 'sample_data'
SOURCE_DIRS = ['qradar', 'iam', 'vendor', 'telemetry', 'risk']


def replay_demo(enrich: bool = False) -> dict:
    clear_demo_data()
    raw_count = 0
    for folder in SOURCE_DIRS:
        for path in sorted((SAMPLE_DIR / folder).glob('*.json')):
            payload = json.loads(path.read_text(encoding='utf-8'))
            source_system = payload.get('system') or payload.get('tool') or payload.get('vendor_name') or folder
            source_type = _guess_source_type(payload)
            insert_raw_event(source_system, source_type, payload, evidence_pointer=f'file://{path.as_posix()}')
            raw_count += 1

    normalized = normalize_batch()
    incidents_created = correlate_batch()
    incidents = list_incidents()
    latest_incident_id = incidents[0]['incident_id'] if incidents else None

    result = {
        'raw_events_inserted': raw_count,
        'normalized_events': normalized,
        'incidents_created': incidents_created,
        'latest_incident_id': latest_incident_id,
    }

    if enrich and latest_incident_id:
        import httpx
        from app.common.config import get_settings
        settings = get_settings()
        with httpx.Client(timeout=180) as client:
            response = client.post(f'{settings.intelligence_service_url}/incidents/{latest_incident_id}/enrich')
            response.raise_for_status()
            result['enrichment'] = response.json()

    return result


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
