import pytest
from playwright.sync_api import expect
from sqlalchemy import text

from test_web_ui_browser import browser_page, login


def confirm(page):
    page.get_by_role("button", name="Проверить и подтвердить", exact=True).click()
    expect(page.get_by_role("region", name="Подтверждение", exact=True)).to_be_visible()
    page.get_by_role("button", name="Подтвердить отправку", exact=True).click()
    expect(page.get_by_text("Операция подтверждена сервером.", exact=True)).to_be_visible()


def switch(page, case, actor):
    page.get_by_role("button", name="Выйти", exact=True).click()
    login(page, case, actor)


def detail(page, project_id):
    # Project lists are scoped and page-limited, not an unlimited catalog.
    page.get_by_role("navigation").get_by_role("button", name="Проекты", exact=True).click()
    expect(page.get_by_role("table", name="Проекты", exact=True)).to_be_visible()
    target = page.get_by_role("button", name=f"Открыть проект {project_id}", exact=True)
    for index in range(200):
        if target.count():
            break
        page.get_by_role("button", name="Далее", exact=True).click()
        expect(page.get_by_text(f"Смещение: {(index + 1) * 10}", exact=True)).to_be_visible()
    target.click()
    expect(page.get_by_role("heading", name=f"Проект № {project_id}", exact=True)).to_be_visible()


def choose_topic(page, topic_id):
    selector = page.get_by_label("Утвержденная тема", exact=True)
    expect(selector).to_be_visible()
    for index in range(200):
        if selector.locator(f'option[value="{topic_id}"]').count():
            selector.select_option(str(topic_id))
            return
        with page.expect_response(lambda response: "/topics?" in response.url):
            page.get_by_role("button", name="Далее", exact=True).click()
        expect(page.get_by_text(f"Смещение: {(index + 1) * 10}", exact=True)).to_be_visible()
    raise AssertionError("Topic was not available in bounded pages")


def fill_plan(page, count=3):
    for _ in range(count - 1):
        page.get_by_role("button", name="Добавить этап", exact=True).click()
    for index in range(count):
        row = page.get_by_role("group", name=f"Этап {index + 1}", exact=True)
        row.get_by_label("Название этапа", exact=True).fill(f"UI stage {index + 1}")
        row.get_by_label("Начало (UTC)", exact=True).fill(f"2099-01-0{index + 1}T00:00")
        row.get_by_label("Срок (UTC)", exact=True).fill(f"2099-01-0{index + 2}T00:00")


def upload(page, data, expected_version):
    page.get_by_role("button", name="Загрузить материал", exact=True).click()
    page.get_by_label("Файл материала", exact=True).set_input_files({
        "name": "ui-material.txt", "mimeType": "text/plain", "buffer": data,
    })
    page.get_by_role("button", name="Проверить и подтвердить", exact=True).click()
    with page.expect_request(lambda request: "/materials" in request.url and request.method == "POST") as sent:
        page.get_by_role("button", name="Подтвердить отправку", exact=True).click()
    assert sent.value.headers["content-type"].startswith("multipart/form-data; boundary=")
    expect(page.get_by_text(f"Версия {expected_version} подтверждена.", exact=False)).to_be_visible()


def review(page, accepted=False):
    page.get_by_role("button", name="Проверить последнюю версию", exact=True).click()
    page.get_by_label("Решение", exact=True).select_option("accepted" if accepted else "revision")
    if not accepted:
        page.get_by_role("button", name="Проверить и подтвердить", exact=True).click()
        expect(page.get_by_role("alert")).to_have_text("Для доработки обязательны замечания.")
        page.get_by_label("Замечания", exact=True).fill("Уточните описание.")
    page.get_by_role("button", name="Проверить и подтвердить", exact=True).click()
    page.get_by_role("button", name="Подтвердить отправку", exact=True).click()
    expect(page.get_by_text("Проверка подтверждена.", exact=False)).to_be_visible()


def test_complete_academic_journey_via_ui(browser_page, scenario):
    page, case = browser_page, scenario
    login(page, case, "teacher")
    page.get_by_role("button", name="Темы", exact=True).click()
    page.get_by_role("button", name="Создать тему", exact=True).click()
    title = "<script>window.injected=true</script> Academic UI"
    page.get_by_label("Название темы", exact=True).fill(title)
    page.get_by_label("Описание темы", exact=True).fill("Описание учебного проекта.")
    page.get_by_role("button", name="Проверить и подтвердить", exact=True).click()
    with page.expect_response(lambda response: response.url.endswith("/topics") and response.request.method == "POST") as created:
        page.get_by_role("button", name="Подтвердить отправку", exact=True).click()
    topic_id = created.value.json()["id"]
    page.get_by_role("button", name=f"Утвердить созданную тему {topic_id}", exact=True).click()
    assert not page.evaluate("Boolean(window.injected)")
    confirm(page)
    switch(page, case, "student")
    page.get_by_role("button", name="Зарегистрировать проект", exact=True).click()
    choose_topic(page, topic_id)
    page.get_by_label("Преподаватель ID", exact=True).fill(str(case.actors["teacher"]["id"]))
    page.get_by_role("button", name="Проверить и подтвердить", exact=True).click()
    with page.expect_response(lambda response: response.url.endswith("/projects") and response.request.method == "POST") as created:
        page.get_by_role("button", name="Подтвердить отправку", exact=True).click()
    project_id = created.value.json()["id"]
    expect(page.get_by_role("heading", name=f"Проект № {project_id}", exact=True)).to_be_visible()
    switch(page, case, "teacher")
    detail(page, project_id)
    page.get_by_role("button", name="Назначить руководителя", exact=True).click()
    page.get_by_label("Руководитель ID", exact=True).fill(str(case.actors["supervisor"]["id"]))
    confirm(page)
    switch(page, case, "student")
    detail(page, project_id)
    page.get_by_role("button", name="Предложить план", exact=True).click()
    fill_plan(page)
    confirm(page)
    switch(page, case, "supervisor")
    detail(page, project_id)
    page.get_by_role("button", name="Утвердить план", exact=True).click()
    confirm(page)
    for index in range(3):
        switch(page, case, "student")
        detail(page, project_id)
        page.get_by_role("button", name=f"Материалы этапа {index + 1}", exact=True).click()
        upload(page, b"Initial UI material", 1)
        switch(page, case, "supervisor")
        detail(page, project_id)
        page.get_by_role("button", name=f"Материалы этапа {index + 1}", exact=True).click()
        if index == 0:
            review(page)
            switch(page, case, "student")
            detail(page, project_id)
            page.get_by_role("button", name="Материалы этапа 1", exact=True).click()
            upload(page, b"Corrected UI material", 2)
            expect(page.get_by_role("table", name="Версии материалов", exact=True).locator("tbody tr")).to_have_count(2)
            expect(page.get_by_role("table", name="Версии материалов", exact=True)).to_contain_text("Уточните описание.")
            switch(page, case, "supervisor")
            detail(page, project_id)
            page.get_by_role("button", name="Материалы этапа 1", exact=True).click()
        review(page, accepted=True)
        page.get_by_role("button", name="К проекту", exact=True).click()
        expect(page.locator("dl")).to_contain_text(["33.33%", "66.67%", "100%"][index])
    expect(page.locator("dl")).to_contain_text("Завершен")
    for row in page.get_by_role("table", name="Этапы", exact=True).locator("tbody tr").all():
        expect(row.locator("td").nth(5)).to_contain_text("UTC")
    assert page.evaluate("localStorage.length === 0 && sessionStorage.length === 0")


@pytest.mark.parametrize("actor", ["teacher", "teacher_department"])
def test_teacher_and_multirole_register_for_student(browser_page, scenario, actor):
    page, case = browser_page, scenario
    topic_id = case.request("student", "GET", f"/projects/{case.project_id}", 200)["topic_id"]
    login(page, case, actor)
    page.get_by_role("button", name="Зарегистрировать проект", exact=True).click()
    choose_topic(page, topic_id)
    expect(page.get_by_label("Преподаватель ID", exact=True)).to_have_value(str(case.actors[actor]["id"]))
    expect(page.get_by_label("Преподаватель ID", exact=True)).not_to_be_editable()
    page.get_by_label("Студент ID", exact=True).fill(str(case.actors["student"]["id"]))
    page.get_by_role("button", name="Проверить и подтвердить", exact=True).click()
    page.get_by_role("button", name="Подтвердить отправку", exact=True).click()
    expect(page.get_by_text("создан.", exact=False)).to_be_visible()
    expect(page.locator("dl")).to_contain_text(str(case.actors[actor]["id"]))


def test_plan_validation_cancel_and_narrow_keyboard(browser_page, scenario):
    page, case = browser_page, scenario
    page.set_viewport_size({"width": 360, "height": 800})
    login(page, case)
    detail(page, case.project_id)
    page.get_by_role("button", name="Предложить план", exact=True).click()
    first = page.get_by_role("group", name="Этап 1", exact=True)
    first.get_by_label("Срок (UTC)", exact=True).fill("2099-01-01T00:00")
    page.get_by_role("button", name="Проверить и подтвердить", exact=True).click()
    expect(page.get_by_role("alert")).to_have_text("Срок должен быть позже начала.")
    first.get_by_label("Срок (UTC)", exact=True).fill("2099-01-02T00:00")
    before = case.snapshot()
    page.get_by_role("button", name="Проверить и подтвердить", exact=True).press("Enter")
    expect(page.get_by_role("button", name="Подтвердить отправку", exact=True)).to_be_focused()
    page.get_by_role("button", name="Подтвердить отправку", exact=True).press("Tab")
    expect(page.get_by_role("button", name="Отмена", exact=True)).to_be_focused()
    page.get_by_role("button", name="Отмена", exact=True).press("Enter")
    expect(page.get_by_role("button", name="Проверить и подтвердить", exact=True)).to_be_focused()
    assert case.snapshot() == before
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.get_by_role("button", name="Выйти", exact=True).click()
    expect(page.get_by_role("group")).to_have_count(0)


def test_dates_conflict_keeps_expected_and_completed_immutable(browser_page, scenario):
    page, case = browser_page, scenario
    case.approve()
    case.review(case.upload())
    before = case.snapshot()
    login(page, case, "supervisor")
    detail(page, case.project_id)
    page.get_by_role("button", name="Изменить даты", exact=True).click()
    expect(page.get_by_label("Начало (UTC)", exact=True).nth(0)).not_to_be_editable()
    page.get_by_label("Причина изменения", exact=True).fill("Согласованный перенос срока.")
    page.get_by_label("Срок (UTC)", exact=True).nth(2).fill("2099-01-06T00:00")
    current = case.request("student", "GET", f"/projects/{case.project_id}/plan", 200)
    external = {
        "reason": "Другой согласованный перенос.",
        "stages": [{"stage_id": stage["id"], "expected_starts_at": stage["starts_at"],
                    "expected_due_at": stage["due_at"], "starts_at": stage["starts_at"],
                    "due_at": "2099-01-05T00:00:00Z" if stage["position"] == 3 else stage["due_at"]}
                   for stage in current["stages"]],
    }
    case.request("supervisor", "PUT", f"/projects/{case.project_id}/plan/dates", 200, json=external)
    snapshot = case.snapshot()
    page.get_by_role("button", name="Проверить и подтвердить", exact=True).click()
    page.get_by_role("button", name="Подтвердить отправку", exact=True).click()
    expect(page.get_by_role("alert")).to_contain_text("Черновик сохранен")
    expect(page.get_by_label("Срок (UTC)", exact=True).nth(2)).to_have_value("2099-01-06T00:00")
    assert case.snapshot() == snapshot
    page.get_by_role("button", name="Проверить актуальные данные", exact=True).click()
    page.get_by_role("button", name="Изменить даты", exact=True).click()
    page.get_by_label("Причина изменения", exact=True).fill("Пересмотренный перенос срока.")
    page.get_by_label("Срок (UTC)", exact=True).nth(2).fill("2099-01-06T00:00")
    confirm(page)
    after = case.snapshot()
    assert before[0][2:4] == after[0][2:4]
    assert before[1] == after[1]
    assert before[0][1][0] == after[0][1][0]


def test_confirmed_mutation_refresh_failure_does_not_repeat(browser_page, scenario):
    page, case = browser_page, scenario
    login(page, case, "teacher")
    page.get_by_role("button", name="Темы", exact=True).click()
    page.get_by_role("button", name="Создать тему", exact=True).click()
    page.get_by_label("Название темы", exact=True).fill("Refresh failure academic topic")
    page.get_by_role("button", name="Проверить и подтвердить", exact=True).click()
    mutations = []
    page.on("request", lambda request: mutations.append(request.url) if request.method == "POST" and request.url.endswith("/topics") else None)
    page.route("**/topics?*", lambda route: route.abort("failed"))
    page.get_by_role("button", name="Подтвердить отправку", exact=True).click()
    expect(page.get_by_role("alert")).to_contain_text("Ошибка сети")
    expect(page.get_by_role("alert")).to_contain_text("Операция уже подтверждена")
    page.unroute("**/topics?*")
    page.get_by_role("button", name="Повторить", exact=True).click()
    expect(page.get_by_role("heading", name="Темы", exact=True)).to_be_visible()
    assert len(mutations) == 1


def test_uncertain_upload_response_and_double_submit(browser_page, scenario):
    page, case = browser_page, scenario
    case.approve()
    login(page, case)
    detail(page, case.project_id)
    page.get_by_role("button", name="Материалы этапа 1", exact=True).click()
    page.get_by_role("button", name="Загрузить материал", exact=True).click()
    page.get_by_label("Файл материала", exact=True).set_input_files({"name": "uncertain.txt", "mimeType": "text/plain", "buffer": b"Owned test file"})
    page.get_by_role("button", name="Проверить и подтвердить", exact=True).click()
    page.evaluate("""() => {
      const original = window.fetch;
      window.fetch = async (...args) => {
        const result = await original(...args);
        if (args[1]?.method === 'POST' && String(args[0]).includes('/materials')) throw new TypeError('controlled delivery failure');
        return result;
      };
    }""")
    page.get_by_role("button", name="Подтвердить отправку", exact=True).evaluate("(button) => { button.click(); button.click(); }")
    expect(page.get_by_role("alert")).to_contain_text("результат неизвестен")
    page.get_by_role("button", name="Проверить актуальные данные", exact=True).click()
    expect(page.get_by_role("table", name="Версии материалов", exact=True).locator("tbody tr")).to_have_count(1)
    expect(page.get_by_role("button", name="Загрузить материал", exact=True)).to_have_count(0)


def test_mutation_logout_discards_real_late_result(browser_page, scenario):
    page, case = browser_page, scenario
    login(page, case, "teacher")
    page.get_by_role("button", name="Темы", exact=True).click()
    page.get_by_role("button", name="Создать тему", exact=True).click()
    page.get_by_label("Название темы", exact=True).fill("Late mutation test")
    page.get_by_role("button", name="Проверить и подтвердить", exact=True).click()
    page.evaluate("""() => {
      const original = window.fetch;
      window.fetch = async (...args) => {
        const result = await original(...args);
        if (args[1]?.method === 'POST' && String(args[0]) === '/topics') {
          document.body.dataset.mutationReceived = "true";
          await new Promise(resolve => { window.releaseMutation = resolve; });
        }
        return result;
      };
    }""")
    page.get_by_role("button", name="Подтвердить отправку", exact=True).click()
    expect(page.locator("body")).to_have_attribute("data-mutation-received", "true")
    switch(page, case, "admin")
    page.evaluate("window.releaseMutation()")
    expect(page.get_by_role("heading", name="Уведомления", exact=True)).to_be_visible()
    expect(page.get_by_role("region", name="Подтверждение", exact=True)).to_have_count(0)
    expect(page.get_by_text("Late mutation test", exact=True)).to_have_count(0)
    assert page.evaluate("localStorage.length === 0 && sessionStorage.length === 0")


@pytest.mark.parametrize("actor,actions", [
    ("student", ["Предложить план"]),
    ("supervisor", ["Предложить план", "Утвердить план"]),
    ("teacher", ["Назначить руководителя", "Предложить план", "Утвердить план"]),
    ("department", ["Назначить руководителя"]),
    ("admin", []),
])
def test_academic_action_visibility_by_permission_and_scope(browser_page, scenario, actor, actions):
    page, case = browser_page, scenario
    login(page, case, actor)
    if actor == "admin":
        expect(page.get_by_role("navigation").get_by_role("button", name="Проекты", exact=True)).to_have_count(0)
        expect(page.get_by_role("navigation").get_by_role("button", name="Темы", exact=True)).to_have_count(0)
        return
    if actor != "department":
        expect(page.get_by_role("button", name=f"Открыть проект {case.other_project_id}", exact=True)).to_have_count(0)
    detail(page, case.project_id)
    for name in ["Назначить руководителя", "Предложить план", "Утвердить план", "Изменить даты"]:
        expect(page.get_by_role("button", name=name, exact=True)).to_have_count(int(name in actions))
    page.get_by_role("button", name="Материалы этапа 1", exact=True).click()
    expect(page.get_by_role("heading", name="История материалов", exact=False)).to_be_visible()
    expect(page.get_by_role("button", name="Загрузить материал", exact=True)).to_have_count(0)
    expect(page.get_by_role("button", name="Проверить последнюю версию", exact=True)).to_have_count(0)


@pytest.mark.parametrize("status", [403, 404])
def test_real_backend_rejection_of_stale_authorized_form(browser_page, scenario, status):
    page, case = browser_page, scenario
    login(page, case, "teacher")
    removed = None
    try:
        if status == 403:
            page.get_by_role("button", name="Темы", exact=True).click()
            page.get_by_role("button", name="Создать тему", exact=True).click()
            page.get_by_label("Название темы", exact=True).fill("Forbidden topic")
            method, path = "POST", "/topics"
            page.get_by_role("button", name="Проверить и подтвердить", exact=True).click()
            with case.engine.begin() as connection:
                removed = connection.execute(text("""
                    DELETE FROM public.role_permissions
                    WHERE role_id = (SELECT id FROM public.roles WHERE code = 'teacher')
                      AND permission_id = (SELECT id FROM public.permissions WHERE code = 'topics.manage')
                    RETURNING role_id, permission_id
                """)).one()
        else:
            detail(page, case.project_id)
            page.get_by_role("button", name="Назначить руководителя", exact=True).click()
            page.get_by_label("Руководитель ID", exact=True).fill(str(case.actors["unassigned"]["id"]))
            method, path = "PUT", f"/projects/{case.project_id}/supervisor"
            page.get_by_role("button", name="Проверить и подтвердить", exact=True).click()
            with case.engine.begin() as connection:
                connection.execute(text("UPDATE public.projects SET teacher_id = :teacher WHERE id = :id"),
                                   {"teacher": case.actors["teacher2"]["id"], "id": case.project_id})
        before = case.snapshot()
        with page.expect_response(lambda response: response.url.endswith(path) and response.request.method == method) as rejected:
            page.get_by_role("button", name="Подтвердить отправку", exact=True).click()
        assert rejected.value.status == status
        expect(page.get_by_role("alert")).to_contain_text("Недостаточно прав" if status == 403 else "Ресурс не найден")
        expect(page.get_by_text("Операция подтверждена сервером.", exact=True)).to_have_count(0)
        assert case.snapshot() == before
        if status == 403:
            with case.engine.connect() as connection:
                assert connection.execute(text("SELECT count(*) FROM public.topics WHERE title = 'Forbidden topic'")).scalar_one() == 0
    finally:
        if removed:
            with case.engine.begin() as connection:
                connection.execute(text("""
                    INSERT INTO public.role_permissions (role_id, permission_id) VALUES (:role, :permission)
                """), {"role": removed.role_id, "permission": removed.permission_id})


def test_review_conflict_reads_history_without_repeating(browser_page, scenario):
    page, case = browser_page, scenario
    case.approve()
    material = case.upload()
    login(page, case, "supervisor")
    detail(page, case.project_id)
    page.get_by_role("button", name="Материалы этапа 1", exact=True).click()
    page.get_by_role("button", name="Проверить последнюю версию", exact=True).click()
    page.get_by_label("Решение", exact=True).select_option("accepted")
    page.get_by_role("button", name="Проверить и подтвердить", exact=True).click()
    case.review(material)
    before = case.snapshot()
    requests = []
    page.on("request", lambda request: requests.append(request.url) if request.method == "POST" and request.url.endswith("/review") else None)
    with page.expect_response(lambda response: response.url.endswith("/review") and response.request.method == "POST") as rejected:
        page.get_by_role("button", name="Подтвердить отправку", exact=True).click()
    assert rejected.value.status == 409
    expect(page.get_by_role("alert")).to_contain_text("Конфликт")
    assert case.snapshot() == before
    page.get_by_role("button", name="Проверить актуальные данные", exact=True).click()
    expect(page.get_by_role("table", name="Версии материалов", exact=True)).to_contain_text("Принято")
    expect(page.get_by_role("button", name="Проверить последнюю версию", exact=True)).to_have_count(0)
    expect(page.get_by_text("Этап: Завершен. Проект:", exact=False)).to_contain_text("33.33%")
    assert len(requests) == 1


@pytest.mark.parametrize("filename,size", [
    ("empty.txt", 0),
    ("bad.exe", 10),
    ("large.txt", 20 * 1024 * 1024 + 1),
], ids=["empty", "extension", "oversize"])
def test_upload_client_validation_without_mutation(browser_page, scenario, filename, size):
    page, case = browser_page, scenario
    case.approve()
    login(page, case)
    detail(page, case.project_id)
    page.get_by_role("button", name="Материалы этапа 1", exact=True).click()
    page.get_by_role("button", name="Загрузить материал", exact=True).click()
    before = case.snapshot()
    page.get_by_label("Файл материала", exact=True).set_input_files({
        "name": filename, "mimeType": "application/octet-stream", "buffer": b"x" * size,
    })
    page.get_by_role("button", name="Проверить и подтвердить", exact=True).click()
    expect(page.get_by_role("alert")).to_have_text("Выберите непустой TXT/PDF/DOCX размером до 20 МиБ.")
    expect(page.get_by_role("region", name="Подтверждение", exact=True)).to_have_count(0)
    assert case.snapshot() == before


def test_project_and_topic_pagination_is_bounded(browser_page, scenario):
    page, case = browser_page, scenario
    for _ in range(11):
        case.create_project("student", "supervisor")
    login(page, case)
    table = page.get_by_role("table", name="Проекты", exact=True)
    expect(table.locator("tbody tr")).to_have_count(10)
    page.get_by_role("button", name="Далее", exact=True).click()
    expect(page.get_by_text("Смещение: 10", exact=True)).to_be_visible()
    expect(table.locator("tbody tr")).to_have_count(2)
    page.get_by_role("button", name="Зарегистрировать проект", exact=True).click()
    expect(page.get_by_label("Утвержденная тема", exact=True).locator("option")).to_have_count(11)
    with page.expect_response(lambda response: "/topics?" in response.url and "offset=10" in response.url) as result:
        page.get_by_role("button", name="Далее", exact=True).click()
    expect(page.get_by_text("Смещение: 10", exact=True)).to_be_visible()
    assert len(result.value.json()) <= 10
