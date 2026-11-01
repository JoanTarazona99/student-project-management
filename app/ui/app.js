(() => {
  "use strict";
  const content = document.getElementById("content");
  const identity = document.getElementById("identity");
  const navigation = document.getElementById("navigation");
  const roles = {student: "Студент", supervisor: "Руководитель", teacher: "Преподаватель", department: "Кафедра", admin: "Администратор"};
  const states = {planning: "Планирование", planned: "Запланирован", in_progress: "В работе", under_review: "На проверке", revision: "На доработке", completed: "Завершен"};
  let token = null;
  let me = null;
  let generation = 0;
  let controller = new AbortController();

  function node(tag, text, attributes = {}) {
    const result = document.createElement(tag);
    if (text !== undefined) result.textContent = text;
    for (const [key, value] of Object.entries(attributes)) result.setAttribute(key, value);
    return result;
  }
  function button(text, action, disabled = false) {
    const result = node("button", text, {type: "button"});
    result.disabled = disabled;
    result.addEventListener("click", action);
    return result;
  }
  function message(text, error = false) {
    return node("p", text, {class: error ? "notice error" : "notice", role: error ? "alert" : "status"});
  }
  function date(value) {
    return value === null || value === undefined ? "—" : new Date(value).toISOString().replace("T", " ").replace(".000Z", " UTC").replace("Z", " UTC");
  }
  function invalidate() {
    controller.abort();
    controller = new AbortController();
    generation += 1;
    return generation;
  }
  function logout(explanation = "Вы вышли. Токен удален из памяти страницы.") {
    invalidate();
    token = me = null;
    identity.replaceChildren();
    navigation.replaceChildren();
    content.replaceChildren();
    login(explanation);
  }
  function nav() {
    identity.replaceChildren(node("p", `${me.first_name} ${me.last_name} · ${me.email} · ${me.roles.map(code => roles[code] || "Дополнительная роль").join(", ")}`));
    navigation.replaceChildren();
    if (me.permissions.includes("projects.read")) navigation.append(button("Проекты", () => open(() => projects())));
    if (me.permissions.includes("reports.read")) navigation.append(button("Отчеты", () => open(() => reports())));
    navigation.append(
      button("Уведомления", () => open(() => notifications())),
      button("Обновить права", () => open(async () => {
        me = await api("/auth/me");
        nav();
        content.replaceChildren(message("Права обновлены. Выберите доступный раздел."));
      })),
      button("Выйти", () => logout())
    );
  }
  async function api(path, options = {}) {
    const captured = generation;
    const headers = new Headers(options.headers);
    if (token) headers.set("Authorization", `Bearer ${token}`);
    let response;
    try {
      response = await fetch(path, {...options, headers, signal: controller.signal, credentials: "omit", cache: "no-store"});
    } catch (error) {
      if (captured !== generation || error.name === "AbortError") throw new DOMException("Отменено", "AbortError");
      throw new Error("Ошибка сети. Проверьте соединение и повторите запрос.");
    }
    if (captured !== generation) throw new DOMException("Отменено", "AbortError");
    if (response.status === 401) {
      logout(token ? "Сессия истекла или доступ отозван. Войдите снова." : "Неверный email или пароль.");
      throw new DOMException("Отменено", "AbortError");
    }
    if (response.status === 403) {
      // Refresh effective permissions without retrying the denied operation.
      if (path !== "/auth/me") {
        me = await api("/auth/me");
        nav();
      }
      throw new Error("Недостаточно прав. Доступ к разделу запрещен.");
    }
    if (response.status === 404) throw new Error("Ресурс не найден или недоступен.");
    if (response.status === 409) throw new Error("Конфликт данных. Обновите страницу раздела.");
    if (response.status === 422) throw new Error("Некорректные параметры. Проверьте значения фильтров.");
    if (!response.ok) throw new Error("Сервис временно недоступен. Повторите запрос позже.");
    let value;
    try {
      value = options.blob ? await response.blob() : await response.json();
    } catch (error) {
      if (captured !== generation || error.name === "AbortError") throw new DOMException("Отменено", "AbortError");
      throw new Error("Не удалось прочитать ответ сервера. Повторите запрос.");
    }
    if (captured !== generation) throw new DOMException("Отменено", "AbortError");
    return value;
  }
  async function open(view) {
    const captured = invalidate();
    content.replaceChildren(message("Загрузка…"));
    try {
      await view();
    } catch (error) {
      if (error.name === "AbortError" || captured !== generation) return;
      content.replaceChildren(message(error.message, true), button("Повторить", () => open(view)));
    }
  }
  function login(explanation = "Войдите для просмотра. После перезагрузки страницы потребуется новый вход.") {
    const form = node("form");
    const email = field(form, "Email", "email", "email", {required: "", autocomplete: "username"});
    const password = field(form, "Пароль", "password", "password", {required: "", autocomplete: "current-password"});
    const submit = node("button", "Войти", {type: "submit"});
    form.append(submit);
    content.replaceChildren(node("h2", "Вход"), message(explanation), form);
    form.addEventListener("submit", async event => {
      event.preventDefault();
      submit.disabled = true;
      const captured = invalidate();
      const credentials = {email: email.value, password: password.value};
      password.value = "";
      const feedback = message("Выполняется вход…");
      form.append(feedback);
      try {
        const result = await api("/auth/login", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(credentials)});
        credentials.password = "";
        token = result.access_token;
        me = await api("/auth/me");
        nav();
        await open(me.permissions.includes("projects.read") ? () => projects() : () => notifications());
      } catch (error) {
        if (error.name !== "AbortError" && captured === generation) {
          token = me = null;
          feedback.replaceWith(message(error.message, true));
          submit.disabled = false;
        }
      } finally {
        credentials.password = "";
      }
    });
  }
  function field(parent, label, name, type = "text", attributes = {}) {
    const wrapper = node("label", label, type === "checkbox" ? {class: "check"} : {});
    const input = node("input", undefined, {name, type, ...attributes});
    wrapper.append(input);
    parent.append(wrapper);
    return input;
  }
  function table(parent, caption, headers, rows) {
    if (!rows.length) {
      parent.append(message("Нет данных."));
      return;
    }
    const wrap = node("div", undefined, {class: "table-wrap", tabindex: "0", role: "region", "aria-label": caption});
    const result = node("table");
    result.append(node("caption", caption));
    const head = node("tr");
    headers.forEach(value => head.append(node("th", value, {scope: "col"})));
    const thead = node("thead"); thead.append(head); result.append(thead);
    const body = node("tbody");
    rows.forEach(values => {
      const row = node("tr");
      values.forEach(value => {
        const cell = node("td");
        cell.append(value instanceof Node ? value : document.createTextNode(String(value ?? "—")));
        row.append(cell);
      });
      body.append(row);
    });
    result.append(body); wrap.append(result); parent.append(wrap);
  }
  function pager(parent, offset, limit, hasNext, go) {
    const actions = node("div", undefined, {class: "actions"});
    actions.append(button("Назад", () => open(() => go(Math.max(0, offset - limit))), offset === 0),
      node("span", `Смещение: ${offset}`),
      button("Далее", () => open(() => go(offset + limit)), !hasNext));
    parent.append(actions);
  }
  async function projects(offset = 0) {
    const limit = 10;
    const rows = await api(`/projects?limit=${limit}&offset=${offset}`);
    content.replaceChildren(node("h2", "Проекты"), message("Список доступных проектов. Общий счетчик API не предоставляет; полная страница может вести к пустой следующей."));
    table(content, "Проекты", ["ID", "Тема ID", "Студент ID", "Руководитель ID", "Преподаватель ID", "Создан (UTC)", "План", "Действие"],
      rows.map(row => [row.id, row.topic_id, row.student_id, row.supervisor_id, row.teacher_id, date(row.created_at),
        row.plan_approved_at ? "Утвержден" : "Не утвержден", button(`Открыть проект ${row.id}`, () => open(() => detail(row.id)))]));
    pager(content, offset, limit, rows.length === limit, next => projects(next));
  }
  async function detail(id) {
    const project = await api(`/projects/${id}`);
    const plan = await api(`/projects/${id}/plan`);
    const status = me.permissions.includes("statuses.read") ? await api(`/projects/${id}/status`) : null;
    content.replaceChildren(node("h2", `Проект № ${id}`));
    const values = node("dl");
    const pairs = [
      ["Тема ID", project.topic_id], ["Студент ID", project.student_id], ["Руководитель ID", project.supervisor_id ?? "—"],
      ["Преподаватель ID", project.teacher_id], ["Создан (UTC)", date(project.created_at)],
      ["План", plan.approved ? "Утвержден" : "Не утвержден"], ["Утвержден (UTC)", date(plan.approved_at)],
      ["Состояние", status ? status.status_name : "Нет разрешения на просмотр состояния"],
      ["Прогресс", status ? status.progress_percent === null ? "Нет этапов" : `${status.progress_percent}%` : "—"],
      ["Срок истек", status ? status.is_overdue ? "Да" : "Нет" : "—"]
    ];
    pairs.forEach(([label, value]) => values.append(node("dt", label), node("dd", value)));
    content.append(values);
    const stages = status ? status.stages : plan.stages;
    table(content, "Этапы", ["Позиция", "Название", "Начало (UTC)", "Срок (UTC)", "Состояние", "Завершен (UTC)", "Срок истек", "Материалы"],
      [...stages].sort((a, b) => a.position - b.position).map(stage => [
        stage.position, stage.title, date(stage.starts_at), date(stage.due_at), states[stage.status] || "Неизвестное состояние",
        date(stage.completed_at), status ? stage.is_overdue ? "Да" : "Нет" : "—",
        button(`Материалы этапа ${stage.position}`, () => open(() => materials(id, stage.id)))
      ]));
    content.append(button("К проектам", () => open(() => projects())));
  }
  async function download(row, result) {
    result.replaceChildren(message("Загрузка файла…"));
    try {
      const blob = await api(`/materials/${row.id}/download`, {blob: true});
      const url = URL.createObjectURL(blob);
      try {
        const link = node("a", undefined, {href: url});
        link.download = (row.original_filename || "material").replace(/[\\/\u0000-\u001f\u007f]/g, "_").slice(0, 255);
        document.body.append(link); link.click(); link.remove();
      } finally {
        // Keep the Blob alive until the browser has dispatched the download.
        setTimeout(() => URL.revokeObjectURL(url), 1000);
      }
      result.replaceChildren(message("Файл передан браузеру."));
    } catch (error) {
      if (error.name !== "AbortError") result.replaceChildren(message(error.message, true));
    }
  }
  async function materials(projectId, stageId, offset = 0) {
    const limit = 10;
    const rows = await api(`/stages/${stageId}/materials?limit=${limit}&offset=${offset}`);
    content.replaceChildren(node("h2", `История материалов · этап № ${stageId}`));
    const feedback = node("div", undefined, {"aria-live": "polite"});
    table(content, "Версии материалов", ["Версия", "Имя файла", "Загружен (UTC)", "Размер (байт)", "Решение", "Замечания", "Действие"],
      rows.map(row => [row.version_no, row.original_filename, date(row.uploaded_at), row.size_bytes,
        row.decision === "accepted" ? "Принято" : row.decision === "revision" ? "На доработку" : "Не проверено",
        row.remarks || "—", button(`Скачать версию ${row.version_no}`, () => download(row, feedback))]));
    content.append(feedback);
    pager(content, offset, limit, rows.length === limit, next => materials(projectId, stageId, next));
    content.append(button("К проекту", () => open(() => detail(projectId))));
  }
  async function notifications(offset = 0, unread = false) {
    const limit = 10;
    const rows = await api(`/notifications?limit=${limit}&offset=${offset}&unread_only=${unread}`);
    content.replaceChildren(node("h2", "Уведомления"));
    const filter = field(content, "Только непрочитанные", "unread", "checkbox");
    filter.checked = unread;
    filter.addEventListener("change", () => open(() => notifications(0, filter.checked)));
    table(content, "Уведомления", ["Сообщение", "Создано (UTC)", "Состояние", "Прочитано (UTC)", "Действие"],
      rows.map(row => [row.message, date(row.created_at), row.read_at ? "Прочитано" : "Не прочитано", date(row.read_at),
        button("Отметить прочитанным", () => open(async () => {
          await api(`/notifications/${row.id}/read`, {method: "PATCH"});
          await notifications(offset, unread);
        }), Boolean(row.read_at))]));
    pager(content, offset, limit, rows.length === limit, next => notifications(next, unread));
  }
  async function reports(filters = {}, offset = 0) {
    const limit = Number(filters.limit || 10);
    const query = new URLSearchParams({...filters, limit, offset});
    const data = await api(`/reports/projects?${query}`);
    content.replaceChildren(node("h2", "Отчеты"),
      message("Период фильтрует дату создания проекта: начало включительно, конец исключительно. Состояние текущее, не историческое. Сводка охватывает все отфильтрованные результаты, не только страницу."));
    const form = node("form");
    const fields = node("div", undefined, {class: "filters"});
    form.append(fields);
    for (const [name, label] of [["created_from", "Создан от (UTC, включительно)"], ["created_before", "Создан до (UTC, исключительно)"]]) {
      const input = field(fields, label, name, "datetime-local");
      input.step = "1";
      input.value = filters[name] ? filters[name].replace("Z", "") : "";
    }
    for (const [name, label] of [["student_id", "Студент ID"], ["supervisor_id", "Руководитель ID"], ["teacher_id", "Преподаватель ID"]]) {
      const input = field(fields, label, name, "text", {inputmode: "numeric", pattern: "[1-9][0-9]{0,18}"});
      input.value = filters[name] || "";
    }
    const label = node("label", "Состояние");
    const select = node("select", undefined, {name: "status", "aria-label": "Состояние"});
    select.append(node("option", "Все", {value: ""}));
    for (const [key, value] of Object.entries(states).filter(([key]) => key !== "planned")) select.append(node("option", value, {value: key}));
    select.value = filters.status || "";
    label.append(select); fields.append(label);
    const unread = field(fields, "Только с истекшим сроком", "overdue_only", "checkbox");
    unread.checked = filters.overdue_only === "true";
    const pageSize = field(fields, "На странице", "limit", "number", {min: "1", max: "100", required: ""});
    pageSize.value = limit;
    const pageOffset = field(fields, "Смещение страницы", "offset", "number", {min: "0", required: ""});
    pageOffset.value = offset;
    const submit = node("button", "Применить фильтры", {type: "submit"});
    const feedback = node("div");
    form.append(submit, button("Открыть смещение", () => {
      if (form.reportValidity()) open(() => reports(filters, Number(pageOffset.value)));
    }), feedback);
    form.addEventListener("submit", event => {
      event.preventDefault();
      const values = new FormData(form);
      const next = {};
      for (const [key, value] of values) {
        if (key === "offset") continue;
        if (!value) continue;
        if (key.endsWith("_id") && BigInt(value) > 9223372036854775807n) {
          feedback.replaceChildren(message("ID превышает допустимый bigint.", true)); return;
        }
        next[key] = key.startsWith("created_") ? `${value}${value.length === 16 ? ":00" : ""}Z` : value;
      }
      next.overdue_only = unread.checked ? "true" : "false";
      open(() => reports(next, 0));
    });
    content.append(form, node("h3", "Сводка"), message(`Всего найдено: ${data.total_matching}. Проектов с истекшим сроком: ${data.summary.overdue_projects}. Этапов: ${data.summary.total_stages}; завершено: ${data.summary.completed_stages}.`));
    table(content, "Количество по состояниям", ["Состояние", "Количество"],
      Object.entries(data.summary.status_counts).map(([key, value]) => [states[key], value]));
    if (!data.items.length) content.append(message(data.total_matching ? "Страница пуста: уменьшите смещение." : "Нет проектов по выбранным фильтрам."));
    else table(content, "Результаты отчета", ["ID", "Тема", "Студент ID", "Руководитель ID", "Преподаватель ID", "Создан (UTC)", "Состояние", "Прогресс", "Срок истек"],
      data.items.map(row => [row.project_id, row.topic_title, row.student_id, row.supervisor_id, row.teacher_id, date(row.created_at), row.status_name,
        row.progress_percent === null ? "Нет этапов" : `${row.progress_percent}%`, row.is_overdue ? "Да" : "Нет"]));
    pager(content, offset, limit, offset + data.items.length < data.total_matching, next => reports(filters, next));
  }
  login();
})();
