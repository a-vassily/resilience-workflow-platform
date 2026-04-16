from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.common.repository import insert_canonical_event, list_unprocessed_raw_events, mark_raw_event_normalized, upsert_service_context


def run_once() -> int:
    rows = list_unprocessed_raw_events()
    count = 0
    for row in rows:
        canonical = normalize_event(row['source_system'], row['source_type'], row['payload'], row.get('evidence_pointer'))
        insert_canonical_event(canonical, str(row['id']))
        if canonical['source_type'] == 'risk_context':
            upsert_service_context(row['payload'])
        mark_raw_event_normalized(str(row['id']))
        count += 1
    return count


def normalize_event(source_system: str, source_type: str, payload: dict[str, Any], evidence_pointer: str | None) -> dict[str, Any]:
    if source_type == 'security_event':
        return {
            'event_id': payload['offense_id'],
            'source_system': 'qradar',
            'source_type': 'security_event',
            'event_type': 'failed_privileged_access',
            'event_timestamp': payload['event_time'],
            'severity': _map_numeric_severity(payload.get('severity', 5)),
            'affected_asset': payload.get('asset_hostname'),
            'linked_service': payload.get('service'),
            'vendor_reference': None,
            'evidence_pointer': evidence_pointer,
            'enrichment_tags': payload.get('tags', []),
            'ingesting_adapter': 'qradar-file-adapter-v1',
        }
    if source_type == 'identity_event':
        return {
            'event_id': payload['event_id'],
            'source_system': payload.get('system', source_system),
            'source_type': 'identity_event',
            'event_type': 'repeated_failed_privileged_access',
            'event_timestamp': payload['timestamp'],
            'severity': 'high' if payload.get('risk_score', 0) >= 70 else 'medium',
            'affected_asset': payload.get('target_asset'),
            'linked_service': payload.get('service'),
            'vendor_reference': None,
            'evidence_pointer': evidence_pointer,
            'enrichment_tags': ['privileged', 'identity'],
            'ingesting_adapter': 'iam-file-adapter-v1',
        }
    if source_type == 'vendor_event':
        return {
            'event_id': payload['vendor_event_id'],
            'source_system': 'vendor',
            'source_type': 'vendor_event',
            'event_type': 'vendor_degradation',
            'event_timestamp': payload['timestamp'],
            'severity': 'high' if payload.get('declared_severity') in ['major', 'critical'] else 'medium',
            'affected_asset': None,
            'linked_service': (payload.get('impacted_services') or [None])[0],
            'vendor_reference': payload.get('vendor_name'),
            'evidence_pointer': evidence_pointer,
            'enrichment_tags': ['vendor', 'dependency'],
            'ingesting_adapter': 'vendor-file-adapter-v1',
        }
    if source_type == 'telemetry_alert':
        return {
            'event_id': payload['alert_id'],
            'source_system': payload.get('tool', source_system),
            'source_type': 'telemetry_alert',
            'event_type': 'service_error_rate_high',
            'event_timestamp': payload['timestamp'],
            'severity': 'high' if payload.get('current_value', 0) >= 10 else 'medium',
            'affected_asset': payload.get('asset'),
            'linked_service': payload.get('service'),
            'vendor_reference': None,
            'evidence_pointer': evidence_pointer,
            'enrichment_tags': ['telemetry', 'service'],
            'ingesting_adapter': 'telemetry-file-adapter-v1',
        }
    if source_type == 'risk_context':
        return {
            'event_id': payload['risk_ref'],
            'source_system': 'risk',
            'source_type': 'risk_context',
            'event_type': 'risk_context_update',
            'event_timestamp': datetime.now(timezone.utc).isoformat(),
            'severity': 'info',
            'affected_asset': None,
            'linked_service': payload.get('service'),
            'vendor_reference': None,
            'evidence_pointer': evidence_pointer,
            'enrichment_tags': ['risk', 'context'] + (['critical_service'] if payload.get('critical_service') else []),
            'ingesting_adapter': 'risk-file-adapter-v1',
        }
    raise ValueError(f'Unsupported source type: {source_type}')


def _map_numeric_severity(value: int) -> str:
    if value >= 8:
        return 'high'
    if value >= 5:
        return 'medium'
    return 'low'
