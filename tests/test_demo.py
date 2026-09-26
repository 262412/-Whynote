import uuid

from fastapi.testclient import TestClient

from whynote.demo import DEMO_CODES, DEMO_TARGET, create_demo_app
from whynote.domain import Principal


def test_local_demo_click_display_select_edit_and_retract(tmp_path):
    app = create_demo_app(tmp_path / "demo.db")
    with TestClient(app) as http:
        page = http.get("/demo")
        assert page.status_code == 200
        assert page.headers["cache-control"] == "no-store"
        assert "requestAnimationFrame" in page.text
        assert "测试" in page.text

        session = {"X-Demo-Session": app.state.demo_token}
        target = {
            "target_ref": DEMO_TARGET,
            "action_type": "negative_feedback",
            "channel": "local-demo",
            "locale": "zh-CN",
        }
        assert http.post("/v1/feedback-actions", json=target).status_code == 401
        wrong_target = {**target, "target_ref": {**DEMO_TARGET, "object_id": "real-answer"}}
        assert (
            http.post(
                "/v1/feedback-actions", json=wrong_target, headers={**session, "Idempotency-Key": "wrong"}
            ).status_code
            == 403
        )

        created = http.post("/v1/feedback-actions", json=target, headers={**session, "Idempotency-Key": "click"})
        assert created.status_code == 202
        event_id = created.json()["event_id"]
        store = app.state.store
        principal = Principal("demo-tenant", "demo-user")
        display = {
            "event_id": event_id,
            "display_id": str(uuid.uuid4()),
            "mode": "manual_menu",
            "ui_version": "demo-v1",
            "shown_reason_codes": DEMO_CODES,
        }
        assert http.post("/demo/rendered-displays", json=display).status_code == 401
        assert [event["event_type"] for event in store.get_events(principal, event_id)] == [
            "negative_feedback_action_recorded"
        ]
        assert http.post("/demo/rendered-displays", json=display, headers=session).status_code == 200
        assert http.post("/demo/rendered-displays", json=display, headers=session).status_code == 200
        assert (
            http.post(
                "/demo/rendered-displays",
                json={**display, "shown_reason_codes": ["style"]},
                headers=session,
            ).status_code
            == 409
        )

        endpoint = f"/v1/feedback-actions/{event_id}"
        selected = http.post(
            f"{endpoint}/attribution-events",
            json={
                "user_action": "reason_selected",
                "reason_code": "style",
                "display_id": display["display_id"],
                "explicit_submission": True,
            },
            headers={**session, "Idempotency-Key": "select"},
        )
        assert selected.status_code == 200
        assert selected.json()["attribution_source"] == "user_manual"

        correction = {**display, "display_id": str(uuid.uuid4()), "mode": "edit_menu"}
        assert http.post("/demo/rendered-displays", json=correction, headers=session).status_code == 200
        edited = http.post(
            f"{endpoint}/attribution-events",
            json={
                "user_action": "reason_edited",
                "reason_code": "irrelevant",
                "display_id": correction["display_id"],
                "explicit_submission": True,
            },
            headers={**session, "Idempotency-Key": "edit"},
        )
        assert edited.status_code == 200
        assert edited.json()["reason_code"] == "irrelevant"
        assert edited.json()["attribution_source"] == "user_edited"

        retracted = http.post(f"{endpoint}/retract", headers={**session, "Idempotency-Key": "retract"})
        assert retracted.status_code == 200
        assert retracted.json()["attribution_status"] == "none"
        assert retracted.json()["reason_code"] is None
        assert (
            http.post(
                "/demo/rendered-displays", json={**correction, "display_id": str(uuid.uuid4())}, headers=session
            ).status_code
            == 409
        )
        events = store.get_events(principal, event_id)
        assert [event["event_type"] for event in events] == [
            "negative_feedback_action_recorded",
            "reason_displayed",
            "reason_selected",
            "reason_displayed",
            "reason_edited",
            "action_retracted",
        ]
        assert all("X-Demo-Session" not in str(event) for event in events)


def test_local_demo_rejects_foreign_or_unshown_display(tmp_path):
    app = create_demo_app(tmp_path / "demo.db")
    with TestClient(app, client=("192.0.2.10", 12345)) as remote:
        assert remote.get("/demo").status_code == 403
        assert (
            remote.post(
                "/v1/feedback-actions",
                json={
                    "target_ref": DEMO_TARGET,
                    "action_type": "negative_feedback",
                    "channel": "local-demo",
                    "locale": "zh-CN",
                },
                headers={"X-Demo-Session": app.state.demo_token, "Idempotency-Key": "remote"},
            ).status_code
            == 403
        )
    with TestClient(app) as http:
        session = {"X-Demo-Session": app.state.demo_token}
        assert (
            http.post(
                "/demo/rendered-displays",
                json={
                    "event_id": str(uuid.uuid4()),
                    "display_id": str(uuid.uuid4()),
                    "mode": "manual_menu",
                    "ui_version": "demo-v1",
                    "shown_reason_codes": DEMO_CODES,
                },
                headers=session,
            ).status_code
            == 404
        )
