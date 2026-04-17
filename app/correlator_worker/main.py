from __future__ import annotations

import os
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import yaml

from app.common.repository import (
    create_candidate_incident,
    get_service_context,
    link_incident_events,
    list_unprocessed_canonical_events,
    mark_canonical_events_processed,
)

RULES_PATH = Path(__file__).resolve().parents[2] / 'rules' / 'correlation_rules.yaml'
SEVERITY_ORDER = {'info': 0, 'low': 1, 'medium': 2, 'high': 3, 'critical': 4}


def run_once() -> int:
    if _loop_enabled('CORRELATOR_LOOP'):
        return _run_loop()
    return _run_batch()


def _run_batch() -> int:
    rows = list_unprocessed_canonical_events()
    if not rows:
        return 0

    grouped: dict[str, list[dict]] = defaultdict(list)
    for event in rows:
        key = event.get('linked_service') or event.get('affected_asset') or event.get('vendor_reference') or 'ungrouped'
        grouped[key].append(event)

    rules = yaml.safe_load(RULES_PATH.read_text(encoding='utf-8'))['rules']
    created = 0
    processed_ids: list[str] = []

    for group_key, group_events in grouped.items():
        service_name = next((e.get('linked_service') for e in group_events if e.get('linked_service')), group_key)
        context = get_service_context(service_name) or {}
        matched_rules = [rule for rule in rules if _matches(rule, context, group_events)]

        if not matched_rules:
            continue

        best_score = max(float(rule.get('confidence_score', 0.5)) for rule in matched_rules)
        draft_severity = _max_severity([rule.get('draft_severity', 'medium') for rule in matched_rules] + [e.get('severity', 'low') for e in group_events])
        rule_hits = [rule['id'] for rule in matched_rules]
        event_type_counter = Counter(e['event_type'] for e in group_events)
        source_type_counter = Counter(e['source_type'] for e in group_events)

        incident_id = f'inc-{uuid4()}'
        review_due_at = datetime.now(timezone.utc) + timedelta(hours=4)
        initial_report_due_at = datetime.now(timezone.utc) + timedelta(hours=24)
        incident = {
            'incident_id': incident_id,
            'status': 'candidate',
            'confidence_score': round(best_score, 4),
            'rule_hits': rule_hits,
            'service_name': service_name,
            'critical_service': bool(context.get('critical_service', False)),
            'owner': context.get('owner'),
            'vendor_name': next((e.get('vendor_reference') for e in group_events if e.get('vendor_reference')), None),
            'threshold_flags': {
                'critical_service_impact': bool(context.get('critical_service', False)),
                'unauthorized_access_indicator': any(e['event_type'] in ['failed_privileged_access', 'repeated_failed_privileged_access', 'impossible_travel_admin_login'] for e in group_events),
                'multi_signal_pattern': len(group_events) >= 2,
                'vendor_dependency_issue': any(e['source_type'] == 'vendor_event' for e in group_events),
                'platform_instability': any(e['event_type'] in ['pod_restart_storm', 'cpu_saturation', 'memory_pressure'] for e in group_events),
            },
            'draft_severity': draft_severity,
            'review_due_at': review_due_at.isoformat(),
            'initial_report_due_at': initial_report_due_at.isoformat(),
            'business_context': {
                'service_name': service_name,
                'critical_service': bool(context.get('critical_service', False)),
                'owner': context.get('owner'),
                'dependencies': context.get('dependencies', []),
                'source_event_refs': [e['event_id'] for e in group_events],
            },
            'classification_support': {
                'threshold_flags': {
                    'critical_service_impact': bool(context.get('critical_service', False)),
                    'unauthorized_access_indicator': any(e['event_type'] in ['failed_privileged_access', 'repeated_failed_privileged_access', 'impossible_travel_admin_login'] for e in group_events),
                },
                'draft_severity': draft_severity,
                'matched_rule_count': len(rule_hits),
            },
            'workflow_state': {
                'assigned_to': 'incident_commander',
                'review_due_at': review_due_at.isoformat(),
                'initial_report_due_at': initial_report_due_at.isoformat(),
            },
            'incident_payload': {
                'matched_rules': rule_hits,
                'event_type_counts': dict(event_type_counter),
                'source_type_counts': dict(source_type_counter),
                'events': [
                    {
                        'event_id': e['event_id'],
                        'event_type': e['event_type'],
                        'source_type': e['source_type'],
                        'severity': e.get('severity'),
                        'event_timestamp': e['event_timestamp'].isoformat() if hasattr(e['event_timestamp'], 'isoformat') else str(e['event_timestamp']),
                    }
                    for e in group_events
                ],
            },
            'incident_pack_ref': None,
            'enrichment_ref': None,
            'reporting_pack_ref': None,
            'remediation_ref': None,
            'final_classification': None,
            'decision_maker': None,
            'decision_notes': None,
        }
        create_candidate_incident(incident)
        link_incident_events(incident_id, [e['event_id'] for e in group_events])
        processed_ids.extend([e['event_id'] for e in group_events])
        created += 1

    if processed_ids:
        mark_canonical_events_processed(processed_ids)
    return created


def _run_loop() -> int:
    total = 0
    sleep_seconds = float(os.getenv('CORRELATOR_POLL_SECONDS', '5'))
    max_cycles = int(os.getenv('CORRELATOR_MAX_CYCLES', '0'))
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


def _matches(rule: dict, context: dict, service_events: list[dict]) -> bool:
    if len(service_events) < rule.get('min_events', 1):
        return False

    event_types = [e['event_type'] for e in service_events]
    event_type_set = set(event_types)
    tags = {tag for e in service_events for tag in (e.get('enrichment_tags') or [])}
    severities = [e.get('severity', 'low') for e in service_events]
    source_types = {e.get('source_type') for e in service_events}

    for cond in rule.get('conditions', {}).get('all', []):
        if 'event_type_all' in cond and not set(cond['event_type_all']).issubset(event_type_set):
            return False
        if 'event_type_any' in cond and not event_type_set.intersection(set(cond['event_type_any'])):
            return False
        if 'event_type_count_at_least' in cond:
            spec = cond['event_type_count_at_least']
            count = sum(1 for e in event_types if e in set(spec.get('types', [])))
            if count < int(spec.get('count', 1)):
                return False
        if cond.get('critical_service') and not context.get('critical_service'):
            return False
        if 'tag_any' in cond and not tags.intersection(set(cond['tag_any'])):
            return False
        if 'source_type_any' in cond and not source_types.intersection(set(cond['source_type_any'])):
            return False
        if cond.get('same_linked_service'):
            services = {e.get('linked_service') for e in service_events}
            if len(services) != 1:
                return False
        if cond.get('vendor_present') and not any(e.get('vendor_reference') for e in service_events):
            return False
        if 'min_distinct_sources' in cond and len(source_types) < int(cond['min_distinct_sources']):
            return False
        if 'severity_at_least' in cond and max(SEVERITY_ORDER.get(s, 0) for s in severities) < SEVERITY_ORDER.get(cond['severity_at_least'], 0):
            return False
    return True


def _max_severity(values: list[str]) -> str:
    values = values or ['medium']
    return max(values, key=lambda x: SEVERITY_ORDER.get(x, 0))
