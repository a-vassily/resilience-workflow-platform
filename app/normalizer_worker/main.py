from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Any

from app.common.repository import insert_canonical_event, list_unprocessed_raw_events, mark_raw_event_normalized, upsert_service_context


def run_once() -> int:
    if _loop_enabled('NORMALIZER_LOOP'):
        return _run_loop()
    return _run_batch()


def _run_batch() -> int:
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


def _run_loop() -> int:
    total = 0
    sleep_seconds = float(os.getenv('NORMALIZER_POLL_SECONDS', '5'))
    max_cycles = int(os.getenv('NORMALIZER_MAX_CYCLES', '0'))
    cycles = 0
    while True:
        processed = _run_batch()
        total += processed
        cycles += 1
        if max_cycles and cycles >= max_cycles:
            return total
        time.sleep(sleep_seconds)


def _loop_enabled(env_name: str) -> bool:
    return os.getenv(env_name, 'false').strip().lower() in {'1', 'true', 'yes', 'on'}


def normalize_event(source_system: str, source_type: str, payload: dict[str, Any], evidence_pointer: str | None) -> dict[str, Any]:
    if source_type == 'security_event':
        event_name = str(payload.get('event_name') or payload.get('category') or '').lower()
        category = str(payload.get('category') or '').lower()
        if 'exfil' in event_name or 'exfil' in category:
            event_type = 'data_exfiltration_alert'
            tags = ['security', 'data_exfiltration']
        elif 'break glass' in event_name or 'break_glass' in category:
            event_type = 'break_glass_account_used'
            tags = ['security', 'privileged', 'break_glass']
        elif 'admin session' in event_name or 'config_change' in category:
            event_type = 'suspicious_admin_session'
            tags = ['security', 'privileged', 'admin_activity']
        elif 'malware' in event_name:
            event_type = 'malware_detected'
            tags = ['security', 'malware']
        else:
            event_type = 'failed_privileged_access'
            tags = ['privileged', 'authentication', 'security']
        tags = list(dict.fromkeys((payload.get('tags') or []) + tags))
        return {
            'event_id': payload['offense_id'],
            'source_system': 'qradar',
            'source_type': 'security_event',
            'event_type': event_type,
            'event_timestamp': payload['event_time'],
            'severity': _map_numeric_severity(payload.get('severity', 5)),
            'affected_asset': payload.get('asset_hostname'),
            'linked_service': payload.get('service'),
            'vendor_reference': payload.get('vendor_name'),
            'evidence_pointer': evidence_pointer,
            'enrichment_tags': tags,
            'ingesting_adapter': 'qradar-file-adapter-v1',
        }
    if source_type == 'identity_event':
        raw_type = str(payload.get('event_type') or '').lower()
        if raw_type in {'impossible_travel_admin_login', 'geo_impossible_admin_login'}:
            event_type = 'impossible_travel_admin_login'
            tags = ['identity', 'privileged', 'impossible_travel']
        elif raw_type in {'break_glass_account_used', 'pam_break_glass_used'}:
            event_type = 'break_glass_account_used'
            tags = ['identity', 'privileged', 'break_glass']
        elif raw_type in {'privileged_group_change', 'suspicious_privilege_escalation'}:
            event_type = 'privileged_group_change'
            tags = ['identity', 'privileged', 'authorization']
        elif raw_type == 'incident_ticket_signal':
            event_type = 'incident_ticket_signal'
            tags = ['itsm', 'incident']
        else:
            event_type = 'repeated_failed_privileged_access'
            tags = ['privileged', 'identity']
        risk_score = payload.get('risk_score', 0) or 0
        return {
            'event_id': payload['event_id'],
            'source_system': payload.get('system', source_system),
            'source_type': 'identity_event',
            'event_type': event_type,
            'event_timestamp': payload['timestamp'],
            'severity': 'high' if risk_score >= 70 else 'medium' if risk_score >= 40 else 'low',
            'affected_asset': payload.get('target_asset'),
            'linked_service': payload.get('service'),
            'vendor_reference': None,
            'evidence_pointer': evidence_pointer,
            'enrichment_tags': tags,
            'ingesting_adapter': 'iam-file-adapter-v1',
        }
    if source_type == 'vendor_event':
        status = str(payload.get('status') or '').lower()
        if status in {'outage', 'down', 'unavailable'}:
            event_type = 'vendor_outage'
            tags = ['vendor', 'dependency', 'availability']
        elif status in {'latency', 'degraded-performance'}:
            event_type = 'vendor_latency_degradation'
            tags = ['vendor', 'dependency', 'performance']
        elif status in {'sla_breach', 'sla-breach'}:
            event_type = 'vendor_sla_breach'
            tags = ['vendor', 'dependency', 'sla']
        else:
            event_type = 'vendor_degradation'
            tags = ['vendor', 'dependency']
        sev = str(payload.get('declared_severity') or '').lower()
        severity = 'critical' if sev in {'critical', 'sev1'} else 'high' if sev in {'major', 'high', 'sev2'} else 'medium'
        return {
            'event_id': payload['vendor_event_id'],
            'source_system': 'vendor',
            'source_type': 'vendor_event',
            'event_type': event_type,
            'event_timestamp': payload['timestamp'],
            'severity': severity,
            'affected_asset': None,
            'linked_service': (payload.get('impacted_services') or [None])[0],
            'vendor_reference': payload.get('vendor_name'),
            'evidence_pointer': evidence_pointer,
            'enrichment_tags': tags,
            'ingesting_adapter': 'vendor-file-adapter-v1',
        }
    if source_type == 'telemetry_alert':
        alert_name = str(payload.get('alert_name') or '').lower()
        metric = str(payload.get('metric') or '').lower()
        labels = payload.get('labels') or {}
        if 'restart' in alert_name or 'restart' in metric:
            event_type = 'pod_restart_storm'
            tags = ['telemetry', 'platform', 'kubernetes']
        elif 'cpu' in alert_name or metric in {'cpu_utilization', 'cpu_saturation'}:
            event_type = 'cpu_saturation'
            tags = ['telemetry', 'capacity', 'cpu']
        elif 'memory' in alert_name or metric in {'memory_utilization', 'memory_pressure'}:
            event_type = 'memory_pressure'
            tags = ['telemetry', 'capacity', 'memory']
        elif 'synthetic' in alert_name or metric == 'synthetic_check_failure':
            event_type = 'synthetic_check_failure'
            tags = ['telemetry', 'availability', 'synthetic']
        elif 'latency' in alert_name or metric in {'latency_ms_p95', 'request_latency'}:
            event_type = 'latency_spike'
            tags = ['telemetry', 'performance', 'latency']
        elif 'queue' in alert_name or metric == 'queue_backlog':
            event_type = 'queue_backlog_high'
            tags = ['telemetry', 'capacity', 'queue']
        elif 'batch' in alert_name or metric == 'batch_job_failures':
            event_type = 'batch_job_failure'
            tags = ['telemetry', 'batch', 'jobs']
        elif 'disk' in alert_name or metric in {'disk_used_percent', 'disk_free_bytes'}:
            event_type = 'disk_full_risk'
            tags = ['telemetry', 'capacity', 'storage']
        elif 'backup' in alert_name or metric == 'backup_failures':
            event_type = 'backup_failure'
            tags = ['telemetry', 'backup', 'storage']
        else:
            event_type = 'service_error_rate_high'
            tags = ['telemetry', 'service']
        current_value = payload.get('current_value', 0) or 0
        severity = 'critical' if current_value >= 20 else 'high' if current_value >= 10 else 'medium'
        return {
            'event_id': payload['alert_id'],
            'source_system': payload.get('tool', source_system),
            'source_type': 'telemetry_alert',
            'event_type': event_type,
            'event_timestamp': payload['timestamp'],
            'severity': severity,
            'affected_asset': payload.get('asset') or labels.get('node'),
            'linked_service': payload.get('service'),
            'vendor_reference': None,
            'evidence_pointer': evidence_pointer,
            'enrichment_tags': tags,
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
    if value >= 9:
        return 'critical'
    if value >= 7:
        return 'high'
    if value >= 4:
        return 'medium'
    return 'low'
