import uuid

from fastapi.testclient import TestClient

from whynote.demo import DEMO_CODES, DEMO_TARGET, create_demo_app


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
        principal = app.state.demo_principal
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
        accepted = http.post("/demo/rendered-displays", json=display, headers=session)
        assert accepted.status_code == 200
        assert accepted.json() == {
            "display_id": display["display_id"],
            "receipt_status": "current",
            "actionable": True,
        }
        assert http.post("/demo/rendered-displays", json=display, headers=session).json() == accepted.json()
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
        consumed = http.post("/demo/rendered-displays", json=display, headers=session)
        assert consumed.json()["receipt_status"] == "historical"
        assert consumed.json()["actionable"] is False

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
        replay = http.post("/demo/rendered-displays", json=correction, headers=session)
        assert replay.status_code == 200
        assert replay.json() == {
            "display_id": correction["display_id"],
            "receipt_status": "historical",
            "actionable": False,
        }
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


def test_old_display_receipt_is_historical_after_new_display(tmp_path):
    app = create_demo_app(tmp_path / "demo.db")
    with TestClient(app) as http:
        session = {"X-Demo-Session": app.state.demo_token}
        action = http.post(
            "/v1/feedback-actions",
            json={
                "target_ref": DEMO_TARGET,
                "action_type": "negative_feedback",
                "channel": "local-demo",
                "locale": "zh-CN",
            },
            headers={**session, "Idempotency-Key": "click"},
        )
        event_id = action.json()["event_id"]
        first = {
            "event_id": event_id,
            "display_id": str(uuid.uuid4()),
            "mode": "manual_menu",
            "ui_version": "demo-v1",
            "shown_reason_codes": DEMO_CODES,
        }
        second = {**first, "display_id": str(uuid.uuid4())}
        assert http.post("/demo/rendered-displays", json=first, headers=session).json()["actionable"] is True
        assert http.post("/demo/rendered-displays", json=second, headers=session).json()["actionable"] is True
        before = app.state.store.get_events(app.state.demo_principal, event_id)

        replay = http.post("/demo/rendered-displays", json=first, headers=session)
        assert replay.status_code == 200
        assert replay.json() == {
            "display_id": first["display_id"],
            "receipt_status": "historical",
            "actionable": False,
        }
        assert (
            http.post(
                f"/v1/feedback-actions/{event_id}/attribution-events",
                json={
                    "user_action": "reason_selected",
                    "reason_code": "style",
                    "display_id": first["display_id"],
                    "explicit_submission": True,
                },
                headers={**session, "Idempotency-Key": "old-selection"},
            ).status_code
            == 409
        )
        assert app.state.store.get_events(app.state.demo_principal, event_id) == before


def test_new_demo_instance_cannot_access_previous_instance_actions(tmp_path):
    db_path = tmp_path / "shared-demo.db"
    first_app = create_demo_app(db_path)
    with TestClient(first_app) as first:
        first_session = {"X-Demo-Session": first_app.state.demo_token}
        action = first.post(
            "/v1/feedback-actions",
            json={
                "target_ref": DEMO_TARGET,
                "action_type": "negative_feedback",
                "channel": "local-demo",
                "locale": "zh-CN",
            },
            headers={**first_session, "Idempotency-Key": "click"},
        )
        event_id = action.json()["event_id"]
        display = {
            "event_id": event_id,
            "display_id": str(uuid.uuid4()),
            "mode": "manual_menu",
            "ui_version": "demo-v1",
            "shown_reason_codes": DEMO_CODES,
        }
        assert first.post("/demo/rendered-displays", json=display, headers=first_session).status_code == 200

    second_app = create_demo_app(db_path)
    assert second_app.state.demo_principal != first_app.state.demo_principal
    with TestClient(second_app) as second:
        second_session = {"X-Demo-Session": second_app.state.demo_token}
        endpoint = f"/v1/feedback-actions/{event_id}"
        assert second.get(endpoint, headers=first_session).status_code == 401
        assert second.get(endpoint, headers=second_session).status_code == 404
        assert second.post("/demo/rendered-displays", json=display, headers=second_session).status_code == 404
        assert (
            second.post(
                f"{endpoint}/attribution-events",
                json={
                    "user_action": "reason_selected",
                    "reason_code": "style",
                    "display_id": display["display_id"],
                    "explicit_submission": True,
                },
                headers={**second_session, "Idempotency-Key": "select"},
            ).status_code
            == 404
        )
        assert (
            second.post(f"{endpoint}/retract", headers={**second_session, "Idempotency-Key": "undo"}).status_code == 404
        )

    assert [
        event["event_type"] for event in first_app.state.store.get_events(first_app.state.demo_principal, event_id)
    ] == [
        "negative_feedback_action_recorded",
        "reason_displayed",
    ]


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
