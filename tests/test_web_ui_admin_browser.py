from uuid import uuid4

import pytest
from playwright.sync_api import expect
from sqlalchemy import text

from conftest import TEST_PASSWORD
from test_role_management_integration import permission_matrix_guard
from test_web_ui_browser import browser_page


def enter(page, email, password=TEST_PASSWORD):
    page.get_by_label("Email", exact=True).fill(email)
    page.get_by_label("Пароль", exact=True).fill(password)
    page.get_by_role("button", name="Войти", exact=True).click()
    expect(page.get_by_role("button", name="Выйти", exact=True)).to_be_visible()
    expect(page.get_by_role("heading", name="Проекты", exact=True).or_(
        page.get_by_role("heading", name="Уведомления", exact=True))).to_be_visible()


def switch(page, email):
    page.get_by_role("button", name="Выйти", exact=True).click()
    enter(page, email)


def section(page, name):
    page.get_by_role("navigation").get_by_role("button", name=name, exact=True).click()
    expect(page.get_by_role("heading", name=name, exact=True)).to_be_visible()


def account_page(page, case, user_id):
    section(page, "Учетные записи")
    # Calculate only a fixture-owned account's current page, never a fixed ID/offset.
    with case.engine.connect() as connection:
        preceding = connection.execute(text("SELECT count(*) FROM public.users WHERE id < :id"), {"id": user_id}).scalar_one()
    offset = preceding // 10 * 10
    page.get_by_label("Смещение страницы", exact=True).fill(str(offset))
    page.get_by_role("button", name="Открыть смещение", exact=True).click()
    expect(page.get_by_text(f"Смещение: {offset}", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name=f"Изменить данные {user_id}", exact=True)).to_be_visible()


def prepare(page):
    page.get_by_role("button", name="Проверить и подтвердить", exact=True).click()
    expect(page.get_by_role("region", name="Подтверждение", exact=True)).to_be_visible()


def confirm(page):
    prepare(page)
    page.get_by_role("button", name="Подтвердить отправку", exact=True).click()
    expect(page.get_by_text("Операция подтверждена сервером.", exact=True)).to_be_visible()


def create_form(page, email, role="department"):
    section(page, "Учетные записи")
    page.get_by_role("button", name="Создать учетную запись", exact=True).click()
    page.get_by_label("Email учетной записи", exact=True).fill(email)
    page.get_by_label("Имя", exact=True).fill("UI <script>window.injected=true</script>")
    page.get_by_label("Фамилия", exact=True).fill("Administration")
    page.get_by_label("Начальный пароль", exact=True).fill(TEST_PASSWORD)
    page.get_by_role("group", name="Начальные роли", exact=True).locator(f'input[name="{role}"]').check()


def open_user_roles(page, user_id):
    section(page, "Роли")
    page.get_by_label("Учетная запись ID", exact=True).fill(str(user_id))
    page.get_by_role("button", name="Открыть роли учетной записи", exact=True).click()
    expect(page.get_by_role("heading", name=f"Роли учетной записи № {user_id}", exact=True)).to_be_visible()


def open_role_permissions(page, role):
    section(page, "Роли")
    page.get_by_role("button", name=f"Разрешения роли {role}", exact=True).click()
    expect(page.get_by_role("heading", name=f"Разрешения роли {role}", exact=True)).to_be_visible()


def user_snapshot(case, user_id):
    with case.engine.connect() as connection:
        user = tuple(connection.execute(text("SELECT * FROM public.users WHERE id = :id"), {"id": user_id}).one())
        roles = tuple(connection.execute(text("SELECT * FROM public.user_roles WHERE user_id = :id ORDER BY role_id"), {"id": user_id}).all())
        events = tuple(connection.execute(text("SELECT * FROM public.audit_events WHERE entity_id = :id AND entity_type = 'users' ORDER BY id"), {"id": user_id}).all())
        return user, roles, events


def test_admin_journey_roles_permissions_effective_and_status(browser_page, scenario, permission_matrix_guard):
    page, case = browser_page, scenario
    original = permission_matrix_guard["student"]
    case.request("admin", "PUT", "/admin/roles/student/permissions", 200,
                 json={"permission_codes": sorted(original - {"plans.propose"})})
    enter(page, case.actors["admin"]["email"])
    email = f"ui-{uuid4().hex}@integration.invalid"
    create_form(page, email)
    prepare(page)
    expect(page.get_by_label("Начальный пароль", exact=True)).to_have_value("")
    assert TEST_PASSWORD not in page.locator("body").inner_text()
    with page.expect_response(lambda response: response.url.endswith("/admin/users") and response.request.method == "POST") as created:
        page.get_by_role("button", name="Подтвердить отправку", exact=True).click()
    user_id = created.value.json()["id"]
    expect(page.get_by_text("Операция подтверждена сервером.", exact=True)).to_be_visible()
    account_page(page, case, user_id)
    expect(page.get_by_role("table", name="Учетные записи", exact=True)).to_contain_text(email)
    assert not page.evaluate("Boolean(window.injected)")
    open_user_roles(page, user_id)
    page.get_by_role("button", name="Назначить роль student", exact=True).click()
    confirm(page)
    expect(page.get_by_role("table", name="Назначенные роли", exact=True)).to_contain_text("student")
    switch(page, email)
    section(page, "Мои эффективные права")
    expect(page.get_by_role("table", name="Эффективные разрешения", exact=True)).not_to_contain_text("plans.propose")
    switch(page, case.actors["admin"]["email"])
    open_role_permissions(page, "student")
    page.get_by_role("button", name="Назначить разрешение plans.propose", exact=True).click()
    confirm(page)
    switch(page, email)
    section(page, "Мои эффективные права")
    expect(page.get_by_role("table", name="Эффективные разрешения", exact=True)).to_contain_text("plans.propose")
    switch(page, case.actors["admin"]["email"])
    open_role_permissions(page, "student")
    page.get_by_role("button", name="Снять разрешение plans.propose", exact=True).click()
    confirm(page)
    switch(page, email)
    section(page, "Мои эффективные права")
    expect(page.get_by_role("table", name="Эффективные разрешения", exact=True)).not_to_contain_text("plans.propose")
    switch(page, case.actors["admin"]["email"])
    open_user_roles(page, user_id)
    page.get_by_role("button", name="Снять роль student", exact=True).click()
    confirm(page)
    expect(page.get_by_role("table", name="Назначенные роли", exact=True)).not_to_contain_text("student")
    account_page(page, case, user_id)
    page.get_by_role("button", name=f"Заблокировать {user_id}", exact=True).click()
    confirm(page)
    account_page(page, case, user_id)
    expect(page.get_by_role("button", name=f"Реактивировать {user_id}", exact=True)).to_be_enabled()
    page.get_by_role("button", name=f"Реактивировать {user_id}", exact=True).click()
    confirm(page)
    account_page(page, case, user_id)
    expect(page.get_by_role("button", name=f"Заблокировать {user_id}", exact=True)).to_be_enabled()
    assert page.evaluate("localStorage.length === 0 && sessionStorage.length === 0")


def test_partial_account_edit_and_safe_response(browser_page, scenario):
    page, case = browser_page, scenario
    user_id = case.actors["student"]["id"]
    case.request("admin", "PATCH", f"/admin/users/{user_id}", 200,
                 json={"middle_name": "Old", "study_group": "Old group"})
    before = user_snapshot(case, user_id)
    enter(page, case.actors["admin"]["email"])
    account_page(page, case, user_id)
    page.get_by_role("button", name=f"Изменить данные {user_id}", exact=True).click()
    page.get_by_label("Имя", exact=True).fill("Updated")
    page.get_by_label("Отчество", exact=True).fill("")
    page.get_by_label("Учебная группа", exact=True).fill("")
    prepare(page)
    with page.expect_request(lambda request: request.url.endswith(f"/admin/users/{user_id}") and request.method == "PATCH") as sent:
        page.get_by_role("button", name="Подтвердить отправку", exact=True).click()
    assert sent.value.post_data_json == {"first_name": "Updated", "middle_name": None, "study_group": None}
    expect(page.get_by_text("Операция подтверждена сервером.", exact=True)).to_be_visible()
    after = user_snapshot(case, user_id)
    assert before[1] == after[1]
    assert len(after[2]) == len(before[2]) + 1
    assert page.get_by_label("Начальный пароль", exact=True).count() == 0
    assert TEST_PASSWORD not in page.locator("body").inner_text()
    assert "password_hash" not in page.locator("body").inner_text()


@pytest.mark.parametrize("actor,users,roles,permissions", [
    ("student", False, False, False), ("supervisor", False, False, False),
    ("teacher", False, False, False), ("teacher_department", False, False, False),
    ("admin", True, True, True), ("account_manager", True, True, False),
])
def test_admin_navigation_uses_permissions_and_admin_role(browser_page, scenario, actor, users, roles, permissions):
    page, case = browser_page, scenario
    enter(page, case.actors[actor]["email"])
    nav = page.get_by_role("navigation")
    for name, shown in [("Учетные записи", users), ("Роли", roles), ("Разрешения", permissions)]:
        expect(nav.get_by_role("button", name=name, exact=True)).to_have_count(int(shown))
    section(page, "Мои эффективные права")
    if actor == "account_manager":
        section(page, "Роли")
        expect(page.get_by_text("Изменение ролей и просмотр разрешений требуют", exact=False)).to_be_visible()


def test_creation_validation_confirmation_cancel_and_keyboard(browser_page, scenario):
    page, case = browser_page, scenario
    page.set_viewport_size({"width": 360, "height": 800})
    enter(page, case.actors["admin"]["email"])
    email = f"cancel-{uuid4().hex}@integration.invalid"
    create_form(page, email)
    page.get_by_role("group", name="Начальные роли", exact=True).locator('input[name="department"]').uncheck()
    page.get_by_role("button", name="Проверить и подтвердить", exact=True).click()
    expect(page.get_by_role("alert")).to_have_text("Выберите от 1 до 5 ролей.")
    page.get_by_role("group", name="Начальные роли", exact=True).locator('input[name="department"]').check()
    page.get_by_role("button", name="Проверить и подтвердить", exact=True).press("Enter")
    expect(page.get_by_role("button", name="Подтвердить отправку", exact=True)).to_be_focused()
    expect(page.get_by_label("Начальный пароль", exact=True)).to_have_value("")
    assert TEST_PASSWORD not in page.locator("body").inner_text()
    page.get_by_role("button", name="Подтвердить отправку", exact=True).press("Tab")
    page.get_by_role("button", name="Отмена", exact=True).press("Enter")
    expect(page.get_by_role("button", name="Проверить и подтвердить", exact=True)).to_be_focused()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    with case.engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM public.users WHERE email = :email"), {"email": email}).scalar_one() == 0
    page.get_by_role("button", name="Выйти", exact=True).click()
    expect(page.get_by_label("Email учетной записи", exact=True)).to_have_count(0)


@pytest.mark.parametrize("status", [403, 404])
def test_real_stale_admin_form_rejections(browser_page, scenario, status):
    page, case = browser_page, scenario
    enter(page, case.actors["admin"]["email"])
    removed = None
    try:
        if status == 403:
            open_user_roles(page, case.actors["student"]["id"])
            page.get_by_role("button", name="Назначить роль department", exact=True).click()
            prepare(page)
            case.request("admin2", "PUT", f"/admin/users/{case.actors['admin']['id']}/roles", 200,
                         json={"role_codes": ["department"]})
            path = f"/admin/users/{case.actors['student']['id']}/roles"
        else:
            unused = case.request("admin", "POST", "/admin/users", 201, json={
                "email": f"missing-{uuid4().hex}@integration.invalid", "password": TEST_PASSWORD,
                "first_name": "Unused", "last_name": "Fixture", "role_codes": ["department"],
            })
            user_id = unused["id"]
            account_page(page, case, user_id)
            page.get_by_role("button", name=f"Изменить данные {user_id}", exact=True).click()
            page.get_by_label("Имя", exact=True).fill("Must not be saved")
            prepare(page)
            # Only a fixture-owned, unassigned user; save it for restoration.
            with case.engine.begin() as connection:
                connection.execute(text("DELETE FROM public.user_roles WHERE user_id = :id"), {"id": user_id})
                removed = connection.execute(text("DELETE FROM public.users WHERE id = :id RETURNING *"), {"id": user_id}).mappings().one()
            path = f"/admin/users/{user_id}"
        before = case.snapshot()
        with page.expect_response(lambda response: response.url.endswith(path) and response.request.method in ("PUT", "PATCH")) as rejected:
            page.get_by_role("button", name="Подтвердить отправку", exact=True).click()
        assert rejected.value.status == status
        expect(page.get_by_role("alert")).to_contain_text("Недостаточно прав" if status == 403 else "Ресурс не найден")
        assert case.snapshot() == before
    finally:
        if removed is not None:
            with case.engine.begin() as connection:
                connection.execute(text("""
                    INSERT INTO public.users (id,email,password_hash,first_name,last_name,middle_name,study_group,is_active,created_at)
                    OVERRIDING SYSTEM VALUE
                    VALUES (:id,:email,:password_hash,:first_name,:last_name,:middle_name,:study_group,:is_active,:created_at)
                """), dict(removed))
                connection.execute(text("""
                    INSERT INTO public.user_roles (user_id,role_id)
                    SELECT :id,id FROM public.roles WHERE code = 'department'
                """), {"id": removed["id"]})


def test_self_administration_controls_and_project_role_conflict(browser_page, scenario):
    page, case = browser_page, scenario
    enter(page, case.actors["admin"]["email"])
    own_id = case.actors["admin"]["id"]
    account_page(page, case, own_id)
    expect(page.get_by_role("button", name=f"Заблокировать {own_id}", exact=True)).to_be_disabled()
    open_user_roles(page, own_id)
    expect(page.get_by_role("button", name="Снять роль admin", exact=True)).to_be_disabled()
    expect(page.get_by_role("button", name="Назначить роль student", exact=True)).to_be_disabled()
    target = case.actors["student"]["id"]
    case.request("admin", "PUT", f"/admin/users/{target}/roles", 200, json={"role_codes": ["student", "department"]})
    open_user_roles(page, target)
    page.get_by_role("button", name="Снять роль student", exact=True).click()
    before = case.snapshot()
    prepare(page)
    page.get_by_role("button", name="Подтвердить отправку", exact=True).click()
    expect(page.get_by_role("alert")).to_contain_text("Конфликт")
    assert case.snapshot() == before


@pytest.mark.parametrize("permission", ["users.manage", "roles.manage"])
def test_required_admin_permission_cannot_be_removed(browser_page, scenario, permission):
    page, case = browser_page, scenario
    enter(page, case.actors["admin"]["email"])
    open_role_permissions(page, "admin")
    page.get_by_role("button", name=f"Снять разрешение {permission}", exact=True).click()
    before = case.snapshot()
    prepare(page)
    page.get_by_role("button", name="Подтвердить отправку", exact=True).click()
    expect(page.get_by_role("alert")).to_contain_text("Конфликт")
    assert case.snapshot() == before


def test_admin_cannot_receive_academic_permission(browser_page, scenario):
    page, case = browser_page, scenario
    enter(page, case.actors["admin"]["email"])
    open_role_permissions(page, "admin")
    page.get_by_role("button", name="Назначить разрешение projects.read", exact=True).click()
    before = case.snapshot()
    prepare(page)
    with page.expect_response(lambda response: response.url.endswith("/admin/roles/admin/permissions") and response.request.method == "PUT") as rejected:
        page.get_by_role("button", name="Подтвердить отправку", exact=True).click()
    assert rejected.value.status == 422
    expect(page.get_by_role("alert")).to_have_text("Некорректные параметры. Проверьте поля формы.")
    assert case.snapshot() == before


def test_multirole_session_refreshes_menu_and_effective_permissions(browser_page, scenario, permission_matrix_guard):
    page, case = browser_page, scenario
    case.request("admin2", "PUT", f"/admin/users/{case.actors['admin']['id']}/roles", 200,
                 json={"role_codes": ["admin", "teacher"]})
    enter(page, case.actors["admin"]["email"])
    nav = page.get_by_role("navigation")
    expect(nav.get_by_role("button", name="Проекты", exact=True)).to_be_visible()
    open_role_permissions(page, "teacher")
    page.get_by_role("button", name="Снять разрешение projects.read", exact=True).click()
    confirm(page)
    expect(nav.get_by_role("button", name="Проекты", exact=True)).to_have_count(0)
    section(page, "Мои эффективные права")
    expect(page.get_by_role("table", name="Эффективные разрешения", exact=True)).not_to_contain_text("projects.read")
    open_role_permissions(page, "teacher")
    page.get_by_role("button", name="Назначить разрешение projects.read", exact=True).click()
    confirm(page)
    expect(nav.get_by_role("button", name="Проекты", exact=True)).to_be_visible()
    section(page, "Мои эффективные права")
    expect(page.get_by_role("table", name="Эффективные разрешения", exact=True)).to_contain_text("projects.read")


def test_repeated_block_is_idempotent_and_reactivation_restores_token(browser_page, scenario):
    page, case = browser_page, scenario
    user_id = case.actors["student"]["id"]
    enter(page, case.actors["admin"]["email"])
    account_page(page, case, user_id)
    page.get_by_role("button", name=f"Заблокировать {user_id}", exact=True).click()
    prepare(page)
    case.request("admin", "PATCH", f"/admin/users/{user_id}/status", 200, json={"is_active": False})
    before = user_snapshot(case, user_id)
    page.get_by_role("button", name="Подтвердить отправку", exact=True).click()
    expect(page.get_by_text("Операция подтверждена сервером.", exact=True)).to_be_visible()
    assert user_snapshot(case, user_id) == before
    case.request("student", "GET", "/auth/me", 401)
    account_page(page, case, user_id)
    page.get_by_role("button", name=f"Реактивировать {user_id}", exact=True).click()
    confirm(page)
    case.request("student", "GET", "/auth/me", 200)


def test_last_active_admin_cannot_be_blocked_by_nonadmin_manager(browser_page, scenario):
    page, case = browser_page, scenario
    target = case.actors["admin"]["id"]
    with case.engine.connect() as connection:
        others = connection.execute(text("""
            SELECT DISTINCT u.id FROM public.users u JOIN public.user_roles ur ON ur.user_id=u.id
            JOIN public.roles r ON r.id=ur.role_id
            WHERE r.code='admin' AND u.is_active AND u.id<>:id
        """), {"id": target}).scalars().all()
    try:
        with case.engine.begin() as connection:
            connection.execute(text("UPDATE public.users SET is_active=false WHERE id=ANY(:ids)"), {"ids": others})
        enter(page, case.actors["account_manager"]["email"])
        account_page(page, case, target)
        page.get_by_role("button", name=f"Заблокировать {target}", exact=True).click()
        before = user_snapshot(case, target)
        prepare(page)
        with page.expect_response(lambda response: response.url.endswith(f"/admin/users/{target}/status") and response.request.method == "PATCH") as rejected:
            page.get_by_role("button", name="Подтвердить отправку", exact=True).click()
        assert rejected.value.status == 409
        expect(page.get_by_role("alert")).to_contain_text("Конфликт")
        expect(page.get_by_role("alert")).not_to_contain_text("данные изменились")
        assert user_snapshot(case, target) == before
    finally:
        with case.engine.begin() as connection:
            connection.execute(text("UPDATE public.users SET is_active=true WHERE id=ANY(:ids)"), {"ids": others})


def test_edit_validation_and_email_conflict_without_partial_changes(browser_page, scenario):
    page, case = browser_page, scenario
    target = case.actors["student"]["id"]
    enter(page, case.actors["admin"]["email"])
    account_page(page, case, target)
    page.get_by_role("button", name=f"Изменить данные {target}", exact=True).click()
    before = user_snapshot(case, target)
    page.get_by_label("Имя", exact=True).fill("   ")
    page.get_by_role("button", name="Проверить и подтвердить", exact=True).click()
    expect(page.get_by_role("alert")).to_have_text("Имя и фамилия не должны быть пустыми.")
    assert user_snapshot(case, target) == before
    page.get_by_label("Имя", exact=True).fill("Must rollback")
    page.get_by_label("Email учетной записи", exact=True).fill("not-email")
    page.get_by_role("button", name="Проверить и подтвердить", exact=True).click()
    assert not page.get_by_label("Email учетной записи", exact=True).evaluate("(input) => input.checkValidity()")
    page.get_by_label("Email учетной записи", exact=True).fill(case.actors["outsider"]["email"])
    prepare(page)
    with page.expect_response(lambda response: response.url.endswith(f"/admin/users/{target}") and response.request.method == "PATCH") as rejected:
        page.get_by_role("button", name="Подтвердить отправку", exact=True).click()
    assert rejected.value.status == 409
    expect(page.get_by_role("alert")).to_contain_text("Конфликт")
    assert user_snapshot(case, target) == before


@pytest.mark.parametrize("uncertain", [False, True])
def test_double_creation_and_uncertain_delivery_not_repeated(browser_page, scenario, uncertain):
    page, case = browser_page, scenario
    enter(page, case.actors["admin"]["email"])
    email = f"double-{uuid4().hex}@integration.invalid"
    create_form(page, email)
    prepare(page)
    sent = []
    page.on("request", lambda request: sent.append(request.url) if request.url.endswith("/admin/users") and request.method == "POST" else None)
    if uncertain:
        page.evaluate("""() => {
          const original = window.fetch;
          window.fetch = async (...args) => {
            const response = await original(...args);
            if (String(args[0]) === '/admin/users' && args[1]?.method === 'POST') throw new TypeError('controlled delivery loss');
            return response;
          };
        }""")
    page.get_by_role("button", name="Подтвердить отправку", exact=True).evaluate("(button) => { button.click(); button.click(); }")
    if uncertain:
        expect(page.get_by_role("alert")).to_contain_text("результат неизвестен")
        expect(page.get_by_label("Начальный пароль", exact=True)).to_have_value("")
        page.get_by_role("button", name="Проверить актуальные данные", exact=True).click()
    expect(page.get_by_role("heading", name="Учетные записи", exact=True)).to_be_visible()
    with case.engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM public.users WHERE email = :email"), {"email": email}).scalar_one() == 1
    assert len(sent) == 1


def test_admin_success_refresh_failure_does_not_reapply(browser_page, scenario):
    page, case = browser_page, scenario
    enter(page, case.actors["admin"]["email"])
    create_form(page, f"refresh-{uuid4().hex}@integration.invalid")
    prepare(page)
    sent = []
    page.on("request", lambda request: sent.append(request.url) if request.url.endswith("/admin/users") and request.method == "POST" else None)
    page.route("**/admin/users?*", lambda route: route.abort("failed"))
    page.get_by_role("button", name="Подтвердить отправку", exact=True).click()
    expect(page.get_by_role("alert")).to_contain_text("Операция уже подтверждена")
    page.unroute("**/admin/users?*")
    page.get_by_role("button", name="Повторить", exact=True).click()
    expect(page.get_by_role("heading", name="Учетные записи", exact=True)).to_be_visible()
    assert len(sent) == 1


def test_logout_during_admin_mutation_discards_late_result(browser_page, scenario):
    page, case = browser_page, scenario
    enter(page, case.actors["admin"]["email"])
    email = f"late-{uuid4().hex}@integration.invalid"
    create_form(page, email)
    prepare(page)
    page.evaluate("""() => {
      const original = window.fetch;
      window.fetch = async (...args) => {
        const response = await original(...args);
        if (String(args[0]) === '/admin/users' && args[1]?.method === 'POST') {
          document.body.dataset.adminReceived = "true";
          await new Promise(resolve => { window.releaseAdmin = resolve; });
        }
        return response;
      };
    }""")
    page.get_by_role("button", name="Подтвердить отправку", exact=True).click()
    expect(page.locator("body")).to_have_attribute("data-admin-received", "true")
    switch(page, case.actors["student"]["email"])
    page.evaluate("window.releaseAdmin()")
    expect(page.get_by_role("heading", name="Проекты", exact=True)).to_be_visible()
    expect(page.get_by_text(email, exact=False)).to_have_count(0)
    assert TEST_PASSWORD not in page.locator("body").inner_text()
    assert page.evaluate("localStorage.length === 0 && sessionStorage.length === 0")


def test_catalogs_paginate_and_unknown_user_id_is_validated(browser_page, scenario):
    page, case = browser_page, scenario
    enter(page, case.actors["admin"]["email"])
    section(page, "Учетные записи")
    expect(page.get_by_role("table", name="Учетные записи", exact=True).locator("tbody tr")).to_have_count(10)
    page.get_by_role("button", name="Далее", exact=True).click()
    expect(page.get_by_text("Смещение: 10", exact=True)).to_be_visible()
    section(page, "Разрешения")
    expect(page.get_by_role("table", name="Каталог разрешений", exact=True).locator("tbody tr")).to_have_count(10)
    page.get_by_role("button", name="Далее", exact=True).click()
    expect(page.get_by_text("Смещение: 10", exact=True)).to_be_visible()
    expect(page.get_by_role("table", name="Каталог разрешений", exact=True).locator("tbody tr")).to_have_count(2)
    section(page, "Роли")
    expect(page.get_by_role("button", name="Далее", exact=True)).to_be_disabled()
    page.get_by_label("Учетная запись ID", exact=True).fill("9223372036854775808")
    page.get_by_role("button", name="Открыть роли учетной записи", exact=True).click()
    assert not page.get_by_label("Учетная запись ID", exact=True).evaluate("(input) => input.checkValidity()")
    page.get_by_label("Учетная запись ID", exact=True).fill("9223372036854775807")
    page.get_by_role("button", name="Открыть роли учетной записи", exact=True).click()
    expect(page.get_by_role("alert")).to_contain_text("Ресурс не найден")
