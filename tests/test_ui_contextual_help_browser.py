from uuid import uuid4

from playwright.sync_api import expect

from test_web_ui_academic_browser import detail as open_project_detail
from test_web_ui_admin_browser import enter, section
from test_web_ui_browser import browser_page


def assert_unique_ids(page):
    duplicates = page.evaluate("""() => {
      const ids = [...document.querySelectorAll('[id]')].map(element => element.id);
      return ids.filter((id, index) => ids.indexOf(id) !== index);
    }""")
    assert duplicates == []


def assert_described(page, control):
    described_by = control.get_attribute("aria-describedby")
    assert described_by
    for element_id in described_by.split():
        expect(page.locator(f'[id="{element_id}"]')).to_have_count(1)


def assert_help_is_text(page):
    assert page.locator(".help").evaluate_all(
        "elements => elements.every(element => element.innerHTML === element.textContent && !element.children.length)"
    )


def test_catalog_help_search_empty_result_and_focus(browser_page, scenario):
    page, case = browser_page, scenario
    marker = uuid4().hex
    case.request("admin", "POST", "/admin/users", 201, json={
        "email": f"{marker}@integration.invalid",
        "password": "integration-only-not-a-real-password",
        "first_name": f"Help{marker}",
        "last_name": "Context",
        "role_codes": ["department"],
    })
    page.set_viewport_size({"width": 360, "height": 800})
    enter(page, case.actors["admin"]["email"])
    section(page, "Учетные записи")
    query = page.get_by_label("Поиск по email, имени или фамилии", exact=True)
    assert_described(page, query)
    help_text = page.locator(f'[id="{query.get_attribute("aria-describedby")}"]').inner_text()
    assert "email, имени и фамилии" in help_text
    assert "кнопкой или Enter" in help_text
    assert "страницу заново" in help_text
    assert "а не ошибку" in help_text
    assert_help_is_text(page)

    query.fill(f"missing-{marker}")
    query.press("Enter")
    expect(page.get_by_text("По запросу учетные записи не найдены. Это не ошибка.", exact=True)).to_be_visible()
    expect(query).to_be_focused()
    assert_unique_ids(page)

    query = page.get_by_label("Поиск по email, имени или фамилии", exact=True)
    query.fill(marker)
    query.press("Enter")
    expect(page.get_by_text("Всего: 1. Страница: 1. Размер: 10.", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="Открыть учетную запись", exact=False)).to_be_visible()
    expect(query).to_be_focused()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


def test_plan_help_links_utc_proposal_approval_and_conflict(browser_page, scenario):
    page, case = browser_page, scenario
    enter(page, case.actors["student"]["email"])
    open_project_detail(page, case.project_id)
    page.get_by_role("button", name="Предложить план", exact=True).click()
    expect(page.get_by_text("отправка не утверждает его.", exact=False)).to_be_visible()
    starts = page.get_by_label("Начало (UTC)", exact=True).first
    assert_described(page, starts)
    help_text = page.locator(f'[id="{starts.get_attribute("aria-describedby")}"]').inner_text()
    assert "вводятся в UTC" in help_text
    assert "не утверждает" in help_text
    page.get_by_role("button", name="Добавить этап", exact=True).click()
    added_start = page.get_by_label("Начало (UTC)", exact=True).nth(1)
    assert_described(page, added_start)
    assert page.locator(f'[id="{added_start.get_attribute("aria-describedby")}"]').inner_text() == help_text
    assert_unique_ids(page)
    assert_help_is_text(page)

    page.get_by_role("button", name="Выйти", exact=True).click()
    enter(page, case.actors["supervisor"]["email"])
    open_project_detail(page, case.project_id)
    page.get_by_role("button", name="Утвердить план", exact=True).click()
    expect(page.get_by_text("Предложенный план еще не утвержден.", exact=False)).to_be_visible()


def test_completed_plan_date_help_explains_manual_expected_conflict_review(browser_page, scenario):
    page, case = browser_page, scenario
    case.approve()
    material = case.upload()
    case.review(material, decision="accepted")
    enter(page, case.actors["supervisor"]["email"])
    open_project_detail(page, case.project_id)
    page.get_by_role("button", name="Изменить даты", exact=True).click()
    completed_start = page.get_by_label("Начало (UTC)", exact=True).first
    expect(completed_start).not_to_be_editable()
    assert_described(page, completed_start)
    help_text = page.locator(f'[id="{completed_start.get_attribute("aria-describedby")}"]').inner_text()
    assert "completed" in help_text
    assert "конфликте expected" in help_text
    assert "повторной отправки автоматически нет" in help_text
    assert_unique_ids(page)
    assert_help_is_text(page)


def test_material_and_review_help_is_linked_to_controls(browser_page, scenario):
    page, case = browser_page, scenario
    case.approve()
    enter(page, case.actors["student"]["email"])
    open_project_detail(page, case.project_id)
    page.get_by_role("button", name="Материалы этапа 1", exact=True).click()
    page.get_by_role("button", name="Загрузить материал", exact=True).click()
    file_input = page.get_by_label("Файл материала", exact=True)
    assert_described(page, file_input)
    upload_help = page.locator(f'[id="{file_input.get_attribute("aria-describedby")}"]').inner_text()
    assert "TXT в UTF-8, PDF и DOCX" in upload_help
    assert "20 МиБ" in upload_help
    assert "создает новую версию" in upload_help
    assert "проверьте историю версий" in upload_help
    assert_unique_ids(page)
    assert_help_is_text(page)

    case.upload()
    page.get_by_role("button", name="Выйти", exact=True).click()
    enter(page, case.actors["supervisor"]["email"])
    open_project_detail(page, case.project_id)
    page.get_by_role("button", name="Материалы этапа 1", exact=True).click()
    page.get_by_role("button", name="Проверить последнюю версию", exact=True).click()
    decision = page.get_by_label("Решение", exact=True)
    remarks = page.get_by_label("Замечания", exact=True)
    assert_described(page, decision)
    assert_described(page, remarks)
    review_help = page.locator(f'[id="{remarks.get_attribute("aria-describedby")}"]').inner_text()
    assert "revision" in review_help
    assert "непустые замечания" in review_help
    assert "проверьте историю" in review_help
    assert_unique_ids(page)


def test_admin_role_permissions_help_and_footer_are_accurate(browser_page, scenario):
    page, case = browser_page, scenario
    enter(page, case.actors["admin"]["email"])
    section(page, "Роли")
    expect(page.get_by_role("heading", name="Роли", exact=True)).to_be_focused()
    page.get_by_label("Учетная запись ID", exact=True).fill(str(case.actors["student"]["id"]))
    page.get_by_role("button", name="Открыть роли учетной записи", exact=True).click()
    expect(page.get_by_role("heading", name=f"Роли учетной записи № {case.actors['student']['id']}", exact=True)).to_be_visible()
    expect(page.get_by_role("heading", name=f"Роли учетной записи № {case.actors['student']['id']}", exact=True)).to_be_focused()
    role_help = page.locator(".help").filter(has_text="PUT заменяет полный набор")
    expect(role_help).to_be_visible()
    role_distinction = page.get_by_text("Роль назначает категорию доступа", exact=False)
    expect(role_distinction).to_be_visible()
    role_action = page.locator("button[aria-describedby]").first
    assert_described(page, role_action)
    assert "эффективные разрешения" in role_distinction.inner_text()
    assert_unique_ids(page)

    section(page, "Роли")
    page.get_by_role("button", name="Разрешения роли student", exact=True).click()
    permission_help = page.locator(".help").filter(has_text="Изменение разрешений влияет на всех пользователей роли")
    expect(permission_help).to_be_visible()
    permission_action = page.locator("button[aria-describedby]").first
    assert_described(page, permission_action)
    assert "полный набор" in permission_help.inner_text()
    assert "Другие сессии автоматически не обновляются" in permission_help.inner_text()
    footer = page.locator("footer")
    expect(footer).to_contain_text("может открыть эффективные права другой учетной записи")
    expect(footer).not_to_contain_text("эффективные права чужой сессии не предоставляются")
    assert_unique_ids(page)


def test_account_form_focus_validation_and_confirmation_cancel(browser_page, scenario):
    page, case = browser_page, scenario
    marker = uuid4().hex
    created = case.request("admin", "POST", "/admin/users", 201, json={
        "email": f"{marker}@integration.invalid",
        "password": "integration-only-not-a-real-password",
        "first_name": "Focus",
        "last_name": "Context",
        "role_codes": ["department"],
    })
    enter(page, case.actors["admin"]["email"])
    section(page, "Учетные записи")
    query = page.get_by_label("Поиск по email, имени или фамилии", exact=True)
    query.fill(marker)
    query.press("Enter")
    page.get_by_role("button", name=f"Изменить данные {created['id']}", exact=True).click()
    email = page.get_by_label("Email учетной записи", exact=True)
    expect(email).to_be_focused()

    page.get_by_role("button", name="Проверить и подтвердить", exact=True).click()
    alert = page.get_by_role("alert")
    expect(alert).to_have_text("Нет измененных полей для отправки.")
    expect(alert).to_be_focused()

    page.get_by_label("Имя", exact=True).fill("Changed only in browser")
    page.get_by_role("button", name="Проверить и подтвердить", exact=True).click()
    expect(page.get_by_role("button", name="Подтвердить отправку", exact=True)).to_be_focused()
    page.get_by_role("button", name="Подтвердить отправку", exact=True).press("Tab")
    cancel = page.get_by_role("button", name="Отмена", exact=True)
    expect(cancel).to_be_focused()
    cancel.press("Enter")
    expect(page.get_by_role("button", name="Проверить и подтвердить", exact=True)).to_be_focused()
    assert case.request("admin", "GET", f"/admin/users/{created['id']}", 200)["first_name"] == "Focus"
    page.get_by_role("button", name="Выйти", exact=True).click()
    expect(page.locator(".help")).to_have_count(0)
    expect(page.get_by_text(marker, exact=False)).to_have_count(0)
