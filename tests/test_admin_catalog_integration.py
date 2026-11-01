from uuid import uuid4

import pytest
from sqlalchemy import text

from test_role_management_integration import permission_matrix_guard
from test_user_management_integration import PUBLIC_USER_FIELDS


def create(case, **fields):
    return case.request("admin", "POST", "/admin/users", 201, json={
        "email": f"catalog-{uuid4().hex}@integration.invalid",
        "password": "integration-only-not-a-real-password",
        "first_name": "Catalog", "last_name": "Reader", "role_codes": ["department"],
        **fields,
    })


@pytest.mark.parametrize("field", ["email", "first_name", "last_name"])
def test_search_case_insensitive_by_each_field(scenario, field):
    marker = "Find" + uuid4().hex
    value = marker + "@integration.invalid" if field == "email" else marker
    user = create(scenario, **{field: value})
    result = scenario.request("admin", "GET", "/admin/users", 200,
                              params={"q": marker.upper(), "paginated": True})
    assert set(result) == {"total", "page", "limit", "offset", "items"}
    assert result["total"] == 1
    assert result["items"] == [user]
    assert set(user) == PUBLIC_USER_FIELDS


def test_catalog_pagination_empty_query_and_legacy_compatibility(scenario):
    marker = uuid4().hex
    users = [create(scenario, first_name=marker) for _ in range(5)]
    for offset, expected in [(0, users[:2]), (2, users[2:4]), (4, users[4:]), (20, [])]:
        page = scenario.request("admin", "GET", "/admin/users", 200,
                                params={"q": marker, "paginated": True, "limit": 2, "offset": offset})
        assert page == {"total": 5, "page": offset // 2 + 1, "limit": 2, "offset": offset, "items": expected}
    legacy = scenario.request("admin", "GET", "/admin/users?limit=10&offset=0", 200)
    envelope = scenario.request("admin", "GET", "/admin/users?limit=10&paginated=true&q=%20%20", 200)
    assert isinstance(legacy, list)
    assert legacy == envelope["items"]
    assert all(set(user) == PUBLIC_USER_FIELDS for user in legacy)
    assert envelope["total"] >= 5
    empty = scenario.request("admin", "GET", "/admin/users", 200, params={"q": uuid4().hex, "paginated": True})
    assert empty["total"] == 0 and empty["items"] == []


@pytest.mark.parametrize("literal", ["%", "_", "\\", "' OR 1=1 --"])
def test_search_wildcards_and_sql_are_literal(scenario, literal):
    marker = uuid4().hex
    user = create(scenario, first_name=marker + literal)
    create(scenario, first_name=marker + "replacement")
    result = scenario.request("admin", "GET", "/admin/users", 200,
                              params={"q": marker + literal, "paginated": True})
    assert result["total"] == 1 and result["items"] == [user]


def test_detail_and_effective_permissions_follow_real_changes(scenario, permission_matrix_guard):
    user = create(scenario)
    path = f"/admin/users/{user['id']}"
    assert scenario.request("admin", "GET", path, 200) == user
    scenario.request("admin", "PUT", path + "/roles", 200, json={"role_codes": ["student", "department"]})
    before = scenario.request("admin", "GET", path + "/effective-permissions", 200)
    assert set(before) == {"user_id", "role_codes", "permission_codes"}
    assert before["role_codes"] == ["department", "student"]
    assert "plans.propose" in before["permission_codes"]
    scenario.request("admin", "PUT", "/admin/roles/student/permissions", 200,
                     json={"permission_codes": sorted(permission_matrix_guard["student"] - {"plans.propose"})})
    after = scenario.request("admin", "GET", path + "/effective-permissions", 200)
    assert "plans.propose" not in after["permission_codes"]
    assert after["permission_codes"] == sorted(set(after["permission_codes"]))
    with scenario.engine.connect() as connection:
        expected = connection.execute(text("""
            SELECT DISTINCT p.code FROM public.user_roles ur
            JOIN public.role_permissions rp ON rp.role_id=ur.role_id
            JOIN public.permissions p ON p.id=rp.permission_id
            WHERE ur.user_id=:id ORDER BY p.code
        """), {"id": user["id"]}).scalars().all()
    assert after["permission_codes"] == expected
    scenario.request("admin", "PATCH", path + "/status", 200, json={"is_active": False})
    assert scenario.request("admin", "GET", path, 200)["is_active"] is False
    assert scenario.request("admin", "GET", path + "/effective-permissions", 200) == after
    before_snapshot = scenario.snapshot()
    scenario.request("admin", "GET", path + "/effective-permissions", 200)
    scenario.request("admin", "GET", "/admin/users?paginated=true", 200)
    assert scenario.snapshot() == before_snapshot


def test_catalog_detail_and_effective_authorization_precedes_existence(scenario, permission_matrix_guard):
    existing = scenario.actors["student"]["id"]
    for path in ["/admin/users?paginated=true", f"/admin/users/{existing}", "/admin/users/9223372036854775807",
                 f"/admin/users/{existing}/effective-permissions", "/admin/users/9223372036854775807/effective-permissions"]:
        assert scenario.client.get(path).status_code == 401
        for actor in ["student", "teacher", "supervisor", "department"]:
            scenario.request(actor, "GET", path, 403)
    for suffix in ["", "/effective-permissions"]:
        scenario.request("admin", "GET", "/admin/users/9223372036854775807" + suffix, 404)
        for invalid in ["0", "-1", "9223372036854775808"]:
            scenario.request("admin", "GET", "/admin/users/" + invalid + suffix, 422)
    scenario.request("account_manager", "GET", f"/admin/users/{existing}", 200)
    scenario.request("account_manager", "GET", f"/admin/users/{existing}/effective-permissions", 403)
    # Only the disposable permission matrix is altered, then restored by the guard.
    user = scenario.actors["admin2"]["id"]
    with scenario.engine.begin() as connection:
        connection.execute(text("""
            DELETE FROM public.role_permissions
            WHERE role_id=(SELECT id FROM public.roles WHERE code='admin')
              AND permission_id=(SELECT id FROM public.permissions WHERE code='users.manage')
        """))
    scenario.request("admin2", "GET", f"/admin/users/{existing}/effective-permissions", 403)
    scenario.request("admin2", "GET", "/admin/users/9223372036854775807/effective-permissions", 403)
    with scenario.engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO public.role_permissions(role_id,permission_id)
            SELECT r.id,p.id FROM public.roles r CROSS JOIN public.permissions p
            WHERE r.code='admin' AND p.code='users.manage'
        """))
        connection.execute(text("""
            DELETE FROM public.role_permissions
            WHERE role_id=(SELECT id FROM public.roles WHERE code='admin')
              AND permission_id=(SELECT id FROM public.permissions WHERE code='roles.manage')
        """))
    scenario.request("admin2", "GET", f"/admin/users/{existing}/effective-permissions", 403)
    with scenario.engine.begin() as connection:
        connection.execute(text("UPDATE public.users SET is_active=false WHERE id=:id"), {"id": user})
    scenario.request("admin2", "GET", f"/admin/users/{existing}/effective-permissions", 401)


def test_query_validation_and_self_protections_preserved(scenario):
    for query in [{"limit": 0}, {"limit": 101}, {"offset": -1}, {"q": "x" * 255}]:
        scenario.request("admin", "GET", "/admin/users", 422, params=query)
    admin_id = scenario.actors["admin"]["id"]
    detail = scenario.request("admin", "GET", f"/admin/users/{admin_id}", 200)
    effective = scenario.request("admin", "GET", f"/admin/users/{admin_id}/effective-permissions", 200)
    assert "admin" in detail["roles"]
    assert {"users.manage", "roles.manage"}.issubset(effective["permission_codes"])
    before = scenario.snapshot()
    scenario.request("admin", "PATCH", f"/admin/users/{admin_id}/status", 403, json={"is_active": False})
    scenario.request("admin", "PUT", f"/admin/users/{admin_id}/roles", 403, json={"role_codes": ["department"]})
    assert scenario.snapshot() == before
