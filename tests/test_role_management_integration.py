from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from time import monotonic
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.admin_lock import ADMIN_MUTATION_LOCK_ID, acquire_admin_mutation_lock


PERMISSIONS_PATH = "/admin/permissions"
ROLE_PERMISSIONS_PATH = "/admin/roles/{role_code}/permissions"
USER_ROLES_PATH = "/admin/users/{user_id}/roles"
APP_ROLES = {"student", "supervisor", "teacher", "department", "admin"}


def request_as(case, actor, method, path, **kwargs):
    return case.client.request(
        method, path, headers=case.actors[actor]["headers"], **kwargs
    )


def role_permissions(case, actor, role_code):
    return request_as(
        case, actor, "GET", ROLE_PERMISSIONS_PATH.format(role_code=role_code)
    )


def read_role_codes(case, user_id):
    with case.engine.connect() as connection:
        return connection.execute(text("""
            SELECT r.code FROM public.user_roles ur
            JOIN public.roles r ON r.id = ur.role_id
            WHERE ur.user_id = :user_id ORDER BY r.code
        """), {"user_id": user_id}).scalars().all()


def role_id(case, role_code):
    with case.engine.connect() as connection:
        return connection.execute(text(
            "SELECT id FROM public.roles WHERE code = :code"
        ), {"code": role_code}).scalar_one()


def project_topic_id(case, project_id):
    with case.engine.connect() as connection:
        return connection.execute(text(
            "SELECT topic_id FROM public.projects WHERE id = :project_id"
        ), {"project_id": project_id}).scalar_one()


def restore_role_codes(case, user_id, role_codes):
    with case.engine.begin() as connection:
        acquire_admin_mutation_lock(connection)
        connection.execute(text("""
            DELETE FROM public.user_roles WHERE user_id = :user_id
        """), {"user_id": user_id})
        if role_codes:
            connection.execute(text("""
                INSERT INTO public.user_roles (user_id, role_id)
                SELECT :user_id, id FROM public.roles WHERE code = :code
            """), [
                {"user_id": user_id, "code": code}
                for code in role_codes
            ])


def user_is_active(case, user_id):
    with case.engine.connect() as connection:
        return connection.execute(text("""
            SELECT is_active FROM public.users WHERE id = :user_id
        """), {"user_id": user_id}).scalar_one()


def project_records(case, project_id):
    with case.engine.connect() as connection:
        return {
            "project": tuple(connection.execute(text("""
                SELECT id, student_id, supervisor_id, teacher_id, topic_id
                FROM public.projects WHERE id = :project_id
            """), {"project_id": project_id}).one()),
            "stages": connection.execute(text("""
                SELECT id, status FROM public.stages
                WHERE project_id = :project_id ORDER BY id
            """), {"project_id": project_id}).all(),
            "versions": connection.execute(text("""
                SELECT mv.id, mv.storage_key, mv.sha256
                FROM public.material_versions mv
                JOIN public.stages s ON s.id = mv.stage_id
                WHERE s.project_id = :project_id ORDER BY mv.id
            """), {"project_id": project_id}).all(),
            "reviews": connection.execute(text("""
                SELECT r.id, r.material_version_id, r.decision
                FROM public.reviews r
                JOIN public.material_versions mv ON mv.id = r.material_version_id
                JOIN public.stages s ON s.id = mv.stage_id
                WHERE s.project_id = :project_id ORDER BY r.id
            """), {"project_id": project_id}).all(),
            "notifications": connection.execute(text("""
                SELECT id, recipient_id, event_key
                FROM public.notifications WHERE project_id = :project_id ORDER BY id
            """), {"project_id": project_id}).all(),
        }


def audit_events(case, actor_id, entity_type, entity_id):
    with case.engine.connect() as connection:
        return connection.execute(text("""
            SELECT action, actor_id, entity_type, entity_id, details
            FROM public.audit_events
            WHERE actor_id = :actor_id AND entity_type = :entity_type
              AND entity_id = :entity_id
            ORDER BY id
        """), {
            "actor_id": actor_id,
            "entity_type": entity_type,
            "entity_id": entity_id,
        }).mappings().all()


@pytest.fixture
def permission_matrix_guard(scenario):
    with scenario.engine.connect() as connection:
        original = {
            row["role_code"]: set(row["permission_codes"])
            for row in connection.execute(text("""
                SELECT r.code AS role_code,
                       COALESCE(array_agg(p.code ORDER BY p.code)
                           FILTER (WHERE p.code IS NOT NULL),
                           ARRAY[]::varchar[]) AS permission_codes
                FROM public.roles r
                LEFT JOIN public.role_permissions rp ON rp.role_id = r.id
                LEFT JOIN public.permissions p ON p.id = rp.permission_id
                WHERE r.code = ANY(:roles)
                GROUP BY r.code
            """), {"roles": sorted(APP_ROLES)}).mappings()
        }
    yield original
    with scenario.engine.begin() as connection:
        acquire_admin_mutation_lock(connection)
        role_ids = connection.execute(text("""
            SELECT id, code FROM public.roles WHERE code = ANY(:roles)
        """), {"roles": sorted(APP_ROLES)}).mappings().all()
        connection.execute(text("""
            DELETE FROM public.role_permissions
            WHERE role_id = ANY(:role_ids)
        """), {"role_ids": [row["id"] for row in role_ids]})
        restored = [
            {"role_id": row["id"], "permission_code": code}
            for row in role_ids
            for code in original[row["code"]]
        ]
        if restored:
            connection.execute(text("""
                INSERT INTO public.role_permissions (role_id, permission_id)
                SELECT :role_id, p.id
                FROM public.permissions p
                WHERE p.code = :permission_code
            """), restored)


def wait_for_advisory_lock_waiters(engine, expected, timeout=8):
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        with engine.connect() as connection:
            count = connection.execute(text("""
                SELECT COUNT(*)
                FROM pg_stat_activity
                WHERE wait_event_type = 'Lock'
                  AND query ILIKE '%pg_advisory_xact_lock%'
                  AND pid <> pg_backend_pid()
            """)).scalar_one()
        if count >= expected:
            return
        Event().wait(0.02)
    raise AssertionError(f"Expected {expected} requests waiting on the shared advisory lock.")


def run_behind_shared_lock(case, requests, before_release=None):
    blocker = case.engine.connect()
    transaction = blocker.begin()
    blocker.execute(text("SELECT pg_advisory_xact_lock(:lock_id)"), {
        "lock_id": ADMIN_MUTATION_LOCK_ID,
    })
    barrier = Barrier(len(requests))
    executor = ThreadPoolExecutor(max_workers=len(requests))
    futures = []
    try:
        for actor, method, path, payload in requests:
            def execute_request(actor=actor, method=method, path=path, payload=payload):
                barrier.wait(timeout=5)
                return request_as(case, actor, method, path, json=payload)

            futures.append(executor.submit(execute_request))
        wait_for_advisory_lock_waiters(case.engine, len(requests))
        if before_release is not None:
            before_release(blocker)
        transaction.commit()
        return [future.result(timeout=10) for future in futures]
    finally:
        if transaction.is_active:
            transaction.rollback()
        blocker.close()
        executor.shutdown(wait=True, cancel_futures=True)


def restore_admin_accounts(case, snapshots):
    with case.engine.begin() as connection:
        acquire_admin_mutation_lock(connection)
        for user_id, state in snapshots.items():
            connection.execute(text("""
                UPDATE public.users SET is_active = :is_active WHERE id = :user_id
            """), {"is_active": state["is_active"], "user_id": user_id})
            connection.execute(text("""
                DELETE FROM public.user_roles WHERE user_id = :user_id
            """), {"user_id": user_id})
            if state["role_codes"]:
                connection.execute(text("""
                    INSERT INTO public.user_roles (user_id, role_id)
                    SELECT :user_id, id FROM public.roles WHERE code = ANY(:codes)
                """), {"user_id": user_id, "codes": state["role_codes"]})


def admin_snapshots(case, user_ids):
    with case.engine.connect() as connection:
        users = connection.execute(text("""
                SELECT id, is_active FROM public.users WHERE id = ANY(:ids)
            """), {"ids": user_ids}).mappings().all()
        snapshots = {}
        for row in users:
            role_codes = connection.execute(text("""
                SELECT r.code FROM public.user_roles ur
                JOIN public.roles r ON r.id = ur.role_id
                WHERE ur.user_id = :user_id ORDER BY r.code
            """), {"user_id": row["id"]}).scalars().all()
            snapshots[row["id"]] = {
                "is_active": row["is_active"],
                "role_codes": role_codes,
            }
        return snapshots


def test_role_management_authorization_and_existing_contract(scenario):
    target_id = scenario.actors["student"]["id"]
    requests = (
        ("GET", PERMISSIONS_PATH, None),
        ("GET", ROLE_PERMISSIONS_PATH.format(role_code="teacher"), None),
        ("PUT", ROLE_PERMISSIONS_PATH.format(role_code="teacher"),
         {"permission_codes": ["topics.manage"]}),
        ("GET", USER_ROLES_PATH.format(user_id=target_id), None),
        ("PUT", USER_ROLES_PATH.format(user_id=target_id),
         {"role_codes": ["student"]}),
    )
    for method, path, payload in requests:
        response = scenario.client.request(method, path, json=payload)
        assert response.status_code == 401
        for actor in ("student", "supervisor", "teacher", "department"):
            assert request_as(scenario, actor, method, path, json=payload).status_code == 403
        assert request_as(
            scenario, "account_manager", method, path, json=payload
        ).status_code == 403

    assert request_as(scenario, "account_manager", "GET", "/admin/roles").status_code == 200
    assert request_as(scenario, "admin", "GET", PERMISSIONS_PATH).status_code == 200
    assert request_as(
        scenario, "admin", "GET",
        USER_ROLES_PATH.format(user_id=target_id),
    ).status_code == 200


def test_existing_user_creation_contract_uses_shared_admin_lock(scenario):
    email = f"created-{uuid4().hex}@integration.invalid"
    response = request_as(
        scenario, "admin", "POST", "/admin/users",
        json={
            "email": email,
            "password": "integration-only-not-a-real-password",
            "first_name": "Created",
            "last_name": "Integration",
            "role_codes": ["student"],
        },
    )
    assert response.status_code == 201, response.text
    result = response.json()
    assert result["email"] == email
    assert result["roles"] == ["student"]
    assert set(result) == {
        "id", "email", "first_name", "last_name",
        "middle_name", "study_group", "is_active", "roles",
    }
    assert "password_hash" not in result
    assert request_as(
        scenario, "admin", "GET",
        USER_ROLES_PATH.format(user_id=result["id"]),
    ).json()["role_codes"] == ["student"]


def test_catalogs_are_explicit_and_auxiliary_roles_are_not_manageable(scenario):
    permissions = scenario.request("admin", "GET", PERMISSIONS_PATH, 200)
    assert len(permissions) == 12
    assert all(set(item) == {"code", "name"} for item in permissions)
    assert all("password" not in str(item).lower() for item in permissions)

    roles = scenario.request("admin", "GET", "/admin/roles", 200)
    assert {item["code"] for item in roles} >= APP_ROLES
    teacher = role_permissions(scenario, "admin", "teacher")
    assert teacher.status_code == 200, teacher.text
    assert set(teacher.json()) == {"role_code", "permission_codes"}
    assert teacher.json()["role_code"] == "teacher"
    assert teacher.json()["permission_codes"] == sorted({
        "topics.manage", "projects.create", "projects.read",
        "supervisors.assign", "plans.propose", "plans.approve",
        "reviews.write", "statuses.read", "reports.read",
    })
    expected_policy = {
        "student": {
            "projects.create", "projects.read", "plans.propose",
            "materials.upload", "statuses.read",
        },
        "supervisor": {
            "projects.read", "plans.propose", "plans.approve",
            "reviews.write", "statuses.read",
        },
        "teacher": {
            "topics.manage", "projects.create", "projects.read",
            "supervisors.assign", "plans.propose", "plans.approve",
            "reviews.write", "statuses.read", "reports.read",
        },
        "department": {
            "topics.manage", "projects.read", "supervisors.assign",
            "statuses.read", "reports.read",
        },
        "admin": {"users.manage", "roles.manage"},
    }
    for role_code, expected in expected_policy.items():
        response = role_permissions(scenario, "admin", role_code)
        assert response.status_code == 200, response.text
        assert set(response.json()["permission_codes"]) == expected

    for method in ("GET", "PUT"):
        response = request_as(
            scenario, "admin", method,
            ROLE_PERMISSIONS_PATH.format(role_code="integration-account-manager"),
            json={"permission_codes": []},
        )
        assert response.status_code == 404

    user_id = scenario.actors["student"]["id"]
    user_roles = request_as(
        scenario, "admin", "GET", USER_ROLES_PATH.format(user_id=user_id)
    )
    assert user_roles.status_code == 200, user_roles.text
    assert user_roles.json() == {"user_id": user_id, "role_codes": ["student"]}
    assert set(user_roles.json()) == {"user_id", "role_codes"}


def test_permission_replacement_policy_validation_and_admin_protection(
    scenario, permission_matrix_guard,
):
    teacher_path = ROLE_PERMISSIONS_PATH.format(role_code="teacher")
    student_path = ROLE_PERMISSIONS_PATH.format(role_code="student")
    admin_path = ROLE_PERMISSIONS_PATH.format(role_code="admin")
    actor_id = scenario.actors["admin"]["id"]
    invalid_requests = (
        (teacher_path, {"permission_codes": ["projects.read", " projects.read "]}, 422),
        (teacher_path, {"permission_codes": [" "]}, 422),
        (teacher_path, {"permission_codes": ["not.a.permission"]}, 422),
        (teacher_path, {"permission_codes": ["users.manage"]}, 422),
        (teacher_path, {"permission_codes": [], "unexpected": True}, 422),
        (student_path, {"permission_codes": ["projects.read", "roles.manage"]}, 422),
        (admin_path, {"permission_codes": ["users.manage"]}, 409),
        (admin_path, {
            "permission_codes": ["users.manage", "roles.manage", "projects.read"],
        }, 422),
    )
    for path, payload, expected in invalid_requests:
        response = request_as(scenario, "admin", "PUT", path, json=payload)
        assert response.status_code == expected, (payload, response.text)

    for role_code, expected in permission_matrix_guard.items():
        assert set(
            role_permissions(scenario, "admin", role_code).json()["permission_codes"]
        ) == expected
        assert audit_events(
            scenario, actor_id, "role_permissions", role_id(scenario, role_code)
        ) == []


def test_empty_academic_permissions_are_allowed_and_audited(
    scenario, permission_matrix_guard,
):
    actor_id = scenario.actors["admin"]["id"]
    with scenario.engine.connect() as connection:
        role_id = connection.execute(text(
            "SELECT id FROM public.roles WHERE code = 'supervisor'"
        )).scalar_one()
    response = request_as(
        scenario, "admin", "PUT",
        ROLE_PERMISSIONS_PATH.format(role_code="supervisor"),
        json={"permission_codes": []},
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"role_code": "supervisor", "permission_codes": []}
    events = audit_events(scenario, actor_id, "role_permissions", role_id)
    assert len(events) == 1
    assert events[0]["action"] == "role_permissions_replaced"
    assert events[0]["details"]["before"]
    assert events[0]["details"]["after"] == []
    assert events[0]["details"]["removed"] == events[0]["details"]["before"]


def test_role_permission_changes_are_idempotent_audited_and_effective_with_same_token(
    scenario, permission_matrix_guard,
):
    actor_id = scenario.actors["admin"]["id"]
    teacher_id = role_id(scenario, "teacher")
    original = role_permissions(scenario, "admin", "teacher").json()["permission_codes"]
    changed = sorted(set(original) - {"topics.manage"})
    path = ROLE_PERMISSIONS_PATH.format(role_code="teacher")

    no_op = request_as(
        scenario, "admin", "PUT", path,
        json={"permission_codes": original},
    )
    assert no_op.status_code == 200, no_op.text
    assert audit_events(scenario, actor_id, "role_permissions", teacher_id) == []

    removed = request_as(
        scenario, "admin", "PUT", path,
        json={"permission_codes": changed},
    )
    assert removed.status_code == 200, removed.text
    assert request_as(
        scenario, "teacher", "POST", "/topics", json={"title": "Permission revoked"}
    ).status_code == 403

    restored = request_as(
        scenario, "admin", "PUT", path,
        json={"permission_codes": original},
    )
    assert restored.status_code == 200, restored.text
    assert request_as(
        scenario, "teacher", "POST", "/topics", json={"title": "Permission restored"}
    ).status_code == 201
    events = audit_events(scenario, actor_id, "role_permissions", teacher_id)
    assert len(events) == 2
    assert events[0]["details"] == {
        "before": original,
        "after": changed,
        "added": [],
        "removed": ["topics.manage"],
    }
    assert events[1]["details"] == {
        "before": changed,
        "after": original,
        "added": ["topics.manage"],
        "removed": [],
    }
    assert all(event["actor_id"] == actor_id for event in events)

    teacher_department_before = {
        code: role_permissions(scenario, "admin", code).json()["permission_codes"]
        for code in ("teacher", "department")
    }
    assert request_as(
        scenario, "teacher_department", "GET", "/reports/projects"
    ).status_code == 200
    for role_code in ("teacher", "department"):
        remaining = sorted(set(teacher_department_before[role_code]) - {"reports.read"})
        response = request_as(
            scenario, "admin", "PUT",
            ROLE_PERMISSIONS_PATH.format(role_code=role_code),
            json={"permission_codes": remaining},
        )
        assert response.status_code == 200, response.text
        if role_code == "teacher":
            assert request_as(
                scenario, "teacher_department", "GET", "/reports/projects"
            ).status_code == 200
    assert request_as(
        scenario, "teacher_department", "GET", "/reports/projects"
    ).status_code == 403


def test_role_assignment_replacement_idempotency_audit_and_inactive_target(scenario):
    target_id = scenario.actors["outsider"]["id"]
    actor_id = scenario.actors["admin"]["id"]
    path = USER_ROLES_PATH.format(user_id=target_id)
    original = read_role_codes(scenario, target_id)
    before = request_as(scenario, "admin", "GET", path)
    assert before.status_code == 200, before.text
    assert before.json()["role_codes"] == ["student"]
    project_id = scenario.other_project_id
    plan = scenario.request(
        "outsider", "PUT", f"/projects/{project_id}/plan", 200,
        json=scenario.plan_input,
    )
    scenario.request(
        "unassigned", "POST", f"/projects/{project_id}/plan/approve", 200,
    )
    upload = request_as(
        scenario, "outsider", "POST",
        f"/stages/{plan['stages'][0]['id']}/materials",
        files={"file": ("role-preservation.txt", b"Keep this version\n", "text/plain")},
    )
    assert upload.status_code == 201, upload.text
    before_project_records = project_records(scenario, project_id)
    before_files = {
        path.name: path.read_bytes()
        for path in scenario.storage.iterdir()
    }
    try:
        updated = request_as(
            scenario, "admin", "PUT", path,
            json={"role_codes": [" teacher ", "student"]},
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["role_codes"] == ["student", "teacher"]
        events = audit_events(scenario, actor_id, "user_roles", target_id)
        assert len(events) == 1
        assert events[0]["action"] == "user_roles_replaced"
        assert events[0]["details"] == {
            "before": ["student"],
            "after": ["student", "teacher"],
            "added": ["teacher"],
            "removed": [],
        }
        repeated = request_as(
            scenario, "admin", "PUT", path,
            json={"role_codes": ["student", "teacher"]},
        )
        assert repeated.status_code == 200, repeated.text
        assert len(audit_events(scenario, actor_id, "user_roles", target_id)) == 1

        disabled = request_as(
            scenario, "admin", "PATCH",
            f"/admin/users/{target_id}/status", json={"is_active": False},
        )
        assert disabled.status_code == 200, disabled.text
        changed_while_inactive = request_as(
            scenario, "admin", "PUT", path, json={"role_codes": ["student"]},
        )
        assert changed_while_inactive.status_code == 200, changed_while_inactive.text
        assert changed_while_inactive.json()["role_codes"] == ["student"]
        assert user_is_active(scenario, target_id) is False
        assert request_as(scenario, "outsider", "GET", "/auth/me").status_code == 401
        assert project_records(scenario, project_id) == before_project_records
        assert {
            path.name: path.read_bytes()
            for path in scenario.storage.iterdir()
        } == before_files
    finally:
        with scenario.engine.begin() as connection:
            acquire_admin_mutation_lock(connection)
            connection.execute(text("""
                UPDATE public.users SET is_active = true WHERE id = :user_id
            """), {"user_id": target_id})
        restore_role_codes(scenario, target_id, original)


def test_role_assignment_rejects_invalid_sets_unknown_user_and_bigint(scenario):
    target_id = scenario.actors["student"]["id"]
    path = USER_ROLES_PATH.format(user_id=target_id)
    original = read_role_codes(scenario, target_id)
    actor_id = scenario.actors["admin"]["id"]
    invalid_payloads = (
        {},
        {"role_codes": []},
        {"role_codes": ["student", " student "]},
        {"role_codes": [" "]},
        {"role_codes": ["not-a-role"]},
        {"role_codes": ["integration-account-manager"]},
        {"role_codes": ["student"], "extra": True},
    )
    for payload in invalid_payloads:
        response = request_as(scenario, "admin", "PUT", path, json=payload)
        assert response.status_code == 422, (payload, response.text)
    assert read_role_codes(scenario, target_id) == original
    assert audit_events(scenario, actor_id, "user_roles", target_id) == []

    missing = USER_ROLES_PATH.format(user_id=9_223_372_036_854_775_807)
    assert request_as(scenario, "admin", "GET", missing).status_code == 404
    assert request_as(
        scenario, "admin", "PUT", missing, json={"role_codes": ["student"]}
    ).status_code == 404
    for user_id in (0, -1, 9_223_372_036_854_775_808):
        assert request_as(
            scenario, "admin", "GET", USER_ROLES_PATH.format(user_id=user_id)
        ).status_code == 422
        assert request_as(
            scenario, "admin", "PUT", USER_ROLES_PATH.format(user_id=user_id),
            json={"role_codes": ["student"]},
        ).status_code == 422


def test_admin_cannot_change_own_roles_but_noop_is_idempotent(scenario):
    admin_id = scenario.actors["admin"]["id"]
    path = USER_ROLES_PATH.format(user_id=admin_id)
    unchanged = request_as(
        scenario, "admin", "PUT", path, json={"role_codes": ["admin"]}
    )
    assert unchanged.status_code == 200, unchanged.text
    assert audit_events(scenario, admin_id, "user_roles", admin_id) == []
    rejected = request_as(
        scenario, "admin", "PUT", path, json={"role_codes": ["teacher"]}
    )
    assert rejected.status_code == 403
    assert read_role_codes(scenario, admin_id) == ["admin"]
    assert audit_events(scenario, admin_id, "user_roles", admin_id) == []


def test_last_active_admin_status_change_returns_conflict_without_side_effects(scenario):
    remaining_admin = scenario.actors["admin"]["id"]
    with scenario.engine.connect() as connection:
        snapshots = connection.execute(text("""
            SELECT DISTINCT u.id, u.is_active
            FROM public.users u
            JOIN public.user_roles ur ON ur.user_id = u.id
            JOIN public.roles r ON r.id = ur.role_id
            WHERE r.code = 'admin'
        """)).mappings().all()
    assert any(row["id"] == remaining_admin and row["is_active"] for row in snapshots)
    actor_id = scenario.actors["account_manager"]["id"]
    try:
        with scenario.engine.begin() as connection:
            acquire_admin_mutation_lock(connection)
            connection.execute(text("""
                UPDATE public.users SET is_active = false
                WHERE id IN (
                    SELECT ur.user_id FROM public.user_roles ur
                    JOIN public.roles r ON r.id = ur.role_id
                    WHERE r.code = 'admin' AND ur.user_id <> :remaining_admin
                )
            """), {"remaining_admin": remaining_admin})

        before_audit = audit_events(
            scenario, actor_id, "users", remaining_admin
        )
        rejected = request_as(
            scenario, "account_manager", "PATCH",
            f"/admin/users/{remaining_admin}/status",
            json={"is_active": False},
        )
        assert rejected.status_code == 409, rejected.text
        assert user_is_active(scenario, remaining_admin) is True
        assert audit_events(
            scenario, actor_id, "users", remaining_admin
        ) == before_audit
        assert request_as(scenario, "admin", "GET", PERMISSIONS_PATH).status_code == 200
    finally:
        with scenario.engine.begin() as connection:
            acquire_admin_mutation_lock(connection)
            for row in snapshots:
                connection.execute(text("""
                    UPDATE public.users SET is_active = :is_active
                    WHERE id = :user_id
                """), {"is_active": row["is_active"], "user_id": row["id"]})


@pytest.mark.parametrize(
    ("actor", "role_code"),
    (("student", "student"), ("teacher", "teacher"), ("supervisor", "supervisor")),
)
def test_project_responsibility_roles_cannot_be_removed(scenario, actor, role_code):
    user_id = scenario.actors[actor]["id"]
    before = read_role_codes(scenario, user_id)
    replacement = sorted(set(before) - {role_code})
    if not replacement:
        replacement = ["department"]
    response = request_as(
        scenario, "admin", "PUT", USER_ROLES_PATH.format(user_id=user_id),
        json={"role_codes": replacement},
    )
    assert response.status_code == 409, response.text
    assert read_role_codes(scenario, user_id) == before
    assert audit_events(
        scenario, scenario.actors["admin"]["id"], "user_roles", user_id
    ) == []


def test_role_assignment_changes_effective_permissions_without_new_token(scenario):
    user_id = scenario.actors["teacher_department"]["id"]
    actor_id = scenario.actors["admin"]["id"]
    topic_id = project_topic_id(scenario, scenario.project_id)
    original = read_role_codes(scenario, user_id)
    assert original == ["department", "teacher"]
    assert request_as(
        scenario, "teacher_department", "GET", "/reports/projects"
    ).status_code == 200

    try:
        changed = request_as(
            scenario, "admin", "PUT", USER_ROLES_PATH.format(user_id=user_id),
            json={"role_codes": ["department"]},
        )
        assert changed.status_code == 200, changed.text
        identity = request_as(scenario, "teacher_department", "GET", "/auth/me")
        assert identity.status_code == 200
        assert identity.json()["roles"] == ["department"]
        assert "projects.create" not in identity.json()["permissions"]
        assert "projects.read" in identity.json()["permissions"]
        assert request_as(
            scenario, "teacher_department", "GET", "/projects"
        ).status_code == 200
        assert request_as(
            scenario, "teacher_department", "POST", "/projects",
            json={
                "topic_id": topic_id,
                "teacher_id": user_id,
                "student_id": scenario.actors["student"]["id"],
            },
        ).status_code == 403
        assert request_as(
            scenario, "teacher_department", "GET", "/reports/projects"
        ).status_code == 200
        events = audit_events(scenario, actor_id, "user_roles", user_id)
        assert len(events) == 1
        assert events[0]["details"] == {
            "before": ["department", "teacher"],
            "after": ["department"],
            "added": [],
            "removed": ["teacher"],
        }
    finally:
        restore_role_codes(scenario, user_id, original)


def test_concurrent_admin_role_removal_and_account_deactivation_are_serialized(scenario):
    admin_a = scenario.actors["admin"]["id"]
    admin_b = scenario.actors["admin2"]["id"]
    admin_guard = scenario.actors["admin3"]["id"]
    snapshots = admin_snapshots(scenario, [admin_a, admin_b, admin_guard])
    requests = [
        ("admin", "PUT", USER_ROLES_PATH.format(user_id=admin_b),
         {"role_codes": ["teacher"]}),
        ("admin2", "PATCH", f"/admin/users/{admin_a}/status",
         {"is_active": False}),
    ]
    try:
        responses = run_behind_shared_lock(scenario, requests)
        assert sorted(response.status_code for response in responses) == [200, 403]
        assert user_is_active(scenario, admin_guard) is True
        assert "admin" in read_role_codes(scenario, admin_guard)
        assert request_as(scenario, "admin3", "GET", PERMISSIONS_PATH).status_code == 200
    finally:
        restore_admin_accounts(scenario, snapshots)


def test_two_concurrent_admin_role_removals_leave_an_operational_admin(scenario):
    admin_a = scenario.actors["admin"]["id"]
    admin_b = scenario.actors["admin2"]["id"]
    admin_guard = scenario.actors["admin3"]["id"]
    snapshots = admin_snapshots(scenario, [admin_a, admin_b, admin_guard])
    requests = [
        ("admin", "PUT", USER_ROLES_PATH.format(user_id=admin_b),
         {"role_codes": ["teacher"]}),
        ("admin2", "PUT", USER_ROLES_PATH.format(user_id=admin_a),
         {"role_codes": ["teacher"]}),
    ]
    try:
        responses = run_behind_shared_lock(scenario, requests)
        assert sorted(response.status_code for response in responses) == [200, 403]
        with scenario.engine.connect() as connection:
            active_admins = connection.execute(text("""
                SELECT COUNT(DISTINCT u.id)
                FROM public.users u
                JOIN public.user_roles ur ON ur.user_id = u.id
                JOIN public.roles r ON r.id = ur.role_id
                WHERE u.is_active AND r.code = 'admin'
            """)).scalar_one()
        assert active_admins >= 1
        assert user_is_active(scenario, admin_guard) is True
        assert request_as(scenario, "admin3", "GET", PERMISSIONS_PATH).status_code == 200
    finally:
        restore_admin_accounts(scenario, snapshots)


def test_actor_authorization_is_revalidated_after_waiting_for_shared_lock(scenario):
    admin_id = scenario.actors["admin"]["id"]
    role_path = ROLE_PERMISSIONS_PATH.format(role_code="teacher")
    original_active = user_is_active(scenario, admin_id)

    def deactivate_actor(connection):
        connection.execute(text("""
            UPDATE public.users SET is_active = false WHERE id = :admin_id
        """), {"admin_id": admin_id})

    try:
        responses = run_behind_shared_lock(
            scenario,
            [("admin", "PUT", role_path, {
                "permission_codes": role_permissions(
                    scenario, "admin", "teacher"
                ).json()["permission_codes"],
            })],
            before_release=deactivate_actor,
        )
        assert responses[0].status_code == 403
        assert role_permissions(scenario, "admin3", "teacher").status_code == 200
        assert user_is_active(scenario, admin_id) is False
    finally:
        with scenario.engine.begin() as connection:
            acquire_admin_mutation_lock(connection)
            connection.execute(text("""
                UPDATE public.users SET is_active = :active WHERE id = :admin_id
            """), {"active": original_active, "admin_id": admin_id})
