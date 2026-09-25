"""HTTP boundary. Authentication and target ownership must be supplied by the host platform."""

import os
import re
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator

from .domain import ConflictError, NotFoundError, Principal
from .store import EventStore

_RFC3339 = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$")


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
    client_occurred_at: str | None = None

    @field_validator("client_occurred_at", mode="before")
    @classmethod
    def require_utc_time(cls, value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or not _RFC3339.fullmatch(value):
            raise ValueError("client_occurred_at must be RFC 3339 with a timezone")
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.utcoffset() is None:
            raise ValueError("client_occurred_at must include a timezone")
        return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


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
    display_id: str = Field(min_length=1)
    explicit_submission: StrictBool


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

    def require_current_target(principal: Principal, event_id: str) -> None:
        target = store.get_target_ref(principal, event_id)
        if not authorize_target(principal, target):
            raise NotFoundError("feedback action not found")

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
            "client_occurred_at": body.client_occurred_at,
        }
        try:
            return store.create_action(principal, target, metadata, idempotency_key)
        except ConflictError as exc:
            raise HTTPException(409, str(exc)) from exc

    @api.get("/v1/feedback-actions/{event_id}")
    def get(event_id: str, principal: Principal = Depends(identity)) -> dict:
        try:
            require_current_target(principal, event_id)
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
            require_current_target(principal, event_id)
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
            require_current_target(principal, event_id)
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
