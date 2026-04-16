from __future__ import annotations

import json
from collections import defaultdict
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

BASE_DIR = Path(__file__).resolve().parents[2]
RULES_PATH = BASE_DIR / 'rules' / 'correlation_rules.yaml'


def run_once() -> int:
    events = list_unprocessed_canonical_events()
    if not events:
        return 0

    grouped = defaultdict(list)
    for event in events:
        key = event.get('linked_service') or event.get('vendor_reference') or 'unassigned'
        grouped[key].append(event)

    rules = yaml.safe_load(RULES_PATH.read_text(encoding='utf-8'))['rules']
    created = 0
    processed_ids: list[str] = []

    for group_key, group_events in grouped.items():
        service_name = next((e.get('linked_service') for e in group_events if e.get('linked_service')), group_key)
        context = get_service_context(service_name) or {}
        event_types = {e['event_type'] for e in group_events}
        tags = {tag for e in group_events for tag in (e.get('enrichment_tags') or [])}

        matched_rule = None
        for rule in rules:
            if _matches(rule, event_types, tags, context, group_events):
                matched_rule = rule
                break

        if not matched_rule:
            continue

        incident_id = f'inc-{uuid4()}'
        review_due_at = datetime.now(timezone.utc) + timedelta(hours=4)
        initial_report_due_at = datetime.now(timezone.utc) + timedelta(hours=24)
        incident = {
            'incident_id': incident_id,
            'status': 'candidate',
            'confidence_score': matched_rule['confidence_score'],
            'rule_hits': [matched_rule['id']],
            'service_name': service_name,
            'critical_service': bool(context.get('critical_service', False)),
            'owner': context.get('owner'),
            'vendor_name': next((e.get('vendor_reference') for e in group_events if e.get('vendor_reference')), None),
            'threshold_flags': {
                'critical_service_impact': bool(context.get('critical_service', False)),
                'unauthorized_access_indicator': any(e['event_type'] in ['failed_privileged_access', 'repeated_failed_privileged_access'] for e in group_events),
                'multi_signal_pattern': len(group_events) >= 2,
            },
            'draft_severity': matched_rule['draft_severity'],
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
                    'unauthorized_access_indicator': any(e['event_type'] in ['failed_privileged_access', 'repeated_failed_privileged_access'] for e in group_events),
                },
                'draft_severity': matched_rule['draft_severity'],
            },
            'workflow_state': {
                'assigned_to': 'incident_commander',
                'review_due_at': review_due_at.isoformat(),
                'initial_report_due_at': initial_report_due_at.isoformat(),
            },
            'incident_payload': {
                'matched_rule': matched_rule['id'],
                'events': [
                    {
                        'event_id': e['event_id'],
                        'event_type': e['event_type'],
                        'source_type': e['source_type'],
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


def _matches(rule: dict, event_types: set[str], tags: set[str], context: dict, service_events: list[dict]) -> bool:
    if len(service_events) < rule.get('min_events', 1):
        return False
    for cond in rule['conditions']['all']:
        if 'event_type_in' in cond and not event_types.intersection(set(cond['event_type_in'])):
            return False
        if cond.get('critical_service') and not context.get('critical_service'):
            return False
        if 'tag_any' in cond and not tags.intersection(set(cond['tag_any'])):
            return False
        if cond.get('same_linked_service'):
            services = {e.get('linked_service') for e in service_events}
            if len(services) != 1:
                return False
    return True
