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
    if (me.permissions.includes("projects.read")) navigation.append(button("Темы", () => open(() => topics())));
    if (me.permissions.includes("reports.read")) navigation.append(button("Отчеты", () => open(() => reports())));
    if (allowed("users.manage")) navigation.append(button("Учетные записи", () => open(() => accounts())));
    if (allowed("roles.manage")) navigation.append(button("Роли", () => open(() => roleCatalog())));
    if (roleAdministrator()) navigation.append(button("Разрешения", () => open(() => permissionCatalog())));
    navigation.append(button("Мои эффективные права", () => open(() => effectivePermissions())));
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
    if (response.status === 409) throw Object.assign(new Error("Конфликт данных. Обновите страницу раздела."), {status: 409});
    if (response.status === 422) throw Object.assign(new Error("Некорректные параметры. Проверьте значения фильтров."), {status: 422});
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
  async function open(view, confirmed = "") {
    const captured = invalidate();
    content.replaceChildren(message("Загрузка…"));
    try {
      await view();
    } catch (error) {
      if (error.name === "AbortError" || captured !== generation) return;
      const info = message(`${confirmed}${error.message}`, true);
      content.replaceChildren(info, button("Повторить", () => open(view, confirmed)));
      focus(info);
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
    if (allowed("projects.create")) content.append(button("Зарегистрировать проект", () => open(() => registerProject())));
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
    academicActions(project, plan, status);
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
    const project = await api(`/projects/${projectId}`);
    const plan = await api(`/projects/${projectId}/plan`);
    const status = allowed("statuses.read") ? await api(`/projects/${projectId}/status`) : null;
    const stage = plan.stages.find(item => item.id === stageId);
    content.replaceChildren(node("h2", `История материалов · этап № ${stageId}`),
      message(`Этап: ${states[stage?.status] || "Неизвестное состояние"}. Проект: ${status ?
        `${status.status_name}, ${status.progress_percent === null ? "Нет этапов" : `${status.progress_percent}%`}` :
        "Нет разрешения на просмотр состояния"}.`));
    const feedback = node("div", undefined, {"aria-live": "polite"});
    table(content, "Версии материалов", ["Версия", "Имя файла", "Загружен (UTC)", "Размер (байт)", "Решение", "Замечания", "Действие"],
      rows.map(row => [row.version_no, row.original_filename, date(row.uploaded_at), row.size_bytes,
        row.decision === "accepted" ? "Принято" : row.decision === "revision" ? "На доработку" : "Не проверено",
        row.remarks || "—", button(`Скачать версию ${row.version_no}`, () => download(row, feedback))]));
    content.append(feedback);
    pager(content, offset, limit, rows.length === limit, next => materials(projectId, stageId, next));
    content.append(button("К проекту", () => open(() => detail(projectId))));
    if (stage && plan.approved && me.id === project.student_id && allowed("materials.upload") &&
        ["in_progress", "revision"].includes(stage.status) &&
        !plan.stages.some(item => item.position < stage.position && item.status !== "completed")) {
      content.append(button("Загрузить материал", () => open(() => upload(projectId, stageId))));
    }
    if (offset === 0 && stage?.status === "under_review" && responsible(project) && allowed("reviews.write") &&
        rows.length && rows[0].review_id === null) {
      content.append(button("Проверить последнюю версию", () => open(() => review(projectId, stageId, rows[0]))));
    }
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
        button("Отметить прочитанным", event => {
          const trigger = event.currentTarget;
          const feedback = node("div"); content.append(feedback);
          send(trigger, feedback, () => api(`/notifications/${row.id}/read`, {method: "PATCH"}),
            () => notifications(offset, unread));
        }, Boolean(row.read_at))]));
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
  function allowed(permission) { return me.permissions.includes(permission); }
  function assigned(project) { return [project.student_id, project.supervisor_id, project.teacher_id].includes(me.id); }
  function responsible(project) {
    return me.id !== project.student_id && [project.supervisor_id, project.teacher_id].includes(me.id);
  }
  function focus(element) {
    element.setAttribute("tabindex", "-1");
    element.focus();
  }
  function idField(parent, label, name, value = "") {
    const input = field(parent, label, name, "text", {required: "", inputmode: "numeric", pattern: "[1-9][0-9]{0,18}"});
    input.value = value;
    input.addEventListener("input", () => {
      input.setCustomValidity(/^[1-9][0-9]*$/.test(input.value) && BigInt(input.value) > 9223372036854775807n ?
        "ID превышает bigint." : "");
    });
    return input;
  }
  function utcField(parent, label, value, readonly = false) {
    const input = field(parent, label, label, "datetime-local", {required: ""});
    input.step = "0.000001";
    input.value = value ? new Date(value).toISOString().replace("Z", "") : "";
    input.dataset.originalValue = input.value;
    input.dataset.originalTimestamp = value || "";
    input.readOnly = readonly;
    return input;
  }
  function utcValue(input) {
    if (input.value === input.dataset.originalValue && input.dataset.originalTimestamp) return input.dataset.originalTimestamp;
    return `${input.value}${input.value.length === 16 ? ":00" : ""}Z`;
  }
  async function send(trigger, feedback, operation, refresh, success = "Операция подтверждена сервером.", rejected = () => {}) {
    if (trigger.disabled) return;
    trigger.disabled = true;
    const captured = generation;
    feedback.replaceChildren(message("Отправка…"));
    let result;
    try {
      result = await operation();
    } catch (error) {
      if (captured !== generation || error.name === "AbortError") return;
      const info = message(error.status === 409 ?
        "Конфликт: операция отклонена сервером. Черновик сохранен; проверьте ограничения и актуальные данные перед новым подтверждением." :
        error.status === 422 ? "Некорректные параметры. Проверьте поля формы." :
        `${error.message} Если результат неизвестен, проверьте данные перед повторным отправлением.`, true);
      feedback.replaceChildren(info, button("Проверить актуальные данные", () => open(refresh)));
      trigger.disabled = false;
      rejected();
      focus(info);
      return;
    }
    if (captured !== generation) return;
    await open(async () => {
      await refresh(result);
      const info = message(typeof success === "function" ? success(result) : success);
      content.prepend(info);
      focus(info);
    }, "Операция уже подтверждена. Не отправляйте ее повторно. Не удалось обновить данные: ");
  }
  function operationForm(title, back, build, prepare, refresh, success) {
    const form = node("form");
    const feedback = node("div", undefined, {"aria-live": "polite"});
    content.replaceChildren(node("h2", title), form);
    const controls = build(form);
    const submit = node("button", "Проверить и подтвердить", {type: "submit"});
    form.append(submit, button("Вернуться без отправки", () => open(back)), feedback);
    form.addEventListener("submit", event => {
      event.preventDefault();
      if (submit.disabled) return;
      let prepared;
      try { prepared = prepare(controls); }
      catch (error) {
        const info = message(error.message, true); feedback.replaceChildren(info); focus(info); return;
      }
      const enabled = [...form.querySelectorAll("input, textarea, select, button")].filter(item => !item.disabled);
      enabled.forEach(item => { item.disabled = true; });
      const unlock = () => { enabled.forEach(item => { item.disabled = false; }); };
      const summary = node("section", undefined, {"aria-label": "Подтверждение"});
      const confirm = button("Подтвердить отправку", () => {
        summary.querySelectorAll("button").forEach(item => { if (item !== confirm) item.disabled = true; });
        send(confirm, feedback, prepared.execute, refresh, success, unlock);
      });
      summary.append(node("h3", "Подтверждение"), node("pre", prepared.summary), confirm,
        button("Отмена", () => { if (prepared.dispose) prepared.dispose(); feedback.replaceChildren(); unlock(); submit.focus(); }));
      feedback.replaceChildren(summary); confirm.focus();
    });
    const first = form.querySelector("input, textarea, select");
    if (first) first.focus();
  }
  function jsonOperation(path, method, body) {
    return () => api(path, {method, headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
  }
  async function topics(offset = 0, created = null) {
    const rows = await api(`/topics?limit=10&offset=${offset}`);
    content.replaceChildren(node("h2", "Темы"));
    if (created && !created.approved_at && allowed("topics.manage")) {
      content.append(message(`Создана тема № ${created.id}: ${created.title}`),
        button(`Утвердить созданную тему ${created.id}`, () => operationForm("Утверждение темы", () => topics(),
          form => form.append(message(created.title)),
          () => ({summary: `Утвердить тему № ${created.id}: ${created.title}`,
            execute: () => api(`/topics/${created.id}/approve`, {method: "POST"})}), () => topics())));
    }
    table(content, "Темы", ["ID", "Название", "Описание", "Утверждение", "Действие"], rows.map(row => [
      row.id, row.title, row.description, row.approved_at ? "Утверждена" : "Не утверждена",
      allowed("topics.manage") && !row.approved_at ? button(`Утвердить тему ${row.id}`, () => operationForm(
        "Утверждение темы", () => topics(offset), form => { form.append(message(row.title)); },
        () => ({summary: `Утвердить тему № ${row.id}: ${row.title}`, execute: () => api(`/topics/${row.id}/approve`, {method: "POST"})}),
        () => topics(offset))) : "—"
    ]));
    pager(content, offset, 10, rows.length === 10, next => topics(next));
    if (allowed("topics.manage")) content.append(button("Создать тему", () => operationForm(
      "Создание темы", () => topics(), form => ({
        title: field(form, "Название темы", "title", "text", {required: "", maxlength: "300"}),
        description: field(form, "Описание темы", "description", "text", {maxlength: "10000"})
      }), inputs => {
        const body = {title: inputs.title.value.trim(), description: inputs.description.value};
        if (!body.title) throw new Error("Название не должно быть пустым.");
        return {summary: `Создать тему: ${body.title}\n${body.description}`, execute: jsonOperation("/topics", "POST", body)};
      }, result => topics(0, result))));
    if (allowed("projects.create")) content.append(button("Зарегистрировать проект", () => open(() => registerProject())));
  }
  async function registerProject(offset = 0) {
    const rows = await api(`/topics?limit=10&offset=${offset}`);
    operationForm("Регистрация проекта", () => projects(), form => {
      form.append(message("Каталог пользователей недоступен: укажите согласованные ID. Темы показываются страницами; поиска API нет."));
      const label = node("label", "Утвержденная тема");
      const select = node("select", undefined, {required: "", "aria-label": "Утвержденная тема"});
      select.append(node("option", "Выберите тему", {value: ""}));
      rows.filter(row => row.approved_at).forEach(row => select.append(node("option", `${row.id}: ${row.title}`, {value: row.id})));
      label.append(select); form.append(label);
      pager(form, offset, 10, rows.length === 10, next => registerProject(next));
      const teacher = idField(form, "Преподаватель ID", "teacher", me.roles.includes("teacher") ? String(me.id) : "");
      teacher.readOnly = me.roles.includes("teacher");
      const student = me.roles.includes("teacher") ? idField(form, "Студент ID", "student") : null;
      if (!student) form.append(message(`Студент: текущая учетная запись № ${me.id}.`));
      return {select, teacher, student};
    }, inputs => {
      const body = {topic_id: inputs.select.value, teacher_id: inputs.teacher.value};
      if (inputs.student) body.student_id = inputs.student.value;
      return {summary: `Тема № ${body.topic_id}; преподаватель № ${body.teacher_id}; студент № ${body.student_id || me.id}`,
        execute: jsonOperation("/projects", "POST", body)};
    }, result => result ? detail(result.id) : projects(), result => `Проект № ${result.id} создан.`);
  }
  function academicActions(project, plan) {
    const id = project.id;
    if (!plan.approved && allowed("supervisors.assign") &&
        (me.id === project.teacher_id || me.roles.includes("department"))) {
      content.append(button("Назначить руководителя", () => operationForm("Назначение руководителя", () => detail(id),
        form => idField(form, "Руководитель ID", "supervisor"),
        input => ({summary: `Изменить назначение руководителя проекта № ${id} на ID ${input.value}.`,
          execute: jsonOperation(`/projects/${id}/supervisor`, "PUT", {supervisor_id: input.value})}),
        () => detail(id))));
    }
    if (!plan.approved && allowed("plans.propose") && assigned(project)) {
      content.append(button("Предложить план", () => open(() => planEditor(id, false))));
    }
    if (responsible(project) && allowed("plans.approve")) {
      if (!plan.approved && plan.stages.length) content.append(button("Утвердить план", () => operationForm(
        "Утверждение плана", () => detail(id), form => {
          form.append(message(plan.stages.map(stage => `${stage.position}. ${stage.title}: ${date(stage.starts_at)} — ${date(stage.due_at)}`).join("\n")));
        }, () => ({summary: "Утвердить показанное предложение плана.", execute: () => api(`/projects/${id}/plan/approve`, {method: "POST"})}),
        () => detail(id))));
      if (plan.approved && plan.stages.some(stage => stage.status !== "completed")) {
        content.append(button("Изменить даты", () => open(() => planEditor(id, true))));
      }
    }
  }
  async function planEditor(id, dates) {
    const plan = await api(`/projects/${id}/plan`);
    operationForm(dates ? "Изменение дат" : "Предложение плана", () => detail(id), form => {
      form.append(message(dates ? "Ожидаемые даты сохранены из актуального GET. Завершенные этапы неизменны." :
        plan.stages.length ? "Существующее предложение будет заменено после подтверждения." : "Составьте последовательные этапы."));
      const reason = dates ? field(form, "Причина изменения", "reason", "text", {required: "", minlength: "10", maxlength: "2000"}) : null;
      const list = node("div");
      form.append(list);
      const rows = [];
      let addButton;
      function add(stage = {}) {
        const item = node("fieldset");
        item.append(node("legend", `Этап ${rows.length + 1}`));
        const title = dates ? null : field(item, "Название этапа", "title", "text", {required: "", maxlength: "200"});
        if (title) title.value = stage.title || "";
        if (dates) item.append(message(`${stage.position}. ${stage.title} · ${states[stage.status]}`));
        const starts = utcField(item, "Начало (UTC)", stage.starts_at, dates && stage.status === "completed");
        const due = utcField(item, "Срок (UTC)", stage.due_at, dates && stage.status === "completed");
        const row = {stage, title, starts, due, item};
        rows.push(row); list.append(item);
        if (!dates) item.append(button("Удалить этап", () => {
          rows.splice(rows.indexOf(row), 1); item.remove();
          rows.forEach((entry, index) => { entry.item.querySelector("legend").textContent = `Этап ${index + 1}`; });
          addButton.disabled = false;
        }));
      }
      [...plan.stages].sort((a, b) => a.position - b.position).forEach(add);
      if (!rows.length && !dates) add();
      if (!dates) {
        addButton = button("Добавить этап", () => { add(); addButton.disabled = rows.length >= 50; }, rows.length >= 50);
        form.append(addButton);
      }
      return {reason, rows};
    }, inputs => {
      if (!inputs.rows.length) throw new Error("Нужен хотя бы один этап.");
      const stages = inputs.rows.map(row => {
        const entry = {starts_at: utcValue(row.starts), due_at: utcValue(row.due)};
        if (dates) Object.assign(entry, {stage_id: row.stage.id,
          expected_starts_at: row.stage.starts_at, expected_due_at: row.stage.due_at});
        else entry.title = row.title.value.trim();
        if (!dates && !entry.title) throw new Error("Название этапа не должно быть пустым.");
        if (new Date(entry.due_at) <= new Date(entry.starts_at)) throw new Error("Срок должен быть позже начала.");
        return entry;
      });
      if (stages.some((stage, index) => index && new Date(stage.starts_at) < new Date(stages[index - 1].due_at))) {
        throw new Error("Этапы не должны пересекаться.");
      }
      const body = {stages};
      if (dates) {
        body.reason = inputs.reason.value.trim();
        if (body.reason.length < 10) throw new Error("Причина: минимум 10 символов.");
      }
      return {summary: `${dates ? "Изменить только даты" : "Заменить предложение"} проекта № ${id}\n${stages.map((stage, index) =>
        `${index + 1}. ${stage.title || inputs.rows[index].stage.title}: ${dates ?
          `${date(stage.expected_starts_at)} — ${date(stage.expected_due_at)} → ` : ""}${date(stage.starts_at)} — ${date(stage.due_at)}`).join("\n")}\n${body.reason || ""}`,
        execute: jsonOperation(`/projects/${id}/plan${dates ? "/dates" : ""}`, "PUT", body)};
    }, () => detail(id));
  }
  async function upload(projectId, stageId) {
    operationForm("Загрузка материала", () => materials(projectId, stageId), form => {
      form.append(message("TXT (UTF-8), PDF или DOCX; максимум 20 МиБ. При неизвестном результате проверьте историю перед повторением."));
      return field(form, "Файл материала", "file", "file", {required: "", accept: ".txt,.pdf,.docx"});
    }, input => {
      const file = input.files[0];
      if (!file || file.size === 0 || file.size > 20 * 1024 * 1024 || !/\.(txt|pdf|docx)$/i.test(file.name)) {
        throw new Error("Выберите непустой TXT/PDF/DOCX размером до 20 МиБ.");
      }
      const body = new FormData(); body.append("file", file);
      return {summary: `Загрузить ${file.name}, ${file.size} байт в этап № ${stageId}`,
        execute: async () => { const result = await api(`/stages/${stageId}/materials`, {method: "POST", body}); input.value = ""; return result; }};
    }, () => materials(projectId, stageId), result => `Версия ${result.version_no} подтверждена. Проверьте историю и состояние проекта.`);
  }
  async function review(projectId, stageId, material) {
    operationForm("Проверка материала", () => materials(projectId, stageId), form => {
      form.append(message(`Версия ${material.version_no}: ${material.original_filename}, ${date(material.uploaded_at)}`));
      const feedback = node("div"); form.append(button("Скачать для проверки", () => download(material, feedback)), feedback);
      const label = node("label", "Решение");
      const decision = node("select", undefined, {"aria-label": "Решение"});
      decision.append(node("option", "Вернуть на доработку", {value: "revision"}), node("option", "Принять", {value: "accepted"}));
      label.append(decision); form.append(label);
      const remarks = field(form, "Замечания", "remarks", "text", {maxlength: "10000"});
      return {decision, remarks};
    }, inputs => {
      const body = {decision: inputs.decision.value, remarks: inputs.remarks.value.trim()};
      if (body.decision === "revision" && !body.remarks) throw new Error("Для доработки обязательны замечания.");
      return {summary: `${body.decision === "accepted" ? "Принять" : "Вернуть"} версию ${material.version_no}:\n${body.remarks}`,
        execute: jsonOperation(`/materials/${material.id}/review`, "POST", body)};
    }, () => materials(projectId, stageId), result => result.next_stage_id ?
      `Проверка подтверждена. Открыт следующий этап № ${result.next_stage_id}.` : "Проверка подтверждена. Проверьте состояние проекта.");
  }
  function roleAdministrator() { return allowed("roles.manage") && me.roles.includes("admin"); }
  const editableRoles = new Set(["student", "supervisor", "teacher", "department", "admin"]);
  const accountCheckPageSize = 100;
  const accountCheckMaxPages = 5;
  function catalogContext(q, offset) {
    return {view: "catalog", q, offset};
  }
  function detailContext(id, catalog) {
    return {view: "detail", id, catalog};
  }
  function returnToAccountContext(context) {
    return open(() => showAccountContext(context));
  }
  async function showAccountContext(context) {
    if (!context || context.view === "catalog") {
      const state = context || catalogContext("", 0);
      return accounts(state.offset, null, state.q);
    }
    if (context.view === "detail") return accountDetail(context.id, context.catalog);
    if (context.view === "roles") return roleCatalog(context.offset);
    return accounts();
  }
  async function accountCatalogPage(q, offset, limit = 10) {
    const query = new URLSearchParams({limit, offset, q, paginated: true});
    return api(`/admin/users?${query}`);
  }
  async function accountMatchesFilter(id, q) {
    if (!q.trim()) return true;
    for (let page = 0; page < accountCheckMaxPages; page += 1) {
      const offset = page * accountCheckPageSize;
      const result = await accountCatalogPage(q, offset, accountCheckPageSize);
      if (result.items.some(user => String(user.id) === String(id))) return true;
      if (offset + result.items.length >= result.total || !result.items.length) return false;
    }
    return null;
  }
  async function refreshAccountMutation(id, catalog) {
    await refreshIdentity();
    const user = await api(`/admin/users/${id}`);
    const matches = await accountMatchesFilter(id, catalog.q);
    await accounts(catalog.offset, user, catalog.q, matches);
  }
  async function refreshIdentity() {
    me = await api("/auth/me");
    nav();
  }
  async function effectivePermissions() {
    await refreshIdentity();
    content.replaceChildren(node("h2", "Мои эффективные права"),
      message(`Текущая сессия: ${me.email}. Права получены из /auth/me, не вычислены по ролям интерфейсом.`));
    table(content, "Роли текущей сессии", ["Код"], me.roles.map(code => [code]));
    table(content, "Эффективные разрешения", ["Код"], me.permissions.map(code => [code]));
    content.append(message("Эффективные права других учетных записей доступны уполномоченному администратору в деталях. Роль admin не означает все права."));
  }
  async function accounts(offset = 0, result = null, q = "", matchesFilter = null) {
    let currentOffset = offset;
    let data = await accountCatalogPage(q, currentOffset);
    if (currentOffset > 0 && currentOffset >= data.total) {
      currentOffset = data.total === 0 ? 0 : Math.floor((data.total - 1) / data.limit) * data.limit;
      data = await accountCatalogPage(q, currentOffset);
    }
    const rows = data.items;
    const catalog = catalogContext(q, currentOffset);
    content.replaceChildren(node("h2", "Учетные записи"),
      message(`Всего: ${data.total}. Страница: ${data.page}. Размер: ${data.limit}.`));
    const search = node("form");
    const query = field(search, "Поиск по email, имени или фамилии", "q", "search", {maxlength: "254"});
    query.value = q;
    search.append(node("button", "Найти учетные записи", {type: "submit"}));
    search.addEventListener("submit", event => { event.preventDefault(); open(() => accounts(0, null, query.value)); });
    content.append(search);
    table(content, "Учетные записи", ["ID", "Email", "Имя", "Фамилия", "Отчество", "Группа", "Состояние", "Роли", "Действия"],
      rows.map(row => {
        const actions = node("div", undefined, {class: "actions"});
        actions.append(button(`Открыть учетную запись ${row.id}`, () => open(() => accountDetail(row.id, catalog))));
        actions.append(button(`Изменить данные ${row.id}`, () => accountEditor(row, catalog)));
        const selfAdmin = me.id === row.id && me.roles.includes("admin");
        actions.append(button(`${row.is_active ? "Заблокировать" : "Реактивировать"} ${row.id}`,
          () => accountStatus(row, catalog), selfAdmin));
        if (selfAdmin) actions.append(message("Нельзя заблокировать собственную учетную запись администратора."));
        if (roleAdministrator()) actions.append(button(`Роли учетной записи ${row.id}`, () => open(() => accountRoles(row.id, catalog))),
          button(`Эффективные права ${row.id}`, () => open(() => accountDetail(row.id, catalog))));
        return [row.id, row.email, row.first_name, row.last_name, row.middle_name, row.study_group,
          row.is_active ? "Активна" : "Заблокирована", row.roles.join(", "), actions];
      }));
    pager(content, currentOffset, data.limit, currentOffset + rows.length < data.total, next => accounts(next, null, q));
    const pageForm = node("form");
    const pageOffset = field(pageForm, "Смещение страницы", "offset", "number",
      {min: "0", max: String(Number.MAX_SAFE_INTEGER), required: ""});
    pageOffset.value = currentOffset;
    pageForm.append(node("button", "Открыть смещение", {type: "submit"}));
    pageForm.addEventListener("submit", event => { event.preventDefault(); open(() => accounts(Number(pageOffset.value), null, q)); });
    content.append(pageForm);
    if (allowed("roles.manage")) content.append(button("Создать учетную запись", () => open(() => accountCreate(catalog))));
    if (result) {
      const detail = await renderAccountDetail(result.id, false, result, catalog);
      content.prepend(detail);
      focus(detail);
      if (matchesFilter === false) {
        content.prepend(message("Данные обновлены, но учетная запись больше не соответствует текущему поиску. Фильтр сохранен; детали доступны ниже."));
        content.prepend(button("Очистить поиск", () => open(async () => {
          const user = await api(`/admin/users/${result.id}`);
          await accounts(0, user, "");
        })));
      } else if (matchesFilter === null) {
        content.prepend(message("Данные обновлены. Не удалось проверить весь результат поиска; фильтр сохранен, детали доступны ниже."));
      }
    }
  }
  async function renderAccountDetail(id, actions = true, freshUser = null, catalog = catalogContext("", 0)) {
    const user = freshUser || await api(`/admin/users/${id}`);
    const effective = roleAdministrator() ? await api(`/admin/users/${id}/effective-permissions`) : null;
    const region = node("section", undefined, {"aria-label": `Учетная запись ${id}`});
    region.append(node("h3", `Учетная запись № ${id}`));
    const values = node("dl");
    for (const [label, value] of [["ID", user.id], ["Email", user.email], ["Имя", user.first_name],
      ["Фамилия", user.last_name], ["Отчество", user.middle_name], ["Группа", user.study_group],
      ["Состояние", user.is_active ? "Активна" : "Заблокирована"]]) {
      values.append(node("dt", label), node("dd", value ?? "—"));
    }
    region.append(values);
    table(region, "Роли учетной записи", ["Код"], user.roles.map(code => [code]));
    if (effective) table(region, "Эффективные права учетной записи", ["Код"], effective.permission_codes.map(code => [code]));
    else region.append(message("Для эффективных прав нужны users.manage, roles.manage и активная роль admin."));
    if (actions) {
      const detail = detailContext(id, catalog);
      region.append(button(`Изменить данные ${id}`, () => accountEditor(user, detail)),
        button(`${user.is_active ? "Заблокировать" : "Реактивировать"} ${id}`,
          () => accountStatus(user, detail), me.id === user.id && me.roles.includes("admin")));
      if (roleAdministrator()) {
        region.append(button(`Роли учетной записи ${id}`, () => open(() => accountRoles(id, detail))));
        user.roles.filter(code => editableRoles.has(code)).forEach(code => region.append(
          button(`Разрешения роли ${code}`, () => open(() => rolePermissions(code, detail)))));
      }
      region.append(button("Обновить детали и эффективные права", () => open(() => accountDetail(id, catalog))));
    } else region.append(button(`Открыть учетную запись ${id}`, () => open(() => accountDetail(id, catalog))));
    return region;
  }
  async function accountDetail(id, catalog = catalogContext("", 0)) {
    await refreshIdentity();
    const region = await renderAccountDetail(id, true, null, catalog);
    content.replaceChildren(node("h2", "Детали учетной записи"), region,
      message("Данные перечитываются явно; обновление других сессий в реальном времени не обещается."),
      button("К учетным записям", () => returnToAccountContext(catalog)));
    focus(region);
  }
  function accountEditor(user, context) {
    const catalog = context.view === "catalog" ? context : context.catalog;
    operationForm(`Изменение учетной записи № ${user.id}`, () => showAccountContext(context), form => {
      form.append(message("Отправляются только измененные поля. Пустые отчество/группа очищаются через null; email, имя и фамилия обязательны. Пароль и роли здесь не изменяются."));
      const inputs = {};
      for (const [key, label, max] of [["email", "Email учетной записи", 254], ["first_name", "Имя", 100],
        ["last_name", "Фамилия", 100], ["middle_name", "Отчество", 100], ["study_group", "Учебная группа", 50]]) {
        inputs[key] = field(form, label, key, key === "email" ? "email" : "text",
          {...(["email", "first_name", "last_name"].includes(key) ? {required: ""} : {}), maxlength: max});
        inputs[key].value = user[key] || "";
      }
      return inputs;
    }, inputs => {
      const body = {};
      for (const [key, input] of Object.entries(inputs)) {
        let value = input.value;
        if (key === "email") value = value.trim().toLowerCase();
        if (["first_name", "last_name"].includes(key)) {
          value = value.trim();
          if (!value) throw new Error("Имя и фамилия не должны быть пустыми.");
        }
        if (["middle_name", "study_group"].includes(key) && !value) value = null;
        if (value !== user[key]) body[key] = value;
      }
      if (!Object.keys(body).length) throw new Error("Нет измененных полей для отправки.");
      return {summary: `Изменить учетную запись № ${user.id}\n${Object.entries(body).map(([key, value]) =>
        `${key}: ${user[key] ?? "—"} → ${value ?? "null"}`).join("\n")}`,
        execute: jsonOperation(`/admin/users/${user.id}`, "PATCH", body)};
    }, () => refreshAccountMutation(user.id, catalog));
  }
  function accountStatus(user, context) {
    const catalog = context.view === "catalog" ? context : context.catalog;
    const active = !user.is_active;
    operationForm(`${active ? "Реактивация" : "Блокировка"} учетной записи`, () => showAccountContext(context), form => {
      form.append(message(`${user.id}: ${user.email}`),
        message("Блокировка запрещает вход и доступ с ранее выданным JWT, но не удаляет данные/назначения. После реактивации неистекший JWT снова может работать; постоянного отзыва нет. Последний активный администратор защищен сервером."));
    }, () => ({summary: `${active ? "Реактивировать" : "Заблокировать"} № ${user.id}: ${user.email}`,
      execute: jsonOperation(`/admin/users/${user.id}/status`, "PATCH", {is_active: active})}),
    () => refreshAccountMutation(user.id, catalog));
  }
  async function accountCreate(context) {
    const accountList = context || catalogContext("", 0);
    const roles = await api("/admin/roles");
    operationForm("Создание учетной записи", () => showAccountContext(accountList), form => {
      form.append(message("Начальный пароль: 12–1024 символа, только для создания. Он не показывается в резюме и очищается до подтверждения. При отмене/ошибке введите его заново. Смена пароля недоступна."));
      const inputs = {};
      for (const [key, label, max] of [["email", "Email учетной записи", 254], ["first_name", "Имя", 100],
        ["last_name", "Фамилия", 100], ["middle_name", "Отчество", 100], ["study_group", "Учебная группа", 50]]) {
        inputs[key] = field(form, label, key, key === "email" ? "email" : "text",
          {...(["email", "first_name", "last_name"].includes(key) ? {required: ""} : {}), maxlength: max});
      }
      inputs.password = field(form, "Начальный пароль", "password", "password",
        {required: "", minlength: "12", maxlength: "1024", autocomplete: "new-password"});
      const selections = node("fieldset");
      selections.append(node("legend", "Начальные роли"));
      const selectionsByRole = roles.map(role => ({code: role.code,
        input: field(selections, `${role.name} (${role.code})`, role.code, "checkbox")}));
      form.append(selections);
      return {inputs, roles: selectionsByRole};
    }, controls => {
      const body = {};
      for (const [key, input] of Object.entries(controls.inputs)) body[key] = input.value;
      body.email = body.email.trim().toLowerCase();
      body.first_name = body.first_name.trim(); body.last_name = body.last_name.trim();
      body.role_codes = controls.roles.filter(role => role.input.checked).map(role => role.code);
      if (!body.first_name || !body.last_name) throw new Error("Имя и фамилия не должны быть пустыми.");
      if (!body.role_codes.length || body.role_codes.length > 5) throw new Error("Выберите от 1 до 5 ролей.");
      controls.inputs.password.value = "";
      const dispose = () => { body.password = ""; };
      return {summary: `Создать ${body.email}: ${body.first_name} ${body.last_name}\nРоли: ${body.role_codes.join(", ")}\nПароль скрыт.`,
        dispose, execute: async () => {
          try { return await jsonOperation("/admin/users", "POST", body)(); }
          finally { dispose(); }
        }};
    }, async result => {
      if (!result || result.id === undefined) {
        await accounts(accountList.offset, null, accountList.q);
        return;
      }
      const user = await api(`/admin/users/${result.id}`);
      const matches = await accountMatchesFilter(user.id, accountList.q);
      await accounts(accountList.offset, user, accountList.q, matches);
    });
  }
  async function roleCatalog(offset = 0) {
    const catalog = await api("/admin/roles");
    const rows = catalog.slice(offset, offset + 10);
    content.replaceChildren(node("h2", "Роли"),
      message("Каталог API возвращается целиком; пагинация ниже только визуальная. Роли не исключают друг друга и не означают фиксированные эффективные права."));
    table(content, "Каталог ролей", ["Код", "Название", "Действие"], rows.map(role => [
      role.code, role.name, roleAdministrator() && editableRoles.has(role.code) ?
        button(`Разрешения роли ${role.code}`, () => open(() => rolePermissions(role.code))) : "—"
    ]));
    pager(content, offset, 10, offset + 10 < catalog.length, next => roleCatalog(next));
    if (roleAdministrator()) {
      const form = node("form");
      const id = idField(form, "Учетная запись ID", "user_id");
      form.append(node("button", "Открыть роли учетной записи", {type: "submit"}));
      form.addEventListener("submit", event => {
        event.preventDefault();
        open(() => accountRoles(id.value, {view: "roles", offset}));
      });
      content.append(form);
    } else content.append(message("Изменение ролей и просмотр разрешений требуют не только roles.manage, но и активной роли admin."));
  }
  async function accountRoles(id, context = {view: "roles", offset: 0}) {
    const current = await api(`/admin/users/${id}/roles`);
    const catalog = await api("/admin/roles");
    content.replaceChildren(node("h2", `Роли учетной записи № ${id}`),
      message("API заменяет весь набор ролей. Подтверждение показывает исходный и новый набор. Нет expected/версии: перечитайте данные перед изменением при конкуренции."),
      message("Роли и эффективные права перечитываются сервером, не вычисляются в UI."));
    const self = String(me.id) === String(id);
    table(content, "Назначенные роли", ["Код", "Действие"], current.role_codes.map(code => [code,
      button(`Снять роль ${code}`, () => roleChange(id, current.role_codes, code, false, context), self || current.role_codes.length <= 1)]));
    if (self) content.append(message("Изменять собственные роли запрещено сервером."));
    const possible = catalog.filter(role => editableRoles.has(role.code) && !current.role_codes.includes(role.code));
    possible.forEach(role => content.append(button(`Назначить роль ${role.code}`,
      () => roleChange(id, current.role_codes, role.code, true, context), self || current.role_codes.length >= 5)));
    content.append(message("Нельзя снимать назначенную проектам учебную роль или роль последнего активного администратора; сервер проверяет эти условия."),
      button("Обновить роли", () => open(() => accountRoles(id, context))),
      button(context.view === "detail" ? "К деталям учетной записи" :
        context.view === "roles" ? "К ролям" : "К учетным записям",
        () => returnToAccountContext(context)));
    if (allowed("users.manage")) {
      const catalog = context.view === "detail" ? context.catalog : context.view === "catalog" ? context : catalogContext("", 0);
      content.append(await renderAccountDetail(id, false, null, catalog));
    }
  }
  function roleChange(id, before, code, add, context) {
    const after = add ? [...before, code].sort() : before.filter(item => item !== code);
    operationForm("Изменение ролей", () => accountRoles(id, context), form => form.append(
      message("Роли могут изменить доступ к системе. Эффективные права определяются сервером, не фиксированным списком UI.")),
    () => ({summary: `${add ? "Назначить" : "Снять"} роль ${code} учетной записи № ${id}\nДо: ${before.join(", ")}\nПосле: ${after.join(", ")}`,
      execute: jsonOperation(`/admin/users/${id}/roles`, "PUT", {role_codes: after})}),
    async () => { await refreshIdentity(); await accountRoles(id, context); });
  }
  async function permissionCatalog(offset = 0) {
    const catalog = await api("/admin/permissions");
    content.replaceChildren(node("h2", "Разрешения"),
      message("Каталог API возвращается целиком; пагинация только визуальная. Управление требует roles.manage и активной роли admin."));
    table(content, "Каталог разрешений", ["Код", "Название"], catalog.slice(offset, offset + 10).map(item => [item.code, item.name]));
    pager(content, offset, 10, offset + 10 < catalog.length, next => permissionCatalog(next));
    content.append(button("К ролям", () => open(() => roleCatalog())));
  }
  async function rolePermissions(code, accountContext = null) {
    const current = await api(`/admin/roles/${encodeURIComponent(code)}/permissions`);
    const catalog = await api("/admin/permissions");
    await refreshIdentity();
    content.replaceChildren(node("h2", `Разрешения роли ${code}`),
      message("Изменяется весь набор. Политику допустимых разрешений проверяет сервер: admin не получает глобальные учебные права и сохраняет users.manage/roles.manage."),
      message("Изменение касается всех носителей роли. Выбранная учетная запись и текущая сессия перечитываются явно. Нет expected/версии набора."));
    table(content, "Разрешения роли", ["Код", "Действие"], current.permission_codes.map(permission => [permission,
      button(`Снять разрешение ${permission}`, () => permissionChange(code, current.permission_codes, permission, false, accountContext))]));
    catalog.filter(item => !current.permission_codes.includes(item.code)).forEach(item => content.append(
      button(`Назначить разрешение ${item.code}`, () => permissionChange(code, current.permission_codes, item.code, true, accountContext))));
    content.append(button("Обновить разрешения роли", () => open(() => rolePermissions(code, accountContext))),
      button("Мои эффективные права", () => open(() => effectivePermissions())));
    if (accountContext && accountContext.view === "detail") {
      content.append(button("К деталям учетной записи", () => returnToAccountContext(accountContext)));
      if (allowed("users.manage")) {
        content.append(await renderAccountDetail(accountContext.id, false, null, accountContext.catalog));
      }
    }
  }
  function permissionChange(code, before, permission, add, accountContext) {
    const after = add ? [...before, permission].sort() : before.filter(item => item !== permission);
    operationForm("Изменение разрешений роли", () => rolePermissions(code, accountContext), form => form.append(
      message(`Все учетные записи с ролью ${code} затронуты. Сервер ограничивает разрешения политикой роли.`)),
    () => ({summary: `${add ? "Назначить" : "Снять"} разрешение ${permission} роли ${code}\nДо: ${before.join(", ") || "—"}\nПосле: ${after.join(", ") || "—"}`,
      execute: jsonOperation(`/admin/roles/${encodeURIComponent(code)}/permissions`, "PUT", {permission_codes: after})}),
    async () => { await refreshIdentity(); await rolePermissions(code, accountContext); });
  }
  login();
})();
