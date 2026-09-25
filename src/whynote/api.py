"""HTTP boundary. Authentication and target ownership must be supplied by the host platform."""

import os
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from .domain import ConflictError, NotFoundError, Principal
from .store import EventStore


class TargetRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    object_type: str = Field(min_length=1)
    object_id: str = Field(min_length=1)
    object_version: str = Field(min_length=1)


class CreateAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_ref: TargetRef
    action_type: Literal["negative_feedback"]
    channel: str = Field(min_length=1)
    locale: str = Field(min_length=1)
    client_occurred_at: datetime | None = None


class AttributionAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_action: Literal[
        "reason_selected",
        "reason_confirmed",
        "reason_edited",
        "reason_declined",
        "reason_skipped",
        "attribution_invalidated",
    ]
    reason_code: str | None = None
    display_id: str | None = None
    explicit_submission: bool


Authenticate = Callable[[Request], Principal]
AuthorizeTarget = Callable[[Principal, Mapping[str, str]], bool]


def _unconfigured_identity(_: Request) -> Principal:
    raise HTTPException(503, "host platform authentication is not configured")


def _unconfigured_target(_: Principal, __: Mapping[str, str]) -> bool:
    return False


def create_app(
    db_path: str | Path = "var/whynote.db",
    authenticate: Authenticate = _unconfigured_identity,
    authorize_target: AuthorizeTarget = _unconfigured_target,
) -> FastAPI:
    store = EventStore(db_path)
    api = FastAPI(title="知因・Whynote", version="0.1.0")
    api.state.store = store

    def identity(request: Request) -> Principal:
        return authenticate(request)

    def key(value: str = Header(alias="Idempotency-Key")) -> str:
        if not value.strip() or len(value) > 200:
            raise HTTPException(400, "invalid Idempotency-Key")
        return value

    @api.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @api.post("/v1/feedback-actions", status_code=202)
    def create(
        body: CreateAction,
        principal: Principal = Depends(identity),
        idempotency_key: str = Depends(key),
    ) -> dict:
        target = body.target_ref.model_dump()
        if not authorize_target(principal, target):
            raise HTTPException(403, "target is not accessible")
        metadata = {
            "action_type": body.action_type,
            "channel": body.channel,
            "locale": body.locale,
            "client_occurred_at": body.client_occurred_at.isoformat() if body.client_occurred_at else None,
        }
        try:
            return store.create_action(principal, target, metadata, idempotency_key)
        except ConflictError as exc:
            raise HTTPException(409, str(exc)) from exc

    @api.get("/v1/feedback-actions/{event_id}")
    def get(event_id: str, principal: Principal = Depends(identity)) -> dict:
        try:
            return store.get_action(principal, event_id)
        except NotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc

    @api.post("/v1/feedback-actions/{event_id}/retract")
    def retract(
        event_id: str,
        principal: Principal = Depends(identity),
        idempotency_key: str = Depends(key),
    ) -> dict:
        try:
            return store.retract_action(principal, event_id, idempotency_key)
        except NotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ConflictError as exc:
            raise HTTPException(409, str(exc)) from exc

    @api.post("/v1/feedback-actions/{event_id}/attribution-events")
    def attribute(
        event_id: str,
        body: AttributionAction,
        principal: Principal = Depends(identity),
        idempotency_key: str = Depends(key),
    ) -> dict:
        try:
            return store.record_user_action(
                principal,
                event_id,
                body.user_action,
                body.reason_code,
                body.display_id,
                body.explicit_submission,
                idempotency_key,
            )
        except NotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ConflictError as exc:
            raise HTTPException(409, str(exc)) from exc

    return api


app = create_app(os.environ.get("WHYNOTE_DB", "var/whynote.db"))
