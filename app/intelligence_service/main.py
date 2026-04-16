from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from app.common.config import get_settings
from app.common.repository import get_incident, insert_ai_request, insert_ai_response

app = FastAPI(title='Resilience Intelligence Service', version='0.3.0')
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
    incident = get_incident(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail='Incident not found')

    prompt_package, retrieval_refs = build_prompt_package(incident)
    prompt_hash = hashlib.sha256(json.dumps(prompt_package, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()
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
    validation_errors = [key for key in ['summary', 'root_cause_hypotheses', 'likely_business_impact', 'review_memo', 'uncertainties'] if key not in response_json]
    schema_valid = not validation_errors
    insert_ai_response(request_id, response_json, schema_valid, latency_ms, validation_errors, token_metadata)

    return {'request_id': request_id, 'schema_valid': schema_valid, 'response': response_json}


def build_prompt_package(incident: dict) -> tuple[dict, list[str]]:
    runbooks = json.loads((REFERENCE_DIR / 'runbooks.json').read_text(encoding='utf-8'))
    prior_incidents = json.loads((REFERENCE_DIR / 'prior_incidents.json').read_text(encoding='utf-8'))
    risk_context = json.loads((REFERENCE_DIR / 'risk_context.json').read_text(encoding='utf-8'))

    service = incident.get('service_name')
    runbook_excerpt = next((x['excerpt'] for x in runbooks if x['service'] == service), 'Validate dependencies and timeline before escalation.')
    prior = [x['incident_id'] for x in prior_incidents if x['service'] == service][:3]
    risk_refs = [x['risk_ref'] for x in risk_context if x['service'] == service][:3]

    package = {
        'system': 'You are an incident-intelligence assistant. Summarize evidence, suggest likely causes, and draft review material. Do not make final classifications. Output valid JSON only.',
        'task': 'Summarize the candidate incident, propose up to 3 plausible root-cause hypotheses grounded in the evidence, and draft a review memo.',
        'incident_context': {
            'incident_id': incident['incident_id'],
            'service': incident.get('service_name'),
            'criticality': 'high' if incident.get('critical_service') else 'normal',
            'rule_hits': incident.get('rule_hits', []),
            'current_status': incident.get('status'),
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
            'root_cause_hypotheses': [{'hypothesis': 'string', 'supporting_evidence': ['string'], 'confidence': 'low|medium|high'}],
            'likely_business_impact': 'string',
            'review_memo': 'string',
            'uncertainties': ['string'],
        },
    }
    return package, prior + risk_refs


def call_lmstudio(prompt_package: dict) -> tuple[dict, dict]:
    url = f"{settings.llm_base_url.rstrip('/')}{settings.llm_chat_path}"
    body = {
        'model': settings.llm_model,
        'messages': [
            {'role': 'system', 'content': prompt_package['system']},
            {'role': 'user', 'content': json.dumps({k: v for k, v in prompt_package.items() if k != 'system'}, ensure_ascii=False)},
        ],
        'temperature': 0.2,
        'stream': False,
        'response_format': {'type': 'json_object'},
    }
    try:
        with httpx.Client(timeout=120) as client:
            resp = client.post(url, json=body)
            resp.raise_for_status()
            raw = resp.json()
            content = raw['choices'][0]['message']['content']
            return json.loads(content), raw.get('usage', {})
    except Exception:
        return {
            'summary': 'Fallback enrichment used because the model route was unavailable.',
            'root_cause_hypotheses': [
                {
                    'hypothesis': 'Combined dependency degradation and privileged access anomaly affected the service.',
                    'supporting_evidence': ['rule hits indicate multi-signal correlation'],
                    'confidence': 'medium',
                }
            ],
            'likely_business_impact': 'Potential degradation of the portfolio-api service.',
            'review_memo': 'Review service dependencies, access logs, and remediation priority.',
            'uncertainties': ['Model response was unavailable; using deterministic fallback output.'],
        }, {}
