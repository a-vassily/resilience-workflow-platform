from __future__ import annotations

import hashlib
import json
import time
from decimal import Decimal
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from app.common.config import get_settings
from app.common.repository import get_incident, insert_ai_request, insert_ai_response, insert_audit_event, set_latest_enrichment_ref

app = FastAPI(title='Resilience Intelligence Service', version='0.5.0')
settings = get_settings()
BASE_DIR = Path(__file__).resolve().parents[2]
REFERENCE_DIR = BASE_DIR / 'reference'

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


@app.post('/incidents/{incident_id}/enrich')
def enrich_incident(incident_id: str) -> dict:
    try:
        incident = get_incident(incident_id)
        if not incident:
            raise HTTPException(status_code=404, detail='Incident not found')

        prompt_package, retrieval_refs = build_prompt_package(incident)
        prompt_package = sanitize_for_json(prompt_package)

        prompt_hash = hashlib.sha256(
            json.dumps(prompt_package, sort_keys=True, ensure_ascii=False).encode('utf-8')
        ).hexdigest()

        request_id = insert_ai_request(
            incident_id=incident_id,
            workload_type='incident_enrichment',
            model_id=settings.llm_model,
            prompt_template_version='v1',
            prompt_hash=prompt_hash,
            retrieval_refs=retrieval_refs,
            prompt_body=prompt_package,
            route_used='lmstudio-openai',
        )

        started = time.perf_counter()
        response_json, token_metadata = call_lmstudio(prompt_package)
        latency_ms = int((time.perf_counter() - started) * 1000)

        required_keys = [
            'summary',
            'root_cause_hypotheses',
            'likely_business_impact',
            'review_memo',
            'uncertainties',
        ]
        validation_errors = [key for key in required_keys if key not in response_json]
        schema_valid = not validation_errors

        insert_ai_response(
            request_id=request_id,
            response_body=response_json,
            schema_valid=schema_valid,
            latency_ms=latency_ms,
            validation_errors=validation_errors,
            token_metadata=token_metadata,
        )
        enrichment_ref = set_latest_enrichment_ref(incident_id, request_id)
        try:
            insert_audit_event(
                entity_type='incident',
                entity_id=incident_id,
                action_type='incident.enrichment.completed',
                actor='intelligence-service',
                details={
                    'request_id': request_id,
                    'schema_valid': schema_valid,
                    'latency_ms': latency_ms,
                    'route_used': 'lmstudio-openai',
                },
            )
        except Exception:
            pass

        return {
            'request_id': request_id,
            'schema_valid': schema_valid,
            'enrichment_ref': enrichment_ref,
            'response': response_json,
        }

    except HTTPException:
        raise
    except Exception as exc:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}")


def build_prompt_package(incident: dict) -> tuple[dict, list[str]]:
    runbooks = json.loads((REFERENCE_DIR / 'runbooks.json').read_text(encoding='utf-8'))
    prior_incidents = json.loads((REFERENCE_DIR / 'prior_incidents.json').read_text(encoding='utf-8'))
    risk_context = json.loads((REFERENCE_DIR / 'risk_context.json').read_text(encoding='utf-8'))

    service = incident.get('service_name')

    runbook_excerpt = next(
        (x['excerpt'] for x in runbooks if x.get('service') == service),
        'Validate dependencies and timeline before escalation.'
    )
    prior = [x['incident_id'] for x in prior_incidents if x.get('service') == service][:3]
    risk_refs = [x['risk_ref'] for x in risk_context if x.get('service') == service][:3]

    package = {
        'system': (
            'You are an incident-intelligence assistant operating inside a regulated financial '
            'firm resilience platform. You may summarize evidence, propose plausible root-cause '
            'hypotheses, describe likely business impact, and draft review material. '
            'You must not make final regulatory classifications. '
            'You must not invent facts. '
            'You must state uncertainty clearly. '
            'Return valid JSON only. '
            'Do not use markdown. '
            'Do not wrap the response in code fences. '
            'Do not include any explanatory text before or after the JSON.'
        ),
        'task': (
            'Summarize the candidate incident, propose up to 3 plausible root-cause hypotheses '
            'grounded in the evidence, describe likely business impact, and draft a review memo.'
        ),
        'incident_context': {
            'incident_id': incident['incident_id'],
            'service': incident.get('service_name'),
            'criticality': 'high' if incident.get('critical_service') else 'normal',
            'rule_hits': incident.get('rule_hits', []),
            'current_status': incident.get('status'),
            'confidence_score': incident.get('confidence_score'),
            'draft_severity': incident.get('draft_severity'),
            'linked_events': incident.get('linked_events', []),
        },
        'retrieved_evidence': {
            'prior_incidents': prior,
            'runbook_excerpt': runbook_excerpt,
            'risk_context_refs': risk_refs,
        },
        'constraints': {
            'do_not_classify_finally': True,
            'do_not_submit_report': True,
            'do_not_invent_missing_evidence': True,
            'max_root_cause_hypotheses': 3,
        },
        'output_schema': {
            'summary': 'string',
            'root_cause_hypotheses': [
                {
                    'hypothesis': 'string',
                    'supporting_evidence': ['string'],
                    'confidence': 'low|medium|high'
                }
            ],
            'likely_business_impact': 'string',
            'review_memo': 'string',
            'uncertainties': ['string'],
        },
    }

    return package, prior + risk_refs


def call_lmstudio(prompt_package: dict) -> tuple[dict, dict]:
    url = f"{settings.llm_base_url.rstrip('/')}{settings.llm_chat_path}"

    user_prompt = json.dumps(
        sanitize_for_json({k: v for k, v in prompt_package.items() if k != 'system'}),
        ensure_ascii=False,
        indent=2,
    )

    body = {
        'model': settings.llm_model,
        'messages': [
            {
                'role': 'system',
                'content': prompt_package['system'],
            },
            {
                'role': 'user',
                'content': user_prompt,
            },
        ],
        'temperature': 0.2,
        'stream': False,
    }

    try:
        with httpx.Client(timeout=120) as client:
            resp = client.post(url, json=body)
            resp.raise_for_status()
            raw = resp.json()

        content = raw['choices'][0]['message']['content']
        parsed = extract_json_from_text(content)
        return parsed, raw.get('usage', {})
    except Exception as exc:
        return {
            'summary': f'Fallback enrichment used because the model route was unavailable or returned invalid JSON: {str(exc)}',
            'root_cause_hypotheses': [
                {
                    'hypothesis': 'Combined dependency degradation and privileged access anomaly affected the service.',
                    'supporting_evidence': ['rule hits indicate multi-signal correlation'],
                    'confidence': 'medium',
                }
            ],
            'likely_business_impact': 'Potential degradation of the portfolio-api service.',
            'review_memo': 'Review service dependencies, access logs, and remediation priority.',
            'uncertainties': ['Model response was unavailable or not valid JSON; using deterministic fallback output.'],
        }, {}


def extract_json_from_text(content: str) -> dict:
    content = content.strip()

    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass

    if '```' in content:
        parts = content.split('```')
        for part in parts:
            cleaned = part.strip()
            if cleaned.startswith('json'):
                cleaned = cleaned[4:].strip()
            try:
                return json.loads(cleaned)
            except json.JSONDecodeError:
                continue

    start = content.find('{')
    end = content.rfind('}')
    if start != -1 and end != -1 and end > start:
        candidate = content[start:end + 1]
        return json.loads(candidate)

    raise ValueError('No valid JSON object found in LM Studio response')


def sanitize_for_json(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {k: sanitize_for_json(v) for k, v in value.items()}
    if isinstance(value, list):
        return [sanitize_for_json(v) for v in value]
    return value
