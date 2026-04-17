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
    insert_audit_event,
    insert_remediation_action,
    insert_review_action,
    list_ai_responses_for_incident,
    list_audit_events,
    list_incidents,
    list_remediation_actions,
    list_review_actions,
    update_incident_status,
    update_remediation_action,
)
from app.common.schemas import IncidentDecisionRequest, IncidentStatusUpdateRequest, RemediationCreateRequest, RemediationUpdateRequest
from app.control_api.incident_pack import assemble_incident_pack, fetch_incident_pack
from app.control_api.reporting import assemble_report_draft, fetch_report_draft
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


@app.get('/incidents/{incident_id}/audit')
def get_audit_log(incident_id: str) -> list[dict]:
    if not get_incident(incident_id):
        raise HTTPException(status_code=404, detail='Incident not found')
    return list_audit_events('incident', incident_id)


@app.post('/incidents/{incident_id}/review')
def review_incident(incident_id: str, request: IncidentDecisionRequest) -> dict[str, str]:
    if not get_incident(incident_id):
        raise HTTPException(status_code=404, detail='Incident not found')
    insert_review_action(incident_id, request.action_type, request.actor, request.notes, request.payload)
    try:
        insert_audit_event(
            entity_type='incident',
            entity_id=incident_id,
            action_type=f'incident.review.{request.action_type}',
            actor=request.actor,
            details={'action_type': request.action_type, 'notes': request.notes},
        )
    except Exception:
        pass
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
    try:
        insert_audit_event(
            entity_type='incident',
            entity_id=incident_id,
            action_type='incident.status_changed',
            actor=request.actor,
            details={'old_status': incident.get('status'), 'new_status': request.status, 'notes': request.notes},
        )
    except Exception:
        pass
    if request.status != 'candidate':
        try:
            assemble_incident_pack(incident_id)
        except Exception:
            pass
    return {'status': 'updated'}


@app.post('/incidents/{incident_id}/enrich')
def enrich_incident(incident_id: str) -> dict:
    if not get_incident(incident_id):
        raise HTTPException(status_code=404, detail='Incident not found')
    try:
        insert_audit_event(
            entity_type='incident',
            entity_id=incident_id,
            action_type='incident.enrichment.requested',
            actor='control-api',
            details={},
        )
    except Exception:
        pass
    try:
        with httpx.Client(timeout=180) as client:
            response = client.post(f'{settings.intelligence_service_url}/incidents/{incident_id}/enrich')
            response.raise_for_status()
            result = response.json()
        try:
            assemble_incident_pack(incident_id)
        except Exception:
            pass
        return result
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f'Intelligence service call failed: {exc}') from exc


@app.post('/incidents/{incident_id}/pack')
def generate_pack(incident_id: str) -> dict[str, str]:
    if not get_incident(incident_id):
        raise HTTPException(status_code=404, detail='Incident not found')
    try:
        ref = assemble_incident_pack(incident_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f'Pack assembly failed: {exc}') from exc
    try:
        insert_audit_event(
            entity_type='incident',
            entity_id=incident_id,
            action_type='incident.pack.generated',
            actor='control-api',
            details={'incident_pack_ref': ref},
        )
    except Exception:
        pass
    return {'incident_pack_ref': ref}


@app.get('/incidents/{incident_id}/pack')
def get_pack(incident_id: str) -> dict:
    incident = get_incident(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail='Incident not found')
    pack_ref = incident.get('incident_pack_ref')
    if not pack_ref:
        raise HTTPException(status_code=404, detail='No incident pack available — generate one first')
    try:
        return fetch_incident_pack(pack_ref)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f'Could not retrieve pack: {exc}') from exc


@app.post('/incidents/{incident_id}/remediation')
def create_remediation(incident_id: str, request: RemediationCreateRequest) -> dict:
    if not get_incident(incident_id):
        raise HTTPException(status_code=404, detail='Incident not found')
    remediation_id = insert_remediation_action(
        incident_id=incident_id,
        title=request.title,
        description=request.description,
        owner=request.owner,
        due_date=request.due_date,
        dependency_note=request.dependency_note,
    )
    try:
        insert_audit_event(
            entity_type='incident',
            entity_id=incident_id,
            action_type='incident.remediation.created',
            actor=request.owner or 'control-api',
            details={'remediation_id': remediation_id, 'title': request.title, 'owner': request.owner},
        )
    except Exception:
        pass
    return {'remediation_id': remediation_id, 'status': 'open'}


@app.get('/incidents/{incident_id}/remediation')
def get_remediation(incident_id: str) -> list[dict]:
    if not get_incident(incident_id):
        raise HTTPException(status_code=404, detail='Incident not found')
    return list_remediation_actions(incident_id)


@app.patch('/incidents/{incident_id}/remediation/{remediation_id}')
def patch_remediation(incident_id: str, remediation_id: str, request: RemediationUpdateRequest) -> dict[str, str]:
    if not get_incident(incident_id):
        raise HTTPException(status_code=404, detail='Incident not found')
    update_remediation_action(
        remediation_id=remediation_id,
        status=request.status,
        closure_evidence_ref=request.closure_evidence_ref,
        lessons_learned=request.lessons_learned,
    )
    try:
        action_type = 'incident.remediation.closed' if request.status == 'closed' else 'incident.remediation.updated'
        insert_audit_event(
            entity_type='incident',
            entity_id=incident_id,
            action_type=action_type,
            actor='control-api',
            details={
                'remediation_id': remediation_id,
                'new_status': request.status,
                'lessons_learned': request.lessons_learned,
            },
        )
    except Exception:
        pass
    return {'status': 'updated'}


@app.post('/incidents/{incident_id}/reporting/draft')
def generate_report_draft(incident_id: str) -> dict[str, str]:
    if not get_incident(incident_id):
        raise HTTPException(status_code=404, detail='Incident not found')
    try:
        ref = assemble_report_draft(incident_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f'Report draft assembly failed: {exc}') from exc
    try:
        insert_audit_event(
            entity_type='incident',
            entity_id=incident_id,
            action_type='incident.report_draft.generated',
            actor='control-api',
            details={'reporting_pack_ref': ref},
        )
    except Exception:
        pass
    return {'reporting_pack_ref': ref}


@app.get('/incidents/{incident_id}/reporting/draft')
def get_report_draft(incident_id: str) -> dict:
    incident = get_incident(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail='Incident not found')
    reporting_pack_ref = incident.get('reporting_pack_ref')
    if not reporting_pack_ref:
        raise HTTPException(status_code=404, detail='No report draft available — generate one first')
    try:
        return fetch_report_draft(reporting_pack_ref)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f'Could not retrieve report draft: {exc}') from exc


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
                  <button class="secondary" onclick="generatePack()">Generate pack</button>
                  <button class="secondary" onclick="generateReportDraft()">Generate report draft</button>
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
              <h4>Incident pack</h4>
              <div id="packSection">
                <div class="muted">No pack generated yet. Click <strong>Generate pack</strong> to assemble a decision-ready dossier.</div>
              </div>
              <h4>Report draft</h4>
              <div id="reportSection">
                <div class="muted">No report draft yet. Click <strong>Generate report draft</strong> to produce an AI-assisted draft for human review.</div>
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
              <hr />
              <h4>Remediation actions</h4>
              <div id="remediationList"><div class="muted">No remediation actions yet.</div></div>
              <h5 style="margin:12px 0 4px 0">Add remediation action</h5>
              <label>Title</label>
              <input id="remTitle" placeholder="e.g. Rotate compromised credentials" />
              <label>Owner</label>
              <input id="remOwner" placeholder="e.g. security_team" />
              <label>Due date</label>
              <input id="remDue" type="date" />
              <label>Description</label>
              <textarea id="remDesc" rows="2"></textarea>
              <label>Dependency note</label>
              <input id="remDep" placeholder="e.g. Requires change window approval" />
              <button onclick="addRemediation()">Add action</button>
              <hr />
              <h4>Audit log</h4>
              <div id="auditLog"><div class="muted">No audit events yet.</div></div>
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
          await Promise.all([loadRemediation(id), loadPack(id), loadReportDraft(id), loadAuditLog(id)]);
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
          await Promise.all([
            loadIncident(currentIncidentId),
            loadAuditLog(currentIncidentId),
          ]);
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
          await Promise.all([
            loadIncident(currentIncidentId),
            loadAuditLog(currentIncidentId),
          ]);
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
          await Promise.all([
            loadIncident(currentIncidentId),
            loadAuditLog(currentIncidentId),
          ]);
        }

        async function loadRemediation(incidentId) {
          const res = await fetch(`${baseUrl}/incidents/${incidentId}/remediation`);
          const items = await res.json();
          const container = document.getElementById('remediationList');
          if (!items.length) {
            container.innerHTML = '<div class="muted">No remediation actions yet.</div>';
            return;
          }
          const statusColor = { open: '#a35f15', in_progress: '#1f3b73', closed: '#2a7a3b' };
          container.innerHTML = items.map(r => `
            <div style="border:1px solid #ddd;border-radius:8px;padding:10px;margin-bottom:8px;">
              <div style="display:flex;justify-content:space-between;align-items:center;">
                <strong>${escapeHtml(r.title)}</strong>
                <span class="pill" style="background:${statusColor[r.status]||'#666'};color:#fff">${r.status}</span>
              </div>
              ${r.owner ? `<div class="muted">Owner: ${escapeHtml(r.owner)}</div>` : ''}
              ${r.due_date ? `<div class="muted">Due: ${escapeHtml(r.due_date)}</div>` : ''}
              ${r.description ? `<div style="margin-top:4px">${escapeHtml(r.description)}</div>` : ''}
              ${r.dependency_note ? `<div class="muted">Dependency: ${escapeHtml(r.dependency_note)}</div>` : ''}
              ${r.lessons_learned ? `<div style="margin-top:4px;font-style:italic">${escapeHtml(r.lessons_learned)}</div>` : ''}
              <div style="margin-top:8px;display:flex;gap:6px;flex-wrap:wrap;">
                ${r.status === 'open' ? `<button onclick="advanceRemediation('${r.remediation_id}','in_progress')">Mark in progress</button>` : ''}
                ${r.status !== 'closed' ? `<button class="secondary" onclick="closeRemediation('${r.remediation_id}')">Mark closed</button>` : ''}
              </div>
            </div>
          `).join('');
        }

        async function addRemediation() {
          if (!currentIncidentId) return;
          const title = document.getElementById('remTitle').value.trim();
          if (!title) return;
          await fetch(`${baseUrl}/incidents/${currentIncidentId}/remediation`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
              title,
              owner: document.getElementById('remOwner').value.trim() || null,
              due_date: document.getElementById('remDue').value || null,
              description: document.getElementById('remDesc').value.trim() || null,
              dependency_note: document.getElementById('remDep').value.trim() || null,
            })
          });
          document.getElementById('remTitle').value = '';
          document.getElementById('remOwner').value = '';
          document.getElementById('remDue').value = '';
          document.getElementById('remDesc').value = '';
          document.getElementById('remDep').value = '';
          await loadRemediation(currentIncidentId);
        }

        async function advanceRemediation(remediationId, status) {
          if (!currentIncidentId) return;
          await fetch(`${baseUrl}/incidents/${currentIncidentId}/remediation/${remediationId}`, {
            method: 'PATCH',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ status })
          });
          await loadRemediation(currentIncidentId);
        }

        async function closeRemediation(remediationId) {
          const lessons = prompt('Lessons learned (optional):') || '';
          await fetch(`${baseUrl}/incidents/${currentIncidentId}/remediation/${remediationId}`, {
            method: 'PATCH',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ status: 'closed', lessons_learned: lessons || null })
          });
          await loadRemediation(currentIncidentId);
        }

        async function generatePack() {
          if (!currentIncidentId) return;
          const section = document.getElementById('packSection');
          section.innerHTML = '<div class="muted">Assembling pack…</div>';
          const res = await fetch(`${baseUrl}/incidents/${currentIncidentId}/pack`, { method: 'POST' });
          if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            section.innerHTML = `<div class="error">Pack assembly failed: ${escapeHtml(err.detail || res.statusText)}</div>`;
            return;
          }
          await loadPack(currentIncidentId);
        }

        async function loadPack(incidentId) {
          const section = document.getElementById('packSection');
          const res = await fetch(`${baseUrl}/incidents/${incidentId}/pack`);
          if (res.status === 404) {
            section.innerHTML = '<div class="muted">No pack generated yet. Click <strong>Generate pack</strong> to assemble a decision-ready dossier.</div>';
            return;
          }
          if (!res.ok) {
            section.innerHTML = '<div class="error">Could not load pack.</div>';
            return;
          }
          const pack = await res.json();
          const ai = pack.ai_enrichment;
          section.innerHTML = `
            <div style="background:#f0f4ff;border:1px solid #c5d3f5;border-radius:8px;padding:12px;margin-bottom:8px;">
              <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
                <span class="muted">Pack v${escapeHtml(pack.pack_version)} · generated ${escapeHtml(pack.generated_at)}</span>
                <button class="secondary" onclick="togglePackJson()">Toggle raw JSON</button>
              </div>
              ${ai ? `
              <div class="summary" style="margin-bottom:8px;">
                <strong>Summary</strong><br>${escapeHtml(ai.summary || '')}
              </div>
              <div style="margin-bottom:8px;">
                <strong>Likely business impact</strong><br>${escapeHtml(ai.likely_business_impact || '')}
              </div>
              ${(ai.root_cause_hypotheses || []).length ? `
              <div style="margin-bottom:8px;">
                <strong>Root-cause hypotheses</strong>
                ${ai.root_cause_hypotheses.map(h => `
                  <div style="margin-top:4px;padding:6px;background:#fff;border-radius:6px;border:1px solid #ddd;">
                    <span class="pill" style="background:#1f3b73;color:#fff">${escapeHtml(h.confidence)}</span>
                    ${escapeHtml(h.hypothesis)}
                  </div>`).join('')}
              </div>` : ''}
              ${(ai.uncertainties || []).length ? `
              <div style="margin-bottom:8px;">
                <strong>Uncertainties</strong>
                <ul style="margin:4px 0 0 16px">${ai.uncertainties.map(u => `<li>${escapeHtml(u)}</li>`).join('')}</ul>
              </div>` : ''}
              <div>
                <strong>Review memo</strong><br>${escapeHtml(ai.review_memo || '')}
              </div>` : '<div class="muted">No AI enrichment in this pack.</div>'}
              <div style="margin-top:10px;font-size:12px;color:#555">
                Events: ${pack.event_timeline.length} ·
                Review actions: ${pack.review_actions.length} ·
                Remediation actions: ${pack.remediation_actions.length}
              </div>
            </div>
            <div id="packJsonBlock" style="display:none"><pre>${escapeHtml(JSON.stringify(pack, null, 2))}</pre></div>
          `;
        }

        function togglePackJson() {
          const el = document.getElementById('packJsonBlock');
          if (el) el.style.display = el.style.display === 'none' ? 'block' : 'none';
        }

        async function generateReportDraft() {
          if (!currentIncidentId) return;
          const section = document.getElementById('reportSection');
          section.innerHTML = '<div class="muted">Assembling report draft…</div>';
          const res = await fetch(`${baseUrl}/incidents/${currentIncidentId}/reporting/draft`, { method: 'POST' });
          if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            section.innerHTML = `<div class="error">Report draft failed: ${escapeHtml(err.detail || res.statusText)}</div>`;
            return;
          }
          await Promise.all([loadReportDraft(currentIncidentId), loadAuditLog(currentIncidentId)]);
        }

        async function loadReportDraft(incidentId) {
          const section = document.getElementById('reportSection');
          const res = await fetch(`${baseUrl}/incidents/${incidentId}/reporting/draft`);
          if (res.status === 404) {
            section.innerHTML = '<div class="muted">No report draft yet. Click <strong>Generate report draft</strong> to produce an AI-assisted draft for human review.</div>';
            return;
          }
          if (!res.ok) {
            section.innerHTML = '<div class="error">Could not load report draft.</div>';
            return;
          }
          const report = await res.json();
          const cs = report.classification_state || {};
          const narrative = report.ai_draft_narrative || {};
          const openItems = report.open_items || [];
          const pending = report.pending_approvals || [];
          const remSummary = report.remediation_summary || {};

          const severityColor = { critical: '#b00020', high: '#a35f15', medium: '#1f3b73', low: '#2a7a3b' };
          const sev = (cs.draft_severity || '').toLowerCase();
          const sevBadge = `<span class="pill" style="background:${severityColor[sev]||'#666'};color:#fff">${escapeHtml(cs.draft_severity || 'unknown')}</span>`;

          section.innerHTML = `
            <div style="border:2px solid #f5a623;border-radius:8px;padding:12px;margin-bottom:8px;background:#fffbf0;">
              <div style="background:#f5a623;color:#fff;border-radius:4px;padding:4px 8px;font-size:12px;font-weight:700;margin-bottom:10px;">
                ⚠ ${escapeHtml(report.disclaimer)}
              </div>
              <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
                <span class="muted">${escapeHtml(report.report_version)} · generated ${escapeHtml(report.generated_at)}</span>
                <button class="secondary" onclick="toggleReportJson()">Toggle raw JSON</button>
              </div>

              <div class="grid2" style="margin-bottom:10px;">
                <div>
                  <strong>Classification state</strong>
                  <div style="margin-top:4px">${sevBadge} ${escapeHtml(cs.reportable_determination || '')}</div>
                  ${cs.final_classification ? `<div class="muted">Classification: ${escapeHtml(cs.final_classification)}</div>` : ''}
                </div>
                <div>
                  <strong>Remediation</strong>
                  <div class="muted" style="margin-top:4px">
                    Open: ${remSummary.open || 0} · In progress: ${remSummary.in_progress || 0} · Closed: ${remSummary.closed || 0}
                  </div>
                </div>
              </div>

              ${narrative.summary ? `
              <div style="margin-bottom:8px;">
                <strong>Draft narrative summary</strong>
                <div style="margin-top:4px;font-style:italic">${escapeHtml(narrative.summary)}</div>
              </div>` : ''}

              ${narrative.likely_business_impact ? `
              <div style="margin-bottom:8px;">
                <strong>Likely business impact</strong>
                <div style="margin-top:4px">${escapeHtml(narrative.likely_business_impact)}</div>
              </div>` : ''}

              ${openItems.length ? `
              <div style="margin-bottom:8px;">
                <strong>Open items requiring resolution before submission</strong>
                <ul style="margin:4px 0 0 16px;color:#a35f15">
                  ${openItems.map(i => `<li>${escapeHtml(i)}</li>`).join('')}
                </ul>
              </div>` : ''}

              ${pending.length ? `
              <div>
                <strong>Pending approvals</strong>
                <ul style="margin:4px 0 0 16px;color:#1f3b73">
                  ${pending.map(p => `<li>${escapeHtml(p)}</li>`).join('')}
                </ul>
              </div>` : ''}
            </div>
            <div id="reportJsonBlock" style="display:none"><pre>${escapeHtml(JSON.stringify(report, null, 2))}</pre></div>
          `;
        }

        function toggleReportJson() {
          const el = document.getElementById('reportJsonBlock');
          if (el) el.style.display = el.style.display === 'none' ? 'block' : 'none';
        }

        async function loadAuditLog(incidentId) {
          const container = document.getElementById('auditLog');
          const res = await fetch(`${baseUrl}/incidents/${incidentId}/audit`);
          const events = await res.json();
          if (!events.length) {
            container.innerHTML = '<div class="muted">No audit events yet.</div>';
            return;
          }
          const iconMap = {
            'incident.created': '🔵',
            'incident.enrichment.requested': '🟡',
            'incident.enrichment.completed': '🟢',
            'incident.status_changed': '🔄',
            'incident.pack.generated': '📦',
            'incident.remediation.created': '🛠',
            'incident.remediation.updated': '🔧',
            'incident.remediation.closed': '✅',
          };
          container.innerHTML = `
            <div style="position:relative;padding-left:18px;border-left:2px solid #dde3f0;margin-left:6px">
              ${events.map(e => {
                const icon = iconMap[e.action_type] || '•';
                const details = e.details && Object.keys(e.details).length
                  ? `<div style="font-size:11px;color:#555;margin-top:2px">${escapeHtml(JSON.stringify(e.details))}</div>`
                  : '';
                return `
                  <div style="margin-bottom:10px;position:relative">
                    <span style="position:absolute;left:-24px;background:#fff;padding:0 2px">${icon}</span>
                    <span style="font-size:12px;font-weight:600">${escapeHtml(e.action_type)}</span>
                    <span class="muted" style="font-size:11px;margin-left:8px">${escapeHtml(e.actor || '')} · ${escapeHtml(e.created_at)}</span>
                    ${details}
                  </div>`;
              }).join('')}
            </div>`;
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
