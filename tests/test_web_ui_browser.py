import socket
from threading import Thread
from time import monotonic

import httpx
import pytest
import uvicorn
from playwright.sync_api import expect, sync_playwright
from sqlalchemy import text

from conftest import TEST_PASSWORD


@pytest.fixture
def browser_page(scenario, backend):
    # Bind our own socket, not an existing Uvicorn or the user's browser.
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    url = f"http://127.0.0.1:{sock.getsockname()[1]}"
    server = uvicorn.Server(uvicorn.Config(
        backend.app, host="127.0.0.1", log_level="critical", access_log=False,
        lifespan="off", timeout_graceful_shutdown=3,
    ))
    thread = Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    try:
        deadline = monotonic() + 15
        while not server.started:
            if not thread.is_alive() or monotonic() > deadline:
                raise RuntimeError("Owned UI test HTTP server did not start")
            thread.join(timeout=0.02)
        with httpx.Client() as client:
            assert client.get(url + "/ui").status_code == 200
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            context = browser.new_context(accept_downloads=True)
            page = context.new_page()
            page.set_default_timeout(8000)
            page.set_default_navigation_timeout(10000)
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            try:
                page.goto(url + "/ui")
                yield page
                assert not errors, errors
            finally:
                context.close()
                browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()
        if thread.is_alive():
            raise RuntimeError("Owned UI HTTP server failed to stop")


def login(page, case, actor="student"):
    page.get_by_label("Email", exact=True).fill(case.actors[actor]["email"])
    page.get_by_label("Пароль", exact=True).fill(TEST_PASSWORD)
    page.get_by_role("button", name="Войти", exact=True).click()
    expect(page.get_by_role("button", name="Выйти", exact=True)).to_be_visible()
    expect(page.get_by_role("heading", name="Проекты" if actor != "admin" else "Уведомления", exact=True)).to_be_visible()


@pytest.fixture
def project_creation_time_guard(scenario):
    with scenario.engine.connect() as connection:
        original = connection.execute(text("SELECT created_at FROM public.projects WHERE id = :id"),
                                      {"id": scenario.project_id}).scalar_one()
    try:
        yield
    finally:
        with scenario.engine.begin() as connection:
            connection.execute(text("UPDATE public.projects SET created_at = :at WHERE id = :id"),
                               {"at": original, "id": scenario.project_id})


def test_login_invalid_valid_local_logout_and_memory_only(browser_page, scenario):
    page = browser_page
    page.get_by_label("Email", exact=True).fill(scenario.actors["student"]["email"])
    page.get_by_label("Пароль", exact=True).fill("wrong-test-password")
    page.get_by_role("button", name="Войти", exact=True).click()
    expect(page.get_by_text("Неверный email или пароль.", exact=True)).to_be_visible()
    expect(page.get_by_label("Пароль", exact=True)).to_have_value("")
    login(page, scenario)
    assert page.evaluate("localStorage.length === 0 && sessionStorage.length === 0")
    assert page.context.cookies() == []
    page.get_by_role("button", name="Выйти", exact=True).click()
    expect(page.get_by_role("heading", name="Вход", exact=True)).to_be_visible()
    expect(page.get_by_text(scenario.actors["student"]["email"], exact=False)).to_have_count(0)
    login(page, scenario)
    page.reload()
    expect(page.get_by_role("heading", name="Вход", exact=True)).to_be_visible()
    assert page.evaluate("localStorage.length === 0 && sessionStorage.length === 0")


@pytest.mark.parametrize("actor,has_projects,has_reports", [
    ("student", True, False), ("supervisor", True, False),
    ("teacher", True, True), ("teacher_department", True, True), ("admin", False, False),
])
def test_effective_permission_navigation(browser_page, scenario, actor, has_projects, has_reports):
    page = browser_page
    login(page, scenario, actor)
    nav = page.get_by_role("navigation")
    expect(nav.get_by_role("button", name="Проекты", exact=True)).to_have_count(int(has_projects))
    expect(nav.get_by_role("button", name="Отчеты", exact=True)).to_have_count(int(has_reports))
    expect(nav.get_by_role("button", name="Уведомления", exact=True)).to_be_visible()
    expect(page.locator("#identity")).to_contain_text(scenario.actors[actor]["email"])


def test_project_pagination_without_n_plus_one_and_detail(browser_page, scenario):
    page = browser_page
    scenario.approve()
    for _ in range(10):
        scenario.create_project("student", "supervisor")
    requests = []
    page.on("request", lambda request: requests.append(request.url))
    login(page, scenario)
    expect(page.get_by_role("table", name="Проекты", exact=True).locator("tbody tr")).to_have_count(10)
    assert not any("/status" in url for url in requests)
    page.get_by_role("button", name="Далее", exact=True).click()
    expect(page.get_by_text("Смещение: 10", exact=True)).to_be_visible()
    expect(page.get_by_role("table", name="Проекты", exact=True).locator("tbody tr")).to_have_count(1)
    expect(page.get_by_role("button", name="Далее", exact=True)).to_be_disabled()
    page.get_by_role("button", name="Назад", exact=True).click()
    page.get_by_role("button", name=f"Открыть проект {scenario.project_id}", exact=True).click()
    expect(page.get_by_role("heading", name=f"Проект № {scenario.project_id}", exact=True)).to_be_visible()
    expect(page.locator("dl")).to_contain_text("0%")
    expect(page.locator("dl")).to_contain_text("В работе")
    expect(page.get_by_role("table", name="Этапы", exact=True).locator("tbody tr")).to_have_count(3)
    expect(page.get_by_role("table", name="Этапы", exact=True)).to_contain_text("2099-01-01 00:00:00 UTC")
    assert not any("/materials?" in url for url in requests)


def test_material_history_authenticated_download_and_safe_text(browser_page, scenario, tmp_path):
    case = scenario
    case.approve()
    data = b"Browser integration download\n"
    material = case.upload(data=data, filename="<img onerror=alert(1)>.txt")
    case.review(material, "revision", "<script>window.injected=true</script>")
    second = case.upload(data=b"Second version")
    page = browser_page
    login(page, case)
    page.get_by_role("button", name=f"Открыть проект {case.project_id}", exact=True).click()
    page.get_by_role("button", name="Материалы этапа 1", exact=True).click()
    expect(page.get_by_role("table", name="Версии материалов", exact=True).locator("tbody tr")).to_have_count(2)
    expect(page.get_by_text("<script>window.injected=true</script>", exact=True)).to_be_visible()
    assert page.locator("#content script, #content img").count() == 0
    assert not page.evaluate("Boolean(window.injected)")
    with page.expect_download() as downloaded:
        page.get_by_role("button", name="Скачать версию 1", exact=True).click()
    download = downloaded.value
    path = tmp_path / "download.txt"
    download.save_as(path)
    assert path.read_bytes() == data
    assert download.suggested_filename.endswith(".txt")
    assert "storage_key" not in page.locator("#content").inner_text()
    assert str(second["version_no"]) in page.locator("#content").inner_text()


def test_notifications_filter_read_and_safe_message(browser_page, scenario):
    case = scenario
    case.approve()
    with case.engine.begin() as connection:
        connection.execute(text("""
            UPDATE public.notifications SET message = '<img src=x onerror=window.injected=true>'
            WHERE project_id = :id AND recipient_id = :user
        """), {"id": case.project_id, "user": case.actors["student"]["id"]})
    page = browser_page
    login(page, case)
    page.get_by_role("button", name="Уведомления", exact=True).click()
    expect(page.get_by_text("<img src=x onerror=window.injected=true>", exact=True)).to_be_visible()
    assert page.locator("#content img").count() == 0
    page.get_by_role("button", name="Отметить прочитанным", exact=True).click()
    expect(page.get_by_role("button", name="Отметить прочитанным", exact=True)).to_be_disabled()
    expect(page.get_by_role("cell", name="Прочитано", exact=True)).to_be_visible()
    page.get_by_label("Только непрочитанные", exact=True).check()
    expect(page.get_by_text("Нет данных.", exact=True)).to_be_visible()
    page.get_by_label("Только непрочитанные", exact=True).uncheck()
    expect(page.get_by_role("button", name="Отметить прочитанным", exact=True)).to_be_disabled()


def test_reports_filters_utc_summary_and_empty_offset(browser_page, scenario, project_creation_time_guard):
    case = scenario
    case.approve()
    with case.engine.begin() as connection:
        connection.execute(text("UPDATE public.projects SET created_at = :date WHERE id = :id"),
                           {"date": "2040-01-01T12:00:00Z", "id": case.project_id})
        connection.execute(text("UPDATE public.topics SET title = '<script>window.injected=true</script>' WHERE id = :id"),
                           {"id": case.request("student", "GET", f"/projects/{case.project_id}", 200)["topic_id"]})
    page = browser_page
    login(page, case, "teacher")
    page.get_by_role("button", name="Отчеты", exact=True).click()
    expect(page.get_by_text("Всего найдено: 1.", exact=False)).to_be_visible()
    page.get_by_label("Создан от (UTC, включительно)", exact=True).fill("2040-01-01T00:00")
    page.get_by_label("Создан до (UTC, исключительно)", exact=True).fill("2040-01-02T00:00")
    page.get_by_label("Студент ID", exact=True).fill(str(case.actors["student"]["id"]))
    page.get_by_label("Руководитель ID", exact=True).fill(str(case.actors["supervisor"]["id"]))
    page.get_by_label("Преподаватель ID", exact=True).fill(str(case.actors["teacher"]["id"]))
    page.get_by_label("Состояние", exact=True).select_option("in_progress")
    page.get_by_label("На странице", exact=True).fill("1")
    with page.expect_request(lambda request: "/reports/projects?" in request.url and "created_from=" in request.url) as sent:
        page.get_by_role("button", name="Применить фильтры", exact=True).click()
    assert "2040-01-01T00%3A00%3A00Z" in sent.value.url
    expect(page.get_by_text("<script>window.injected=true</script>", exact=True)).to_be_visible()
    assert not page.evaluate("Boolean(window.injected)")
    page.get_by_label("Смещение страницы", exact=True).fill("100")
    page.get_by_role("button", name="Открыть смещение", exact=True).click()
    expect(page.get_by_text("Страница пуста: уменьшите смещение.", exact=True)).to_be_visible()
    expect(page.get_by_text("Всего найдено: 1.", exact=False)).to_be_visible()
    page.get_by_label("Студент ID", exact=True).fill(str(case.actors["outsider"]["id"]))
    page.get_by_role("button", name="Применить фильтры", exact=True).click()
    expect(page.get_by_text("Нет проектов по выбранным фильтрам.", exact=True)).to_be_visible()
    expect(page.get_by_label("Смещение страницы", exact=True)).to_have_value("0")


def test_real_permission_revocation_403_refresh_and_401(browser_page, scenario):
    case = scenario
    page = browser_page
    login(page, case, "teacher")
    role = permission = None
    with case.engine.begin() as connection:
        role, permission = connection.execute(text("""
            DELETE FROM public.role_permissions
            WHERE role_id = (SELECT id FROM public.roles WHERE code='teacher')
              AND permission_id = (SELECT id FROM public.permissions WHERE code='reports.read')
            RETURNING role_id, permission_id
        """)).one()
    try:
        page.get_by_role("button", name="Отчеты", exact=True).click()
        expect(page.get_by_role("alert")).to_contain_text("Недостаточно прав")
        expect(page.get_by_role("navigation").get_by_role("button", name="Отчеты", exact=True)).to_have_count(0)
    finally:
        with case.engine.begin() as connection:
            connection.execute(text("INSERT INTO public.role_permissions VALUES (:role, :permission)"),
                               {"role": role, "permission": permission})
    page.get_by_role("button", name="Обновить права", exact=True).click()
    expect(page.get_by_role("navigation").get_by_role("button", name="Отчеты", exact=True)).to_be_visible()
    case.request("admin", "PATCH", f"/admin/users/{case.actors['teacher']['id']}/status", 200, json={"is_active": False})
    page.get_by_role("button", name="Проекты", exact=True).click()
    expect(page.get_by_role("heading", name="Вход", exact=True)).to_be_visible()
    expect(page.get_by_text("Сессия истекла или доступ отозван. Войдите снова.", exact=True)).to_be_visible()
    expect(page.locator("#identity")).to_be_empty()


def test_real_unavailable_resource_and_network_retry(browser_page, scenario):
    case = scenario
    page = browser_page
    login(page, case)
    with case.engine.begin() as connection:
        connection.execute(text("UPDATE public.projects SET student_id = :user WHERE id = :id"),
                           {"id": case.project_id, "user": case.actors["outsider"]["id"]})
    page.get_by_role("button", name=f"Открыть проект {case.project_id}", exact=True).click()
    expect(page.get_by_role("alert")).to_have_text("Ресурс не найден или недоступен.")
    page.route("**/projects?*", lambda route: route.abort("failed"))
    page.get_by_role("button", name="Проекты", exact=True).click()
    expect(page.get_by_role("alert")).to_contain_text("Ошибка сети")
    page.unroute("**/projects?*")
    page.get_by_role("button", name="Повторить", exact=True).click()
    expect(page.get_by_role("heading", name="Проекты", exact=True)).to_be_visible()
    expect(page.get_by_text("Нет данных.", exact=True)).to_be_visible()


def test_delayed_real_response_cannot_repopulate_after_logout_or_new_login(browser_page, scenario):
    page = browser_page
    login(page, scenario)
    # Delay delivery of a real authenticated API response, not its authorization.
    page.evaluate("""() => {
      const original = window.fetch;
      window.fetch = async (...args) => {
        const response = await original(...args);
        if (String(args[0]).startsWith('/projects?')) {
          window.received = true;
          await new Promise(resolve => { window.releaseResponse = resolve; });
        }
        return response;
      };
    }""")
    page.get_by_role("button", name="Проекты", exact=True).click()
    page.wait_for_function("window.received === true")
    page.get_by_role("button", name="Выйти", exact=True).click()
    login(page, scenario, "admin")
    page.evaluate("window.releaseResponse()")
    expect(page.get_by_role("heading", name="Уведомления", exact=True)).to_be_visible()
    expect(page.get_by_role("table", name="Проекты", exact=True)).to_have_count(0)
    expect(page.locator("#identity")).to_contain_text(scenario.actors["admin"]["email"])


def test_narrow_viewport_and_keyboard_login(browser_page, scenario):
    page = browser_page
    page.set_viewport_size({"width": 360, "height": 800})
    page.get_by_label("Email", exact=True).focus()
    page.keyboard.type(scenario.actors["student"]["email"])
    page.keyboard.press("Tab")
    expect(page.get_by_label("Пароль", exact=True)).to_be_focused()
    page.keyboard.type(TEST_PASSWORD)
    page.keyboard.press("Tab")
    expect(page.get_by_role("button", name="Войти", exact=True)).to_be_focused()
    page.keyboard.press("Enter")
    expect(page.get_by_role("heading", name="Проекты", exact=True)).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    expect(page.get_by_role("region", name="Проекты", exact=True)).to_be_visible()


def test_material_and_notification_pagination(browser_page, scenario):
    case = scenario
    case.approve()
    for index in range(11):
        material = case.upload(data=f"Version {index}".encode())
        if index < 10:
            case.review(material, "revision", "Исправить версию")
    page = browser_page
    login(page, case)
    page.get_by_role("button", name=f"Открыть проект {case.project_id}", exact=True).click()
    page.get_by_role("button", name="Материалы этапа 1", exact=True).click()
    expect(page.get_by_role("table", name="Версии материалов", exact=True).locator("tbody tr")).to_have_count(10)
    page.get_by_role("button", name="Далее", exact=True).click()
    expect(page.get_by_role("button", name="Скачать версию 1", exact=True)).to_be_visible()
    expect(page.get_by_role("table", name="Версии материалов", exact=True).locator("tbody tr")).to_have_count(1)
    page.get_by_role("button", name="Уведомления", exact=True).click()
    expect(page.get_by_role("table", name="Уведомления", exact=True).locator("tbody tr")).to_have_count(10)
    page.get_by_role("button", name="Далее", exact=True).click()
    expect(page.get_by_role("table", name="Уведомления", exact=True).locator("tbody tr")).to_have_count(1)
    expect(page.get_by_role("button", name="Далее", exact=True)).to_be_disabled()


def test_no_stages_null_progress_and_service_error(browser_page, scenario):
    case = scenario
    empty = case.create_project("student", "supervisor")
    page = browser_page
    login(page, case)
    page.get_by_role("button", name=f"Открыть проект {empty}", exact=True).click()
    expect(page.locator("dl")).to_contain_text("Нет этапов")
    expect(page.get_by_text("Нет данных.", exact=True)).to_be_visible()
    # An owned, deliberately unavailable uploaded file exercises real 503.
    case.approve()
    material = case.upload()
    with case.engine.connect() as connection:
        key = connection.execute(text("SELECT storage_key FROM public.material_versions WHERE id = :id"),
                                 {"id": material["id"]}).scalar_one()
    (case.storage / key).unlink()
    page.get_by_role("button", name="Проекты", exact=True).click()
    page.get_by_role("button", name=f"Открыть проект {case.project_id}", exact=True).click()
    page.get_by_role("button", name="Материалы этапа 1", exact=True).click()
    page.get_by_role("button", name="Скачать версию 1", exact=True).click()
    expect(page.get_by_role("alert")).to_have_text("Сервис временно недоступен. Повторите запрос позже.")


def test_report_invalid_period_and_overdue_filter(browser_page, scenario):
    page = browser_page
    login(page, scenario, "teacher")
    page.get_by_role("button", name="Отчеты", exact=True).click()
    page.get_by_label("Только с истекшим сроком", exact=True).check()
    page.get_by_role("button", name="Применить фильтры", exact=True).click()
    expect(page.get_by_text("Нет проектов по выбранным фильтрам.", exact=True)).to_be_visible()
    page.get_by_label("Создан от (UTC, включительно)", exact=True).fill("2040-01-02T00:00")
    page.get_by_label("Создан до (UTC, исключительно)", exact=True).fill("2040-01-01T00:00")
    page.get_by_role("button", name="Применить фильтры", exact=True).click()
    expect(page.get_by_role("alert")).to_have_text("Некорректные параметры. Проверьте значения фильтров.")
