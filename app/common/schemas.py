from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class IngestEventRequest(BaseModel):
    source_system: str
    source_type: str
    payload: dict[str, Any]


class CanonicalEvent(BaseModel):
    event_id: str
    source_system: str
    source_type: str
    event_type: str
    timestamp: datetime
    severity: str
    affected_asset: str | None = None
    linked_service: str | None = None
    vendor_reference: str | None = None
    evidence_pointer: str | None = None
    enrichment_tags: list[str] = Field(default_factory=list)
    ingesting_adapter: str


class IncidentDecisionRequest(BaseModel):
    actor: str
    action_type: str
    notes: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class IncidentStatusUpdateRequest(BaseModel):
    actor: str
    status: str
    notes: str | None = None
    decision_payload: dict[str, Any] = Field(default_factory=dict)
