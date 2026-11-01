from uuid import uuid4

import pytest
from playwright.sync_api import expect

from test_role_management_integration import permission_matrix_guard
from test_web_ui_admin_browser import enter, section, create_form, prepare, confirm
from test_web_ui_browser import browser_page


def search(page, query):
    section(page, "Учетные записи")
    page.get_by_label("Поиск по email, имени или фамилии", exact=True).fill(query)
    page.get_by_role("button", name="Найти учетные записи", exact=True).click()
    expect(page.get_by_role("table", name="Учетные записи", exact=True)).to_be_visible()


def test_search_created_account_detail_and_roles_permissions(browser_page, scenario, permission_matrix_guard):
    page, case = browser_page, scenario
    enter(page, case.actors["admin"]["email"])
    email = f"detail-{uuid4().hex}@integration.invalid"
    create_form(page, email)
    prepare(page)
    with page.expect_response(lambda response: response.url.endswith("/admin/users") and response.request.method == "POST") as response:
        page.get_by_role("button", name="Подтвердить отправку", exact=True).click()
    user_id = response.value.json()["id"]
    detail = page.get_by_role("region", name=f"Учетная запись {user_id}", exact=True)
    expect(detail).to_contain_text(email)
    expect(detail).to_contain_text("UI <script>window.injected=true</script>")
    expect(detail.locator("script")).to_have_count(0)
    expect(page.get_by_role("table", name="Учетные записи", exact=True)).not_to_contain_text(email)
    assert not page.evaluate("Boolean(window.injected)")
    search(page, email.upper())
    expect(page.get_by_text("Всего: 1. Страница: 1. Размер: 10.", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="Далее", exact=True)).to_be_disabled()
    page.get_by_role("button", name=f"Открыть учетную запись {user_id}", exact=True).click()
    expect(detail).to_contain_text(email)
    expect(detail.get_by_role("table", name="Эффективные права учетной записи", exact=True)).to_contain_text("topics.manage")
    page.get_by_role("button", name="Разрешения роли department", exact=True).click()
    page.get_by_role("button", name="Снять разрешение topics.manage", exact=True).click()
    confirm(page)
    expect(detail.get_by_role("table", name="Эффективные права учетной записи", exact=True)).not_to_contain_text("topics.manage")
    page.get_by_role("button", name="Назначить разрешение topics.manage", exact=True).click()
    confirm(page)
    expect(detail.get_by_role("table", name="Эффективные права учетной записи", exact=True)).to_contain_text("topics.manage")
    detail.get_by_role("button", name=f"Открыть учетную запись {user_id}", exact=True).click()
    page.get_by_role("button", name=f"Роли учетной записи {user_id}", exact=True).click()
    page.get_by_role("button", name="Назначить роль student", exact=True).click()
    confirm(page)
    expect(detail.get_by_role("table", name="Роли учетной записи", exact=True)).to_contain_text("student")
    expect(detail.get_by_role("table", name="Эффективные права учетной записи", exact=True)).to_contain_text("materials.upload")


def test_catalog_total_empty_search_and_keyboard_narrow(browser_page, scenario):
    page, case = browser_page, scenario
    page.set_viewport_size({"width": 360, "height": 800})
    enter(page, case.actors["admin"]["email"])
    section(page, "Учетные записи")
    expect(page.get_by_role("table", name="Учетные записи", exact=True).locator("tbody tr")).to_have_count(10)
    page.get_by_role("button", name="Далее", exact=True).click()
    expect(page.get_by_text("Смещение: 10", exact=True)).to_be_visible()
    page.get_by_role("button", name="Назад", exact=True).click()
    expect(page.get_by_text("Смещение: 0", exact=True)).to_be_visible()
    query = page.get_by_label("Поиск по email, имени или фамилии", exact=True)
    query.fill(uuid4().hex)
    query.press("Enter")
    expect(page.get_by_text("Всего: 0. Страница: 1. Размер: 10.", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="Далее", exact=True)).to_be_disabled()
    expect(page.get_by_role("button", name="Назад", exact=True)).to_be_disabled()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    query.fill(case.actors["student"]["email"])
    query.press("Enter")
    expect(page.get_by_role("button", name=f"Открыть учетную запись {case.actors['student']['id']}", exact=True)).to_be_visible()


@pytest.mark.parametrize("status", [403, 404])
def test_effective_and_detail_real_denials(browser_page, scenario, status):
    page, case = browser_page, scenario
    enter(page, case.actors["admin"]["email"])
    user_id = case.actors["student"]["id"]
    search(page, case.actors["student"]["email"])
    if status == 403:
        case.request("admin2", "PUT", f"/admin/users/{case.actors['admin']['id']}/roles", 200,
                     json={"role_codes": ["department"]})
        button = page.get_by_role("button", name=f"Эффективные права {user_id}", exact=True)
    else:
        # Alter only the navigation target, not the backend's response or authorization.
        page.evaluate("""() => {
          const original = window.fetch;
          window.fetch = (...args) => {
            if (/^\\/admin\\/users\\/\\d+$/.test(String(args[0]))) args[0] = '/admin/users/9223372036854775807';
            return original(...args);
          };
        }""")
        button = page.get_by_role("button", name=f"Открыть учетную запись {user_id}", exact=True)
    button.click()
    expect(page.get_by_role("alert")).to_contain_text("Недостаточно прав" if status == 403 else "Ресурс не найден")
    expect(page.get_by_role("table", name="Эффективные права учетной записи", exact=True)).to_have_count(0)


def test_detail_response_logout_and_refresh_no_storage(browser_page, scenario):
    page, case = browser_page, scenario
    enter(page, case.actors["admin"]["email"])
    user_id = case.actors["student"]["id"]
    search(page, case.actors["student"]["email"])
    page.evaluate("""() => {
      const original = window.fetch;
      window.fetch = async (...args) => {
        const response = await original(...args);
        if (/^\\/admin\\/users\\/\\d+$/.test(String(args[0]))) {
          document.body.dataset.detailReceived = 'true';
          await new Promise(resolve => { window.releaseDetail = resolve; });
        }
        return response;
      };
    }""")
    page.get_by_role("button", name=f"Открыть учетную запись {user_id}", exact=True).click()
    expect(page.locator("body")).to_have_attribute("data-detail-received", "true")
    page.get_by_role("button", name="Выйти", exact=True).click()
    enter(page, case.actors["outsider"]["email"])
    page.evaluate("window.releaseDetail()")
    expect(page.get_by_role("heading", name="Проекты", exact=True)).to_be_visible()
    expect(page.get_by_role("region", name=f"Учетная запись {user_id}", exact=True)).to_have_count(0)
    assert page.evaluate("localStorage.length === 0 && sessionStorage.length === 0")


def test_detail_mutation_uncertain_double_click_and_cancel(browser_page, scenario):
    page, case = browser_page, scenario
    user_id = case.actors["student"]["id"]
    enter(page, case.actors["admin"]["email"])
    search(page, case.actors["student"]["email"])
    page.get_by_role("button", name=f"Открыть учетную запись {user_id}", exact=True).click()
    page.get_by_role("button", name=f"Изменить данные {user_id}", exact=True).click()
    page.get_by_label("Имя", exact=True).fill("Changed catalog")
    prepare(page)
    page.get_by_role("button", name="Отмена", exact=True).click()
    assert case.request("admin", "GET", f"/admin/users/{user_id}", 200)["first_name"] == "student"
    prepare(page)
    page.evaluate("""() => {
      const original = window.fetch;
      window.fetch = async (...args) => {
        const response = await original(...args);
        if (args[1]?.method === 'PATCH') throw new TypeError('controlled lost response');
        return response;
      };
    }""")
    requests = []
    page.on("request", lambda request: requests.append(request.url) if request.method == "PATCH" else None)
    page.get_by_role("button", name="Подтвердить отправку", exact=True).evaluate("(button) => {button.click();button.click();}")
    expect(page.get_by_role("alert")).to_contain_text("результат неизвестен")
    page.get_by_role("button", name="Проверить актуальные данные", exact=True).click()
    expect(page.get_by_role("heading", name="Учетные записи", exact=True)).to_be_visible()
    expect(page.get_by_role("region", name=f"Учетная запись {user_id}", exact=True)).to_contain_text("Changed catalog")
    assert case.request("admin", "GET", f"/admin/users/{user_id}", 200)["first_name"] == "Changed catalog"
    assert len(requests) == 1


def test_own_effective_detail_mutation_refreshes_auth_me_and_menu(browser_page, scenario, permission_matrix_guard):
    page, case = browser_page, scenario
    user_id = case.actors["admin"]["id"]
    case.request("admin2", "PUT", f"/admin/users/{user_id}/roles", 200,
                 json={"role_codes": ["admin", "department"]})
    enter(page, case.actors["admin"]["email"])
    expect(page.get_by_role("navigation").get_by_role("button", name="Отчеты", exact=True)).to_be_visible()
    search(page, case.actors["admin"]["email"])
    page.get_by_role("button", name=f"Эффективные права {user_id}", exact=True).click()
    detail = page.get_by_role("region", name=f"Учетная запись {user_id}", exact=True)
    expect(detail).to_contain_text("reports.read")
    page.get_by_role("button", name="Разрешения роли department", exact=True).click()
    page.get_by_role("button", name="Снять разрешение reports.read", exact=True).click()
    prepare(page)
    with page.expect_response(lambda response: response.url.endswith("/auth/me")) as identity:
        page.get_by_role("button", name="Подтвердить отправку", exact=True).click()
    assert "reports.read" not in identity.value.json()["permissions"]
    expect(detail).not_to_contain_text("reports.read")
    expect(page.get_by_role("navigation").get_by_role("button", name="Отчеты", exact=True)).to_have_count(0)
