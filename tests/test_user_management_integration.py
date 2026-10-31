import json
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import text


EDIT_URL = "/admin/users/{user_id}"
STATUS_URL = "/admin/users/{user_id}/status"
PUBLIC_USER_FIELDS = {
    "id", "email", "first_name", "last_name", "middle_name",
    "study_group", "is_active", "roles",
}


def request_as(case, actor, method, path, **kwargs):
    return case.client.request(
        method, path, headers=case.actors[actor]["headers"], **kwargs
    )


def audit_events(case, actor_id, user_id):
    with case.engine.connect() as connection:
        return connection.execute(text("""
            SELECT action, actor_id, entity_type, entity_id, details
            FROM public.audit_events
            WHERE actor_id = :actor AND entity_type = 'users' AND entity_id = :user
            ORDER BY id
        """), {"actor": actor_id, "user": user_id}).mappings().all()


def user_row(case, user_id):
    with case.engine.connect() as connection:
        return connection.execute(text("""
            SELECT email, first_name, last_name, middle_name, study_group, is_active
            FROM public.users WHERE id = :id
        """), {"id": user_id}).mappings().one()


def test_account_endpoints_require_users_manage(scenario):
    user_id = scenario.actors["student"]["id"]
    edit_path = EDIT_URL.format(user_id=user_id)
    status_path = STATUS_URL.format(user_id=user_id)

    for path, method, payload in (
        (edit_path, "PATCH", {"first_name": "Updated"}),
        (status_path, "PATCH", {"is_active": True}),
    ):
        response = scenario.client.request(method, path, json=payload)
        assert response.status_code == 401
        for actor in ("student", "supervisor", "teacher", "department"):
            assert request_as(scenario, actor, method, path, json=payload).status_code == 403

    edited = request_as(
        scenario, "admin", "PATCH", edit_path, json={"first_name": "Admin edit"}
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["first_name"] == "Admin edit"
    status = request_as(
        scenario, "admin", "PATCH", status_path, json={"is_active": True}
    )
    assert status.status_code == 200, status.text


def test_disabled_admin_token_is_rejected(scenario):
    admin_id = scenario.actors["admin2"]["id"]
    response = request_as(
        scenario, "admin", "PATCH",
        STATUS_URL.format(user_id=admin_id), json={"is_active": False},
    )
    assert response.status_code == 200, response.text

    protected = request_as(
        scenario, "admin2", "PATCH",
        EDIT_URL.format(user_id=scenario.actors["student"]["id"]),
        json={"first_name": "Should not apply"},
    )
    assert protected.status_code == 401
    login = scenario.client.post("/auth/login", json={
        "email": scenario.actors["admin2"]["email"],
        "password": "integration-only-not-a-real-password",
    })
    assert login.status_code == 401


def test_partial_edit_normalizes_email_and_clears_optional_fields(scenario):
    target_id = scenario.actors["student"]["id"]
    path = EDIT_URL.format(user_id=target_id)
    before = user_row(scenario, target_id)
    response = request_as(scenario, "admin", "PATCH", path, json={
        "email": "  New.Student@Example.Invalid ",
        "middle_name": None,
        "study_group": None,
    })
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["email"] == "new.student@example.invalid"
    assert result["first_name"] == before["first_name"]
    assert result["last_name"] == before["last_name"]
    assert result["middle_name"] is None
    assert result["study_group"] is None
    assert result["roles"] == ["student"]
    assert set(result) == PUBLIC_USER_FIELDS
    assert not {"password", "password_hash", "permissions", "token"} & set(result)

    login = scenario.client.post("/auth/login", json={
        "email": " NEW.STUDENT@EXAMPLE.INVALID ",
        "password": "integration-only-not-a-real-password",
    })
    assert login.status_code == 200, login.text


def test_edit_validation_rejects_null_empty_invalid_and_unknown_fields(scenario):
    target_id = scenario.actors["student"]["id"]
    path = EDIT_URL.format(user_id=target_id)
    original = dict(user_row(scenario, target_id))
    invalid_payloads = (
        {},
        {"email": None},
        {"first_name": None},
        {"last_name": None},
        {"first_name": ""},
        {"last_name": "   "},
        {"email": ""},
        {"email": "not-an-email"},
        {"email": "x" * 250 + "@example.invalid"},
        {"first_name": "x" * 101},
        {"last_name": "x" * 101},
        {"middle_name": "x" * 101},
        {"study_group": "x" * 51},
        {"roles": ["admin"]},
        {"role_codes": ["admin"]},
        {"password": "integration-only-not-a-real-password"},
        {"password_hash": "not-a-hash"},
        {"is_active": False},
        {"unexpected": "field"},
    )
    for payload in invalid_payloads:
        response = request_as(
            scenario, "admin", "PATCH", path, json=payload
        )
        assert response.status_code == 422, (payload, response.text)
    assert dict(user_row(scenario, target_id)) == original
    assert audit_events(
        scenario, scenario.actors["admin"]["id"], target_id
    ) == []


def test_edit_email_conflict_is_case_insensitive_and_atomic(scenario):
    target_id = scenario.actors["student"]["id"]
    other_email = scenario.actors["supervisor"]["email"]
    before = dict(user_row(scenario, target_id))
    response = request_as(scenario, "admin", "PATCH", EDIT_URL.format(user_id=target_id), json={
        "first_name": "Must not persist",
        "email": other_email.upper(),
    })
    assert response.status_code == 409, response.text
    assert dict(user_row(scenario, target_id)) == before
    assert audit_events(scenario, scenario.actors["admin"]["id"], target_id) == []


def test_edit_idempotency_audit_and_inactive_accounts(scenario):
    target_id = scenario.actors["student"]["id"]
    actor_id = scenario.actors["admin"]["id"]
    path = EDIT_URL.format(user_id=target_id)
    original_email = scenario.actors["student"]["email"]

    same = request_as(scenario, "admin", "PATCH", path, json={
        "email": f" {original_email.upper()} ",
    })
    assert same.status_code == 200, same.text
    assert audit_events(scenario, actor_id, target_id) == []

    changed = request_as(scenario, "admin", "PATCH", path, json={
        "first_name": "Changed",
    })
    assert changed.status_code == 200, changed.text
    events = audit_events(scenario, actor_id, target_id)
    assert len(events) == 1
    assert events[0]["action"] == "user_updated"
    assert events[0]["details"] == {
        "changes": {"first_name": {"old": "student", "new": "Changed"}}
    }
    serialized = json.dumps(events[0]["details"]).lower()
    for forbidden in (
        "password", "password_hash", "jwt", "authorization",
        "integration-only-not-a-real-password",
    ):
        assert forbidden not in serialized

    disabled = request_as(
        scenario, "admin", "PATCH",
        STATUS_URL.format(user_id=target_id), json={"is_active": False},
    )
    assert disabled.status_code == 200, disabled.text
    edited_inactive = request_as(
        scenario, "admin", "PATCH", path, json={"last_name": "Inactive edit"}
    )
    assert edited_inactive.status_code == 200, edited_inactive.text
    assert edited_inactive.json()["last_name"] == "Inactive edit"
    enabled = request_as(
        scenario, "admin", "PATCH",
        STATUS_URL.format(user_id=target_id), json={"is_active": True},
    )
    assert enabled.status_code == 200, enabled.text


def test_user_id_must_fit_positive_bigint(scenario):
    headers = scenario.actors["admin"]["headers"]
    for user_id in (0, -1, 9_223_372_036_854_775_808):
        response = scenario.client.patch(
            EDIT_URL.format(user_id=user_id), headers=headers,
            json={"first_name": "No SQL"},
        )
        assert response.status_code == 422, response.text
        response = scenario.client.patch(
            STATUS_URL.format(user_id=user_id), headers=headers,
            json={"is_active": False},
        )
        assert response.status_code == 422, response.text

    missing = request_as(
        scenario, "admin", "PATCH",
        EDIT_URL.format(user_id=MAX_BIGINT), json={"first_name": "Missing"},
    )
    assert missing.status_code == 404, missing.text


MAX_BIGINT = 9_223_372_036_854_775_807


def test_status_is_strict_idempotent_and_reactivates_existing_token(scenario):
    target_id = scenario.actors["student"]["id"]
    target_headers = scenario.actors["student"]["headers"]
    path = STATUS_URL.format(user_id=target_id)
    actor_id = scenario.actors["admin"]["id"]

    for value in ("false", 0, 1, None):
        response = request_as(
            scenario, "admin", "PATCH", path, json={"is_active": value}
        )
        assert response.status_code == 422, (value, response.text)

    before_events = audit_events(scenario, actor_id, target_id)
    first = request_as(
        scenario, "admin", "PATCH", path, json={"is_active": False}
    )
    assert first.status_code == 200, first.text
    assert first.json()["is_active"] is False
    assert request_as(
        scenario, "student", "GET", "/auth/me"
    ).status_code == 401
    login = scenario.client.post("/auth/login", json={
        "email": scenario.actors["student"]["email"],
        "password": "integration-only-not-a-real-password",
    })
    assert login.status_code == 401

    duplicate = request_as(
        scenario, "admin", "PATCH", path, json={"is_active": False}
    )
    assert duplicate.status_code == 200, duplicate.text
    assert len(audit_events(scenario, actor_id, target_id)) == len(before_events) + 1

    reactivated = request_as(
        scenario, "admin", "PATCH", path, json={"is_active": True}
    )
    assert reactivated.status_code == 200, reactivated.text
    assert reactivated.json()["is_active"] is True
    assert request_as(scenario, "student", "GET", "/auth/me").status_code == 200
    login = scenario.client.post("/auth/login", json={
        "email": scenario.actors["student"]["email"],
        "password": "integration-only-not-a-real-password",
    })
    assert login.status_code == 200, login.text


def test_deactivation_preserves_relations_and_audits_once(scenario):
    scenario.approve()
    material = scenario.upload()
    scenario.review(material)
    target_id = scenario.actors["student"]["id"]
    actor_id = scenario.actors["admin"]["id"]

    with scenario.engine.connect() as connection:
        before = {
            "roles": connection.execute(text(
                "SELECT COUNT(*) FROM public.user_roles WHERE user_id = :id"
            ), {"id": target_id}).scalar_one(),
            "projects": connection.execute(text(
                "SELECT COUNT(*) FROM public.projects WHERE student_id = :id"
            ), {"id": target_id}).scalar_one(),
            "stages": connection.execute(text(
                "SELECT COUNT(*) FROM public.stages WHERE project_id = :id"
            ), {"id": scenario.project_id}).scalar_one(),
            "versions": connection.execute(text("""
                SELECT COUNT(*) FROM public.material_versions mv
                JOIN public.stages s ON s.id = mv.stage_id
                WHERE s.project_id = :id
            """), {"id": scenario.project_id}).scalar_one(),
            "reviews": connection.execute(text("""
                SELECT COUNT(*) FROM public.reviews r
                JOIN public.material_versions mv ON mv.id = r.material_version_id
                JOIN public.stages s ON s.id = mv.stage_id
                WHERE s.project_id = :id
            """), {"id": scenario.project_id}).scalar_one(),
            "notifications": connection.execute(text(
                "SELECT COUNT(*) FROM public.notifications WHERE project_id = :id"
            ), {"id": scenario.project_id}).scalar_one(),
        }
    assert before["projects"] > 0
    assert before["stages"] > 0
    assert before["versions"] > 0
    assert before["reviews"] > 0
    assert before["notifications"] > 0

    path = STATUS_URL.format(user_id=target_id)
    first = request_as(
        scenario, "admin", "PATCH", path, json={"is_active": False}
    )
    assert first.status_code == 200, first.text
    again = request_as(
        scenario, "admin", "PATCH", path, json={"is_active": False}
    )
    assert again.status_code == 200, again.text

    with scenario.engine.connect() as connection:
        after = {
            "roles": connection.execute(text(
                "SELECT COUNT(*) FROM public.user_roles WHERE user_id = :id"
            ), {"id": target_id}).scalar_one(),
            "projects": connection.execute(text(
                "SELECT COUNT(*) FROM public.projects WHERE student_id = :id"
            ), {"id": target_id}).scalar_one(),
            "stages": connection.execute(text(
                "SELECT COUNT(*) FROM public.stages WHERE project_id = :id"
            ), {"id": scenario.project_id}).scalar_one(),
            "versions": connection.execute(text("""
                SELECT COUNT(*) FROM public.material_versions mv
                JOIN public.stages s ON s.id = mv.stage_id
                WHERE s.project_id = :id
            """), {"id": scenario.project_id}).scalar_one(),
            "reviews": connection.execute(text("""
                SELECT COUNT(*) FROM public.reviews r
                JOIN public.material_versions mv ON mv.id = r.material_version_id
                JOIN public.stages s ON s.id = mv.stage_id
                WHERE s.project_id = :id
            """), {"id": scenario.project_id}).scalar_one(),
            "notifications": connection.execute(text(
                "SELECT COUNT(*) FROM public.notifications WHERE project_id = :id"
            ), {"id": scenario.project_id}).scalar_one(),
        }
    assert after == before
    events = audit_events(scenario, actor_id, target_id)
    assert len([event for event in events if event["action"] == "user_status_changed"]) == 1
    status_event = next(event for event in events if event["action"] == "user_status_changed")
    assert status_event["details"] == {
        "changes": {"is_active": {"old": True, "new": False}}
    }


def test_admin_cannot_deactivate_self(scenario):
    admin_id = scenario.actors["admin"]["id"]
    before = audit_events(scenario, admin_id, admin_id)
    response = request_as(
        scenario, "admin", "PATCH",
        STATUS_URL.format(user_id=admin_id), json={"is_active": False},
    )
    assert response.status_code == 403
    assert user_row(scenario, admin_id)["is_active"] is True
    assert audit_events(scenario, admin_id, admin_id) == before


def test_last_active_admin_is_protected_under_concurrent_requests(scenario):
    first_id = scenario.actors["admin"]["id"]
    second_id = scenario.actors["admin2"]["id"]
    manager_id = scenario.actors["account_manager"]["id"]
    with scenario.engine.begin() as connection:
        original_admins = connection.execute(text("""
            SELECT u.id, u.is_active
            FROM public.users u
            JOIN public.user_roles ur ON ur.user_id = u.id
            JOIN public.roles r ON r.id = ur.role_id
            WHERE r.code = 'admin'
        """)).all()
        all_ids = [row.id for row in original_admins]
        connection.execute(text("""
            UPDATE public.users SET is_active = false WHERE id = ANY(:ids)
        """), {"ids": all_ids})
        connection.execute(text("""
            UPDATE public.users SET is_active = true WHERE id = ANY(:ids)
        """), {"ids": [first_id, second_id]})

    try:
        def deactivate(user_id):
            return request_as(
                scenario, "account_manager", "PATCH",
                STATUS_URL.format(user_id=user_id), json={"is_active": False},
            ).status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(deactivate, (first_id, second_id)))
        assert sorted(results) == [200, 409]
        with scenario.engine.connect() as connection:
            active_admins = connection.execute(text("""
                SELECT COUNT(DISTINCT u.id)
                FROM public.users u
                JOIN public.user_roles ur ON ur.user_id = u.id
                JOIN public.roles r ON r.id = ur.role_id
                WHERE u.is_active AND r.code = 'admin'
            """)).scalar_one()
        assert active_admins == 1
        manager_events = []
        for user_id in (first_id, second_id):
            manager_events.extend(
                audit_events(scenario, manager_id, user_id)
            )
        assert len(manager_events) == 1
        assert manager_events[0]["action"] == "user_status_changed"
        assert manager_id != first_id and manager_id != second_id
    finally:
        with scenario.engine.begin() as connection:
            for row in original_admins:
                connection.execute(text("""
                    UPDATE public.users SET is_active = :active WHERE id = :id
                """), {"active": row.is_active, "id": row.id})


def test_rejected_status_change_has_no_audit_or_partial_update(scenario):
    target_id = scenario.actors["student"]["id"]
    actor_id = scenario.actors["admin"]["id"]
    before = dict(user_row(scenario, target_id))
    response = request_as(
        scenario, "admin", "PATCH",
        STATUS_URL.format(user_id=target_id), json={"is_active": "false"},
    )
    assert response.status_code == 422
    assert dict(user_row(scenario, target_id)) == before
    assert audit_events(scenario, actor_id, target_id) == []
