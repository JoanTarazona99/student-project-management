from uuid import uuid4

from playwright.sync_api import expect
from sqlalchemy import text

from conftest import TEST_PASSWORD
from test_web_ui_admin_browser import account_page, enter, section
from test_web_ui_browser import browser_page, login


def create_account_draft(page, email):
    section(page, "Учетные записи")
    page.get_by_role("button", name="Создать учетную запись", exact=True).click()
    page.get_by_label("Email учетной записи", exact=True).fill(email)
    page.get_by_label("Имя", exact=True).fill("Unsaved")
    page.get_by_label("Фамилия", exact=True).fill("Draft")
    page.get_by_label("Начальный пароль", exact=True).fill(TEST_PASSWORD)
    page.get_by_role("group", name="Начальные роли", exact=True).locator(
        'input[name="department"]'
    ).check()


def test_clean_form_and_restored_values_do_not_prompt(browser_page, scenario):
    page, case = browser_page, scenario
    enter(page, case.actors["admin"]["email"])
    account_page(page, case, case.actors["student"]["id"])
    page.get_by_role("button", name=f"Изменить данные {case.actors['student']['id']}", exact=True).click()
    first_name = page.get_by_label("Имя", exact=True)
    original = first_name.input_value()
    first_name.fill(original + " updated")
    first_name.fill(original)
    assert not page.evaluate("""() => {
      const event = new Event('beforeunload', {cancelable: true});
      window.dispatchEvent(event);
      return event.defaultPrevented;
    }""")
    page.get_by_role("navigation").get_by_role("button", name="Уведомления", exact=True).click()
    expect(page.get_by_role("alertdialog")).to_have_count(0)
    expect(page.get_by_role("heading", name="Уведомления", exact=True)).to_be_visible()


def test_dirty_account_draft_can_stay_or_be_discarded_without_exposure(browser_page, scenario):
    page, case = browser_page, scenario
    page.set_viewport_size({"width": 360, "height": 800})
    enter(page, case.actors["admin"]["email"])
    email = f"unsaved-{uuid4().hex}@integration.invalid"
    create_account_draft(page, email)
    assert page.evaluate("""() => {
      const event = new Event('beforeunload', {cancelable: true});
      window.dispatchEvent(event);
      return event.defaultPrevented;
    }""")
    page.get_by_role("navigation").get_by_role("button", name="Уведомления", exact=True).click()
    dialog = page.get_by_role("alertdialog", name="Покинуть форму?")
    expect(dialog).to_be_visible()
    assert page.get_by_role("alertdialog").get_attribute("aria-modal") == "true"
    title_id = page.get_by_role("alertdialog").get_attribute("aria-labelledby")
    description_id = page.get_by_role("alertdialog").get_attribute("aria-describedby")
    assert page.locator(f"#{title_id}").count() == 1
    assert page.locator(f"#{description_id}").count() == 1
    assert page.evaluate("""() => {
      const ids = [...document.querySelectorAll('[id]')].map(element => element.id);
      return ids.length === new Set(ids).size;
    }""")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    expect(dialog).not_to_contain_text(TEST_PASSWORD)
    expect(dialog).not_to_contain_text(email)
    expect(page.get_by_role("button", name="Остаться", exact=True)).to_be_focused()
    page.keyboard.press("Shift+Tab")
    expect(page.get_by_role("button", name="Покинуть форму без сохранения", exact=True)).to_be_focused()
    page.keyboard.press("Tab")
    expect(page.get_by_role("button", name="Остаться", exact=True)).to_be_focused()
    page.keyboard.press("Escape")
    expect(page.get_by_role("alertdialog")).to_have_count(0)
    expect(page.get_by_label("Email учетной записи", exact=True)).to_have_value(email)
    expect(page.get_by_role("navigation").get_by_role("button", name="Уведомления", exact=True)).to_be_focused()
    page.get_by_role("navigation").get_by_role("button", name="Уведомления", exact=True).click()
    page.get_by_role("button", name="Покинуть форму без сохранения", exact=True).click()
    expect(page.get_by_role("heading", name="Уведомления", exact=True)).to_be_visible()
    expect(page.get_by_text(email, exact=True)).to_have_count(0)
    assert page.evaluate("localStorage.length === 0 && sessionStorage.length === 0")
    with case.engine.connect() as connection:
        assert connection.execute(
            text("SELECT count(*) FROM public.users WHERE email = :email"), {"email": email}
        ).scalar_one() == 0


def test_prepared_confirmation_requires_explicit_discard_and_is_not_sent(browser_page, scenario):
    page, case = browser_page, scenario
    login(page, case, "teacher")
    section(page, "Темы")
    page.get_by_role("button", name="Создать тему", exact=True).click()
    title = f"Unsaved topic {uuid4().hex}"
    page.get_by_label("Название темы", exact=True).fill(title)
    page.get_by_role("button", name="Проверить и подтвердить", exact=True).click()
    expect(page.get_by_role("region", name="Подтверждение", exact=True)).to_be_visible()
    project_reads = []
    page.on("request", lambda request: project_reads.append(request.url) if
            request.url.endswith("/projects?limit=10&offset=0") and request.method == "GET" else None)
    sent = []
    page.on("request", lambda request: sent.append(request.url) if
            request.url.endswith("/topics") and request.method == "POST" else None)
    page.get_by_role("navigation").get_by_role("button", name="Проекты", exact=True).click()
    dialog = page.get_by_role("alertdialog")
    expect(dialog).to_contain_text("еще не отправлено")
    assert project_reads == []
    page.get_by_role("navigation").get_by_role("button", name="Темы", exact=True).evaluate("(button) => button.click()")
    assert project_reads == []
    page.get_by_role("button", name="Остаться", exact=True).click()
    expect(page.get_by_role("region", name="Подтверждение", exact=True)).to_be_visible()
    page.get_by_role("button", name="Отмена", exact=True).click()
    page.get_by_role("navigation").get_by_role("button", name="Проекты", exact=True).click()
    page.get_by_role("button", name="Покинуть форму без сохранения", exact=True).click()
    expect(page.get_by_role("heading", name="Проекты", exact=True)).to_be_visible()
    assert len(project_reads) == 1
    assert sent == []


def test_dynamic_plan_rows_return_to_clean_state_when_removed(browser_page, scenario):
    page, case = browser_page, scenario
    login(page, case)
    page.get_by_role("button", name=f"Открыть проект {case.project_id}", exact=True).click()
    page.get_by_role("button", name="Предложить план", exact=True).click()
    expect(page.get_by_role("heading", name="Предложение плана", exact=True)).to_be_visible()
    page.get_by_role("button", name="Добавить этап", exact=True).click()
    page.get_by_role("navigation").get_by_role("button", name="Проекты", exact=True).click()
    expect(page.get_by_role("alertdialog")).to_be_visible()
    page.get_by_role("button", name="Остаться", exact=True).click()
    page.get_by_role("button", name="Удалить этап", exact=True).last.click()
    page.get_by_role("navigation").get_by_role("button", name="Проекты", exact=True).click()
    expect(page.get_by_role("alertdialog")).to_have_count(0)
    expect(page.get_by_role("heading", name="Проекты", exact=True)).to_be_visible()


def test_material_filename_is_not_in_discard_warning(browser_page, scenario):
    page, case = browser_page, scenario
    case.approve()
    login(page, case)
    page.get_by_role("button", name=f"Открыть проект {case.project_id}", exact=True).click()
    page.get_by_role("button", name="Материалы этапа 1", exact=True).click()
    page.get_by_role("button", name="Загрузить материал", exact=True).click()
    filename = "private-integration-material.txt"
    page.get_by_label("Файл материала", exact=True).set_input_files({
        "name": filename, "mimeType": "text/plain", "buffer": b"test-only content"
    })
    page.get_by_role("navigation").get_by_role("button", name="Темы", exact=True).click()
    dialog = page.get_by_role("alertdialog")
    expect(dialog).to_be_visible()
    expect(dialog).not_to_contain_text(filename)
    page.get_by_role("button", name="Покинуть форму без сохранения", exact=True).click()
    expect(page.get_by_role("heading", name="Темы", exact=True)).to_be_visible()
    assert not case.storage.exists() or list(case.storage.iterdir()) == []


def test_leaving_during_submission_does_not_claim_rollback_or_repeat(browser_page, scenario):
    page, case = browser_page, scenario
    login(page, case, "teacher")
    section(page, "Темы")
    page.get_by_role("button", name="Создать тему", exact=True).click()
    title = f"In-flight topic {uuid4().hex}"
    page.get_by_label("Название темы", exact=True).fill(title)
    page.get_by_role("button", name="Проверить и подтвердить", exact=True).click()
    page.evaluate("""() => {
      const original = window.fetch;
      window.fetch = async (...args) => {
        const response = await original(...args);
        if (String(args[0]) === '/topics' && args[1]?.method === 'POST') {
          document.body.dataset.topicReceived = 'true';
          await new Promise(resolve => { window.releaseTopic = resolve; });
        }
        return response;
      };
    }""")
    sent = []
    page.on("request", lambda request: sent.append(request.url) if
            request.url.endswith("/topics") and request.method == "POST" else None)
    page.get_by_role("button", name="Подтвердить отправку", exact=True).click()
    expect(page.locator("body")).to_have_attribute("data-topic-received", "true")
    page.get_by_role("navigation").get_by_role("button", name="Проекты", exact=True).click()
    dialog = page.get_by_role("alertdialog")
    expect(dialog).to_contain_text("не отменяет и не откатывает")
    page.get_by_role("button", name="Покинуть форму без сохранения", exact=True).click()
    expect(page.get_by_role("heading", name="Проекты", exact=True)).to_be_visible()
    page.evaluate("window.releaseTopic()")
    with case.engine.connect() as connection:
        assert connection.execute(
            text("SELECT count(*) FROM public.topics WHERE title = :title"), {"title": title}
        ).scalar_one() == 1
    assert len(sent) == 1
    expect(page.get_by_role("region", name="Подтверждение", exact=True)).to_have_count(0)


def test_uncertain_submission_requires_read_before_new_confirmation(browser_page, scenario):
    page, case = browser_page, scenario
    enter(page, case.actors["admin"]["email"])
    email = f"uncertain-{uuid4().hex}@integration.invalid"
    create_account_draft(page, email)
    page.get_by_role("button", name="Проверить и подтвердить", exact=True).click()
    page.evaluate("""() => {
      const original = window.fetch;
      window.fetch = async (...args) => {
        const response = await original(...args);
        if (String(args[0]) === '/admin/users' && args[1]?.method === 'POST') {
          throw new TypeError('controlled response loss');
        }
        return response;
      };
    }""")
    sent = []
    page.on("request", lambda request: sent.append(request.url) if
            request.url.endswith("/admin/users") and request.method == "POST" else None)
    page.get_by_role("button", name="Подтвердить отправку", exact=True).click()
    expect(page.get_by_role("alert")).to_contain_text("результат неизвестен")
    expect(page.get_by_role("button", name="Подтвердить отправку", exact=True)).to_have_count(0)
    expect(page.get_by_role("button", name="Проверить актуальные данные", exact=True)).to_be_visible()
    expect(page.get_by_label("Начальный пароль", exact=True)).to_have_value("")
    page.get_by_role("button", name="Проверить актуальные данные", exact=True).click()
    expect(page.get_by_role("heading", name="Учетные записи", exact=True)).to_be_visible()
    with case.engine.connect() as connection:
        assert connection.execute(
            text("SELECT count(*) FROM public.users WHERE email = :email"), {"email": email}
        ).scalar_one() == 1
    assert len(sent) == 1


def test_logout_and_401_clear_a_dirty_form_without_a_discard_prompt(browser_page, scenario):
    page, case = browser_page, scenario
    enter(page, case.actors["admin"]["email"])
    email = f"logout-draft-{uuid4().hex}@integration.invalid"
    create_account_draft(page, email)
    page.get_by_role("navigation").get_by_role("button", name="Выйти", exact=True).click()
    expect(page.get_by_role("alertdialog")).to_have_count(0)
    expect(page.get_by_role("heading", name="Вход", exact=True)).to_be_visible()
    expect(page.get_by_text(email, exact=True)).to_have_count(0)
    assert not page.evaluate("""() => {
      const event = new Event('beforeunload', {cancelable: true});
      window.dispatchEvent(event);
      return event.defaultPrevented;
    }""")
    login(page, case)
    expect(page.get_by_text(email, exact=True)).to_have_count(0)
    assert page.evaluate("localStorage.length === 0 && sessionStorage.length === 0")

    page.get_by_role("navigation").get_by_role("button", name="Выйти", exact=True).click()
    enter(page, case.actors["admin"]["email"])
    create_account_draft(page, f"401-draft-{uuid4().hex}@integration.invalid")
    case.request("admin2", "PATCH", f"/admin/users/{case.actors['admin']['id']}/status", 200,
                 json={"is_active": False})
    page.get_by_role("navigation").get_by_role("button", name="Уведомления", exact=True).click()
    page.get_by_role("button", name="Покинуть форму без сохранения", exact=True).click()
    expect(page.get_by_role("heading", name="Вход", exact=True)).to_be_visible()
    expect(page.get_by_role("alertdialog")).to_have_count(0)
    expect(page.get_by_label("Email учетной записи", exact=True)).to_have_count(0)
    assert page.evaluate("localStorage.length === 0 && sessionStorage.length === 0")
    assert not page.evaluate("""() => {
      const event = new Event('beforeunload', {cancelable: true});
      window.dispatchEvent(event);
      return event.defaultPrevented;
    }""")


def test_project_registration_draft_requires_discard(browser_page, scenario):
    page, case = browser_page, scenario
    login(page, case, "teacher")
    section(page, "Проекты")
    page.get_by_role("button", name="Зарегистрировать проект", exact=True).click()
    expect(page.get_by_role("heading", name="Регистрация проекта", exact=True)).to_be_visible()
    page.get_by_label("Утвержденная тема", exact=True).select_option(index=1)
    page.get_by_label("Студент ID", exact=True).fill(str(case.actors["student"]["id"]))
    sent = []
    page.on("request", lambda request: sent.append(request.url) if
            request.url.endswith("/projects") and request.method == "POST" else None)
    page.get_by_role("navigation").get_by_role("button", name="Темы", exact=True).click()
    expect(page.get_by_role("alertdialog")).to_be_visible()
    page.get_by_role("button", name="Покинуть форму без сохранения", exact=True).click()
    expect(page.get_by_role("heading", name="Темы", exact=True)).to_be_visible()
    assert sent == []


def test_supervisor_assignment_draft_requires_discard(browser_page, scenario):
    page, case = browser_page, scenario
    login(page, case, "teacher")
    page.get_by_role("button", name=f"Открыть проект {case.project_id}", exact=True).click()
    page.get_by_role("button", name="Назначить руководителя", exact=True).click()
    page.get_by_label("Руководитель ID", exact=True).fill(str(case.actors["unassigned"]["id"]))
    sent = []
    page.on("request", lambda request: sent.append(request.url) if
            request.url.endswith(f"/projects/{case.project_id}/supervisor") and request.method == "PUT" else None)
    page.get_by_role("navigation").get_by_role("button", name="Темы", exact=True).click()
    expect(page.get_by_role("alertdialog")).to_be_visible()
    page.get_by_role("button", name="Покинуть форму без сохранения", exact=True).click()
    expect(page.get_by_role("heading", name="Темы", exact=True)).to_be_visible()
    assert sent == []


def test_review_draft_requires_discard_without_creating_review(browser_page, scenario):
    page, case = browser_page, scenario
    case.approve()
    material = case.upload()
    with case.engine.connect() as connection:
        before_reviews = connection.execute(
            text("SELECT count(*) FROM public.reviews WHERE material_version_id = :id"),
            {"id": material["id"]},
        ).scalar_one()
        before_stage = connection.execute(
            text("SELECT status FROM public.stages WHERE id = :id"),
            {"id": case.stages[0]["id"]},
        ).scalar_one()
    login(page, case, "supervisor")
    page.get_by_role("button", name=f"Открыть проект {case.project_id}", exact=True).click()
    page.get_by_role("button", name="Материалы этапа 1", exact=True).click()
    page.get_by_role("button", name="Проверить последнюю версию", exact=True).click()
    page.get_by_label("Замечания", exact=True).fill("Integration-only review draft.")
    sent = []
    page.on("request", lambda request: sent.append(request.url) if
            request.url.endswith(f"/materials/{material['id']}/review") and request.method == "POST" else None)
    page.get_by_role("navigation").get_by_role("button", name="Проекты", exact=True).click()
    expect(page.get_by_role("alertdialog")).to_be_visible()
    page.get_by_role("button", name="Покинуть форму без сохранения", exact=True).click()
    expect(page.get_by_role("heading", name="Проекты", exact=True)).to_be_visible()
    assert sent == []
    with case.engine.connect() as connection:
        assert connection.execute(
            text("SELECT count(*) FROM public.reviews WHERE material_version_id = :id"),
            {"id": material["id"]},
        ).scalar_one() == before_reviews
        assert connection.execute(
            text("SELECT status FROM public.stages WHERE id = :id"),
            {"id": case.stages[0]["id"]},
        ).scalar_one() == before_stage


def test_role_and_permission_confirmations_warn_before_leaving(browser_page, scenario):
    page, case = browser_page, scenario
    enter(page, case.actors["admin"]["email"])
    section(page, "Роли")
    page.get_by_label("Учетная запись ID", exact=True).fill(str(case.actors["student"]["id"]))
    page.get_by_role("button", name="Открыть роли учетной записи", exact=True).click()
    page.get_by_role("button", name="Назначить роль supervisor", exact=True).click()
    page.get_by_role("button", name="Проверить и подтвердить", exact=True).click()
    role_puts = []
    page.on("request", lambda request: role_puts.append(request.url) if
            request.method == "PUT" and request.url.endswith(f"/admin/users/{case.actors['student']['id']}/roles") else None)
    page.get_by_role("navigation").get_by_role("button", name="Уведомления", exact=True).click()
    expect(page.get_by_role("alertdialog")).to_contain_text("еще не отправлено")
    page.get_by_role("button", name="Покинуть форму без сохранения", exact=True).click()
    expect(page.get_by_role("heading", name="Уведомления", exact=True)).to_be_visible()
    assert role_puts == []

    current = case.request("admin", "GET", "/admin/roles/department/permissions", 200)
    permissions = case.request("admin", "GET", "/admin/permissions", 200)
    available = next(item["code"] for item in permissions if item["code"] not in current["permission_codes"])
    section(page, "Роли")
    page.get_by_role("button", name="Разрешения роли department", exact=True).click()
    page.get_by_role("button", name=f"Назначить разрешение {available}", exact=True).click()
    page.get_by_role("button", name="Проверить и подтвердить", exact=True).click()
    permission_puts = []
    page.on("request", lambda request: permission_puts.append(request.url) if
            request.method == "PUT" and request.url.endswith("/admin/roles/department/permissions") else None)
    page.get_by_role("navigation").get_by_role("button", name="Уведомления", exact=True).click()
    expect(page.get_by_role("alertdialog")).to_contain_text("еще не отправлено")
    page.get_by_role("button", name="Покинуть форму без сохранения", exact=True).click()
    expect(page.get_by_role("heading", name="Уведомления", exact=True)).to_be_visible()
    assert permission_puts == []
