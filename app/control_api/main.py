from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from app.common.config import get_settings
from app.common.repository import (
    get_incident,
    insert_review_action,
    list_ai_responses_for_incident,
    list_incidents,
    list_review_actions,
    update_incident_status,
)
from app.common.schemas import IncidentDecisionRequest, IncidentStatusUpdateRequest

app = FastAPI(title='Resilience Control API', version='0.3.0')
settings = get_settings()
STATIC_DIR = Path(__file__).resolve().parent / 'static'

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


@app.get('/incidents')
def incidents() -> list[dict]:
    return list_incidents()


@app.get('/incidents/{incident_id}')
def incident_detail(incident_id: str) -> dict:
    incident = get_incident(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail='Incident not found')
    incident['ai_enrichments'] = list_ai_responses_for_incident(incident_id)
    incident['review_actions'] = list_review_actions(incident_id)
    return incident


@app.post('/incidents/{incident_id}/review')
def review_incident(incident_id: str, request: IncidentDecisionRequest) -> dict[str, str]:
    if not get_incident(incident_id):
        raise HTTPException(status_code=404, detail='Incident not found')
    insert_review_action(incident_id, request.action_type, request.actor, request.notes, request.payload)
    return {'status': 'recorded'}


@app.post('/incidents/{incident_id}/status')
def update_status(incident_id: str, request: IncidentStatusUpdateRequest) -> dict[str, str]:
    incident = get_incident(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail='Incident not found')
    update_incident_status(incident_id, request.status, request.decision_payload)
    action_payload = {'status': request.status, **request.decision_payload}
    action_payload['old_status'] = incident.get('status')
    action_payload['new_status'] = request.status
    insert_review_action(
        incident_id,
        action_type=f'status_change:{request.status}',
        actor=request.actor,
        notes=request.notes,
        payload=action_payload,
    )
    return {'status': 'updated'}


@app.post('/incidents/{incident_id}/enrich')
def enrich_incident(incident_id: str) -> dict:
    if not get_incident(incident_id):
        raise HTTPException(status_code=404, detail='Incident not found')
    try:
        with httpx.Client(timeout=180) as client:
            response = client.post(f'{settings.intelligence_service_url}/incidents/{incident_id}/enrich')
            response.raise_for_status()
            return response.json()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f'Intelligence service call failed: {exc}') from exc


@app.get('/ui')
def review_ui() -> FileResponse:
    return FileResponse(STATIC_DIR / 'index.html')
