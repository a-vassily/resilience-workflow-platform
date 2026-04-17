from __future__ import annotations

import json
from textwrap import dedent

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from app.common.config import get_settings
from app.common.repository import (
    get_incident,
    get_latest_ai_response_for_incident,
    insert_review_action,
    list_ai_responses_for_incident,
    list_incidents,
    list_review_actions,
    update_incident_status,
)
from app.common.schemas import IncidentDecisionRequest, IncidentStatusUpdateRequest
from app.demo_tools.reset_seed import replay_demo

app = FastAPI(title='Resilience Control API', version='0.5.0')
settings = get_settings()

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
    items = list_incidents()
    for item in items:
        latest = get_latest_ai_response_for_incident(item['incident_id'])
        item['latest_ai_enrichment'] = latest
    return items


@app.get('/incidents/{incident_id}')
def incident_detail(incident_id: str) -> dict:
    incident = get_incident(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail='Incident not found')
    incident['ai_enrichments'] = list_ai_responses_for_incident(incident_id)
    incident['latest_ai_enrichment'] = incident['ai_enrichments'][0] if incident['ai_enrichments'] else None
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


@app.post('/demo/reset-seed')
def demo_reset_seed(enrich: bool = False) -> dict:
    return replay_demo(enrich=enrich)


@app.get('/ui', response_class=HTMLResponse)
def review_ui() -> str:
    return dedent(
        """
        <!doctype html>
        <html lang="en">
        <head>
          <meta charset="utf-8" />
          <title>Resilience Review UI</title>
          <meta name="viewport" content="width=device-width, initial-scale=1" />
          <style>
            body { font-family: Arial, sans-serif; margin: 0; background: #f6f7fb; color: #1a1a1a; }
            header { background: #1f3b73; color: white; padding: 16px 24px; }
            main { display: grid; grid-template-columns: 360px 1fr; gap: 16px; padding: 16px; }
            .panel { background: white; border-radius: 12px; box-shadow: 0 1px 6px rgba(0,0,0,.08); padding: 16px; }
            .incident { padding: 10px; border: 1px solid #ddd; border-radius: 8px; margin-bottom: 10px; cursor: pointer; }
            .incident:hover, .incident.active { border-color: #1f3b73; background: #eef3ff; }
            .pill { display: inline-block; padding: 2px 8px; border-radius: 999px; background: #eef; margin-right: 6px; margin-bottom: 6px; font-size: 12px; }
            button { background: #1f3b73; color: white; border: none; border-radius: 8px; padding: 8px 12px; cursor: pointer; margin-right: 8px; }
            button.secondary { background: #5a6473; }
            button.warn { background: #a35f15; }
            input, textarea, select { width: 100%; margin-top: 6px; margin-bottom: 10px; padding: 8px; border-radius: 8px; border: 1px solid #ccc; box-sizing: border-box; }
            pre { white-space: pre-wrap; background: #fafafa; padding: 12px; border-radius: 8px; overflow: auto; }
            .muted { color: #666; }
            .grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
            .toolbar { display:flex; gap:8px; flex-wrap:wrap; align-items:center; }
            .summary { background:#f7fbff; border:1px solid #d7e6ff; border-radius:8px; padding:10px; margin-bottom:10px; }
            .error { color:#b00020; margin-top:8px; }
          </style>
        </head>
        <body>
        <header>
          <h2 style="margin:0">Resilience Review UI</h2>
          <div style="margin-top:6px; opacity:.9">Single-page review of incident state + latest enrichment, with one-click enrichment and demo replay.</div>
        </header>
        <main>
          <section class="panel">
            <div class="toolbar">
              <h3 style="margin:0; flex:1">Incidents</h3>
              <button onclick="loadIncidents()">Refresh</button>
              <button class="warn" onclick="resetDemo(false)">Reset & seed</button>
              <button class="warn" onclick="resetDemo(true)">Reset, seed & enrich</button>
            </div>
            <div id="incidentList" style="margin-top:12px"></div>
            <div id="listError" class="error"></div>
          </section>
          <section class="panel">
            <div id="detailEmpty" class="muted">Select an incident to inspect it.</div>
            <div id="detail" style="display:none">
              <div class="toolbar">
                <div style="flex:1">
                  <h3 id="incidentTitle" style="margin:0 0 8px 0"></h3>
                  <div id="incidentMeta" class="muted"></div>
                </div>
                <div>
                  <button onclick="enrichCurrent()">Run AI enrichment</button>
                </div>
              </div>
              <hr />
              <div class="grid2">
                <div>
                  <h4>Incident</h4>
                  <div id="ruleHits"></div>
                  <pre id="incidentSnapshot"></pre>
                </div>
                <div>
                  <h4>Latest enrichment</h4>
                  <div id="latestEnrichmentMeta" class="muted"></div>
                  <div id="latestEnrichment"></div>
                </div>
              </div>
              <div class="grid2">
                <div>
                  <h4>Linked events</h4>
                  <div id="eventRefs"></div>
                </div>
                <div>
                  <h4>Business context</h4>
                  <pre id="businessContext"></pre>
                </div>
              </div>
              <h4>All enrichments</h4>
              <div id="aiEnrichments"></div>
              <h4>Review actions</h4>
              <div id="reviewActions"></div>
              <h4>Record review action</h4>
              <label>Actor</label>
              <input id="actor" value="incident_commander" />
              <label>Action type</label>
              <select id="actionType">
                <option value="review_note">review_note</option>
                <option value="classification_recommendation">classification_recommendation</option>
                <option value="escalation_note">escalation_note</option>
              </select>
              <label>Notes</label>
              <textarea id="notes" rows="4"></textarea>
              <button onclick="saveReview()">Save review action</button>
              <hr />
              <h4>Change incident status</h4>
              <label>New status</label>
              <select id="statusSelect">
                <option value="candidate">candidate</option>
                <option value="triage_pending">triage_pending</option>
                <option value="under_review">under_review</option>
                <option value="classified_internal">classified_internal</option>
                <option value="classified_reportable">classified_reportable</option>
                <option value="remediation_open">remediation_open</option>
                <option value="closed">closed</option>
              </select>
              <label>Status notes</label>
              <textarea id="statusNotes" rows="3"></textarea>
              <button class="secondary" onclick="changeStatus()">Update status</button>
            </div>
          </section>
        </main>
        <script>
        const baseUrl = '';
        let currentIncidentId = null;

        async function loadIncidents() {
          try {
            const res = await fetch(`${baseUrl}/incidents`);
            const items = await res.json();
            const list = document.getElementById('incidentList');
            list.innerHTML = '';
            document.getElementById('listError').textContent = '';
            if (!items.length) {
              list.innerHTML = '<div class="muted">No incidents yet. Use Reset & seed to replay the full scenario.</div>';
              return;
            }
            for (const inc of items) {
              const latest = inc.latest_ai_enrichment?.response_body?.summary || 'No enrichment yet';
              const div = document.createElement('div');
              div.className = 'incident' + (inc.incident_id === currentIncidentId ? ' active' : '');
              div.innerHTML = `<strong>${inc.incident_id}</strong><br><span class="muted">${inc.service_name || 'n/a'} · ${inc.status} · confidence ${inc.confidence_score}</span><div class="summary">${escapeHtml(latest)}</div>`;
              div.onclick = () => loadIncident(inc.incident_id);
              list.appendChild(div);
            }
          } catch (err) {
            document.getElementById('listError').textContent = err;
          }
        }

        async function loadIncident(id) {
          currentIncidentId = id;
          await loadIncidents();
          const res = await fetch(`${baseUrl}/incidents/${id}`);
          const inc = await res.json();
          document.getElementById('detailEmpty').style.display = 'none';
          document.getElementById('detail').style.display = 'block';
          document.getElementById('incidentTitle').textContent = `${inc.incident_id} — ${inc.service_name || 'unknown service'}`;
          document.getElementById('incidentMeta').textContent = `status: ${inc.status} | severity: ${inc.draft_severity || 'n/a'} | owner: ${inc.owner || 'n/a'} | latest enrichment ref: ${inc.enrichment_ref || 'none'}`;
          document.getElementById('ruleHits').innerHTML = (inc.rule_hits || []).map(x => `<span class="pill">${x}</span>`).join('');
          document.getElementById('eventRefs').innerHTML = (inc.linked_events || []).map(x => `<span class="pill">${x.event_id} · ${x.event_type}</span>`).join('');
          document.getElementById('businessContext').textContent = JSON.stringify(inc.business_context || {}, null, 2);
          document.getElementById('incidentSnapshot').textContent = JSON.stringify({
            incident_id: inc.incident_id,
            status: inc.status,
            confidence_score: inc.confidence_score,
            service_name: inc.service_name,
            threshold_flags: inc.threshold_flags,
            source_event_refs: inc.source_event_refs,
            workflow_state: inc.workflow_state,
          }, null, 2);
          const latest = inc.latest_ai_enrichment;
          document.getElementById('latestEnrichmentMeta').textContent = latest ? `model: ${latest.model_id} | route: ${latest.route_used} | requested_at: ${latest.requested_at}` : 'No enrichment yet.';
          document.getElementById('latestEnrichment').innerHTML = latest ? `<pre>${escapeHtml(JSON.stringify(latest.response_body, null, 2))}</pre>` : '<div class="muted">No enrichment yet.</div>';
          document.getElementById('aiEnrichments').innerHTML = renderEnrichments(inc.ai_enrichments || []);
          document.getElementById('reviewActions').innerHTML = renderReviews(inc.review_actions || []);
          document.getElementById('statusSelect').value = inc.status;
        }

        function renderEnrichments(items) {
          if (!items.length) return '<div class="muted">No enrichment yet.</div>';
          return items.map(x => `<pre>${escapeHtml(JSON.stringify({request_id:x.request_id, model_id:x.model_id, requested_at:x.requested_at, schema_valid:x.schema_valid, response_body:x.response_body}, null, 2))}</pre>`).join('');
        }

        function renderReviews(items) {
          if (!items.length) return '<div class="muted">No review actions yet.</div>';
          return items.map(x => `<pre>${escapeHtml(JSON.stringify(x, null, 2))}</pre>`).join('');
        }

        async function enrichCurrent() {
          if (!currentIncidentId) return;
          await fetch(`${baseUrl}/incidents/${currentIncidentId}/enrich`, { method: 'POST' });
          await loadIncident(currentIncidentId);
        }

        async function saveReview() {
          if (!currentIncidentId) return;
          await fetch(`${baseUrl}/incidents/${currentIncidentId}/review`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
              actor: document.getElementById('actor').value,
              action_type: document.getElementById('actionType').value,
              notes: document.getElementById('notes').value,
              payload: {}
            })
          });
          document.getElementById('notes').value = '';
          await loadIncident(currentIncidentId);
        }

        async function changeStatus() {
          if (!currentIncidentId) return;
          await fetch(`${baseUrl}/incidents/${currentIncidentId}/status`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
              actor: document.getElementById('actor').value,
              status: document.getElementById('statusSelect').value,
              notes: document.getElementById('statusNotes').value,
              decision_payload: {}
            })
          });
          document.getElementById('statusNotes').value = '';
          await loadIncident(currentIncidentId);
        }

        async function resetDemo(enrich) {
          await fetch(`${baseUrl}/demo/reset-seed?enrich=${enrich ? 'true' : 'false'}`, { method: 'POST' });
          currentIncidentId = null;
          document.getElementById('detail').style.display = 'none';
          document.getElementById('detailEmpty').style.display = 'block';
          await loadIncidents();
        }

        function escapeHtml(str) {
          return String(str).replace(/[&<>\"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
        }

        loadIncidents();
        </script>
        </body>
        </html>
        """
    )
