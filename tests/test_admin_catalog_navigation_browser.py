from uuid import uuid4

from playwright.sync_api import expect

from test_web_ui_admin_browser import enter, section
from test_web_ui_browser import browser_page


def create_account(case, *, email, first_name):
    return case.request("admin", "POST", "/admin/users", 201, json={
        "email": email,
        "password": "integration-only-not-a-real-password",
        "first_name": first_name,
        "last_name": "Navigation",
        "role_codes": ["department"],
    })


def search(page, query):
    section(page, "Учетные записи")
    field = page.get_by_label("Поиск по email, имени или фамилии", exact=True)
    field.fill(query)
    page.get_by_role("button", name="Найти учетные записи", exact=True).click()
    expect(page.get_by_role("heading", name="Учетные записи", exact=True)).to_be_visible()
    expect(page.get_by_text("Всего:", exact=False)).to_be_visible()
    return field


def expect_catalog_context(page, query, offset):
    expect(page.get_by_label("Поиск по email, имени или фамилии", exact=True)).to_have_value(query)
    expect(page.get_by_label("Смещение страницы", exact=True)).to_have_value(str(offset))
    expect(page.get_by_role("table", name="Учетные записи", exact=True)).to_be_visible()


def open_second_page_target(page, user_id):
    page.get_by_role("button", name="Далее", exact=True).click()
    expect(page.get_by_label("Смещение страницы", exact=True)).to_have_value("10")
    page.get_by_role("button", name=f"Открыть учетную запись {user_id}", exact=True).click()
    expect(page.get_by_role("heading", name="Детали учетной записи", exact=True)).to_be_visible()


def test_detail_return_restores_search_and_nonzero_offset(browser_page, scenario):
    page, case = browser_page, scenario
    marker = uuid4().hex
    users = [
        create_account(case, email=f"{marker}-{index}@integration.invalid", first_name=f"Nav{marker}")
        for index in range(12)
    ]
    enter(page, case.actors["admin"]["email"])
    search(page, marker)
    expect(page.get_by_text(f"Всего: 12. Страница: 1. Размер: 10.", exact=True)).to_be_visible()
    open_second_page_target(page, users[-1]["id"])
    page.get_by_role("button", name="К учетным записям", exact=True).click()
    expect_catalog_context(page, marker, 10)
    expect(page.get_by_role("button", name=f"Открыть учетную запись {users[-1]['id']}", exact=True)).to_be_visible()


def test_cancel_edit_and_status_restores_origin_context(browser_page, scenario):
    page, case = browser_page, scenario
    marker = uuid4().hex
    users = [
        create_account(case, email=f"{marker}-{index}@integration.invalid", first_name=f"Cancel{marker}")
        for index in range(11)
    ]
    target = users[-1]
    enter(page, case.actors["admin"]["email"])
    search(page, marker)
    open_second_page_target(page, target["id"])
    page.get_by_role("button", name="К учетным записям", exact=True).click()
    page.get_by_role("button", name="Создать учетную запись", exact=True).click()
    page.get_by_role("button", name="Вернуться без отправки", exact=True).click()
    expect_catalog_context(page, marker, 10)
    page.get_by_role("button", name=f"Изменить данные {target['id']}", exact=True).click()
    page.get_by_role("button", name="Вернуться без отправки", exact=True).click()
    expect_catalog_context(page, marker, 10)
    page.get_by_role("button", name=f"Заблокировать {target['id']}", exact=True).click()
    page.get_by_role("button", name="Вернуться без отправки", exact=True).click()
    expect_catalog_context(page, marker, 10)
    assert case.request("admin", "GET", f"/admin/users/{target['id']}", 200)["is_active"] is True


def test_roles_and_permissions_return_through_captured_account_context(browser_page, scenario):
    page, case = browser_page, scenario
    marker = uuid4().hex
    user = create_account(case, email=f"{marker}@integration.invalid", first_name="RoleContext")
    enter(page, case.actors["admin"]["email"])
    search(page, marker)
    page.get_by_role("button", name=f"Роли учетной записи {user['id']}", exact=True).click()
    expect(page.get_by_role("heading", name=f"Роли учетной записи № {user['id']}", exact=True)).to_be_visible()
    page.get_by_role("button", name="К учетным записям", exact=True).click()
    expect_catalog_context(page, marker, 0)
    page.get_by_role("button", name=f"Открыть учетную запись {user['id']}", exact=True).click()
    page.get_by_role("button", name=f"Разрешения роли department", exact=True).click()
    expect(page.get_by_role("heading", name="Разрешения роли department", exact=True)).to_be_visible()
    page.get_by_role("button", name="К деталям учетной записи", exact=True).click()
    expect(page.get_by_role("heading", name="Детали учетной записи", exact=True)).to_be_visible()
    page.get_by_role("button", name="К учетным записям", exact=True).click()
    expect_catalog_context(page, marker, 0)


def test_account_roles_opened_from_role_catalog_returns_to_role_catalog(browser_page, scenario):
    page, case = browser_page, scenario
    user_id = case.actors["student"]["id"]
    enter(page, case.actors["admin"]["email"])
    section(page, "Роли")
    page.get_by_label("Учетная запись ID", exact=True).fill(str(user_id))
    page.get_by_role("button", name="Открыть роли учетной записи", exact=True).click()
    expect(page.get_by_role("heading", name=f"Роли учетной записи № {user_id}", exact=True)).to_be_visible()
    page.get_by_role("button", name="К ролям", exact=True).click()
    expect(page.get_by_role("heading", name="Роли", exact=True)).to_be_visible()


def test_mutation_that_preserves_filter_refreshes_detail_and_page(browser_page, scenario):
    page, case = browser_page, scenario
    marker = uuid4().hex
    users = [
        create_account(case, email=f"{marker}-{index}@integration.invalid", first_name=f"Still{marker}")
        for index in range(11)
    ]
    target = users[-1]
    enter(page, case.actors["admin"]["email"])
    search(page, marker)
    open_second_page_target(page, target["id"])
    page.get_by_role("button", name="К учетным записям", exact=True).click()
    page.get_by_role("button", name=f"Изменить данные {target['id']}", exact=True).click()
    page.get_by_label("Фамилия", exact=True).fill("Updated surname")
    page.get_by_role("button", name="Проверить и подтвердить", exact=True).click()
    page.get_by_role("button", name="Подтвердить отправку", exact=True).click()
    expect(page.get_by_text("Операция подтверждена сервером.", exact=True)).to_be_visible()
    expect_catalog_context(page, marker, 10)
    expect(page.get_by_role("region", name=f"Учетная запись {target['id']}", exact=True)).to_contain_text("Updated surname")
    assert case.request("admin", "GET", f"/admin/users/{target['id']}", 200)["last_name"] == "Updated surname"
    expect(page.get_by_text("больше не соответствует текущему поиску", exact=False)).to_have_count(0)


def test_status_mutation_refreshes_context_and_fresh_account(browser_page, scenario):
    page, case = browser_page, scenario
    marker = uuid4().hex
    user = create_account(case, email=f"{marker}@integration.invalid", first_name="StatusContext")
    enter(page, case.actors["admin"]["email"])
    search(page, marker)
    page.get_by_role("button", name=f"Заблокировать {user['id']}", exact=True).click()
    page.get_by_role("button", name="Проверить и подтвердить", exact=True).click()
    page.get_by_role("button", name="Подтвердить отправку", exact=True).click()
    expect(page.get_by_text("Операция подтверждена сервером.", exact=True)).to_be_visible()
    expect_catalog_context(page, marker, 0)
    expect(page.get_by_role("region", name=f"Учетная запись {user['id']}", exact=True)).to_contain_text("Заблокирована")
    assert case.request("admin", "GET", f"/admin/users/{user['id']}", 200)["is_active"] is False


def test_filter_departure_shows_fresh_detail_clear_action_and_repairs_offset(browser_page, scenario):
    page, case = browser_page, scenario
    marker = uuid4().hex
    users = [
        create_account(case, email=f"leave-{uuid4().hex}@integration.invalid", first_name=f"Before{marker}")
        for _ in range(11)
    ]
    target = users[-1]
    enter(page, case.actors["admin"]["email"])
    search(page, marker)
    open_second_page_target(page, target["id"])
    page.get_by_role("button", name="К учетным записям", exact=True).click()
    page.get_by_role("button", name=f"Изменить данные {target['id']}", exact=True).click()
    page.get_by_label("Имя", exact=True).fill("AfterFilter")
    page.get_by_role("button", name="Проверить и подтвердить", exact=True).click()
    page.get_by_role("button", name="Подтвердить отправку", exact=True).click()
    expect(page.get_by_text("больше не соответствует текущему поиску", exact=False)).to_be_visible()
    expect_catalog_context(page, marker, 0)
    detail = page.get_by_role("region", name=f"Учетная запись {target['id']}", exact=True)
    expect(detail).to_contain_text("AfterFilter")
    expect(page.get_by_role("table", name="Учетные записи", exact=True)).not_to_contain_text(target["email"])
    assert case.request("admin", "GET", f"/admin/users/{target['id']}", 200)["first_name"] == "AfterFilter"
    page.get_by_role("button", name="Очистить поиск", exact=True).click()
    expect_catalog_context(page, "", 0)
    expect(page.get_by_role("region", name=f"Учетная запись {target['id']}", exact=True)).to_contain_text("AfterFilter")


def test_empty_search_out_of_range_offset_resets_without_repeated_refetch(browser_page, scenario):
    page, case = browser_page, scenario
    enter(page, case.actors["admin"]["email"])
    search(page, uuid4().hex)
    requests = []
    page.on("request", lambda request: requests.append(request.url)
            if request.url.split("?", 1)[0].endswith("/admin/users") else None)
    page.get_by_label("Смещение страницы", exact=True).fill("20")
    page.get_by_role("button", name="Открыть смещение", exact=True).click()
    expect(page.get_by_text("Всего: 0. Страница: 1. Размер: 10.", exact=True)).to_be_visible()
    expect(page.get_by_label("Смещение страницы", exact=True)).to_have_value("0")
    assert len(requests) == 2
    assert page.evaluate("localStorage.length === 0 && sessionStorage.length === 0")


def test_confirmed_edit_refresh_failure_does_not_repeat_and_context_is_session_only(browser_page, scenario):
    page, case = browser_page, scenario
    marker = uuid4().hex
    user = create_account(case, email=f"{marker}@integration.invalid", first_name="BeforeRefresh")
    enter(page, case.actors["admin"]["email"])
    search(page, marker)
    page.get_by_role("button", name=f"Изменить данные {user['id']}", exact=True).click()
    page.get_by_label("Имя", exact=True).fill("AfterRefresh")
    page.get_by_role("button", name="Проверить и подтвердить", exact=True).click()
    mutations = []
    page.on("request", lambda request: mutations.append(request.url) if request.method == "PATCH" else None)
    page.route("**/admin/users?*", lambda route: route.abort("failed"))
    page.get_by_role("button", name="Подтвердить отправку", exact=True).click()
    expect(page.get_by_role("alert")).to_contain_text("Операция уже подтверждена")
    page.unroute("**/admin/users?*")
    page.get_by_role("button", name="Повторить", exact=True).click()
    expect(page.get_by_role("heading", name="Учетные записи", exact=True)).to_be_visible()
    assert len(mutations) == 1
    assert case.request("admin", "GET", f"/admin/users/{user['id']}", 200)["first_name"] == "AfterRefresh"
    page.get_by_role("button", name="Выйти", exact=True).click()
    enter(page, case.actors["outsider"]["email"])
    expect(page.get_by_role("heading", name="Проекты", exact=True)).to_be_visible()
    assert page.evaluate("localStorage.length === 0 && sessionStorage.length === 0")
    page.get_by_role("button", name="Выйти", exact=True).click()
    enter(page, case.actors["admin"]["email"])
    page.get_by_role("navigation").get_by_role("button", name="Учетные записи", exact=True).click()
    expect_catalog_context(page, "", 0)


def test_late_detail_response_cannot_restore_previous_catalog_in_new_session(browser_page, scenario):
    page, case = browser_page, scenario
    marker = uuid4().hex
    user = create_account(case, email=f"{marker}@integration.invalid", first_name="LateContext")
    enter(page, case.actors["admin"]["email"])
    search(page, marker)
    page.evaluate("""() => {
      const original = window.fetch;
      window.fetch = async (...args) => {
        const response = await original(...args);
        if (/^\\/admin\\/users\\/\\d+$/.test(String(args[0]))) {
          document.body.dataset.catalogDetailReceived = 'true';
          await new Promise(resolve => { window.releaseCatalogDetail = resolve; });
        }
        return response;
      };
    }""")
    page.get_by_role("button", name=f"Открыть учетную запись {user['id']}", exact=True).click()
    expect(page.locator("body")).to_have_attribute("data-catalog-detail-received", "true")
    page.get_by_role("button", name="Выйти", exact=True).click()
    enter(page, case.actors["outsider"]["email"])
    page.evaluate("window.releaseCatalogDetail()")
    expect(page.get_by_role("heading", name="Проекты", exact=True)).to_be_visible()
    expect(page.get_by_role("region", name=f"Учетная запись {user['id']}", exact=True)).to_have_count(0)
    assert marker not in page.locator("#content").inner_text()
    assert page.evaluate("localStorage.length === 0 && sessionStorage.length === 0")
