# Lab № 6 — учебная симуляция команды и конфликта

## Цель и границы

Работа двух разработчиков смоделирована одним исполнителем
с использованием двух учебных идентификаторов Git.
Конфликт слияния подготовлен специально для демонстрации
конфигурационного управления.

По сообщению пользователя, преподаватель принимает такую симуляцию.
Письменное подтверждение преподавателя не предоставлялось и не выдумывается.
Идентификаторы ниже не представляют двух людей или две учетные записи GitHub.
Copilot участвовал как ИИ-помощник в анализе, изменении кода, запуске команд,
подготовке тестов и документации, а не как второй разработчик-человек.

Изменение только уточняет существующую подсказку каталога. Backend, API,
SQL-схема, permissions, зависимости, workflow, мутации и правила поиска не
менялись. Это небольшое совершенствующее обслуживание существующей помощи.
Строка `app/users.py` с построением `pattern` экранирует `\`, `%` и `_`;
предикат использует `ILIKE ... ESCAPE '\'` для email, имени и фамилии.
Следовательно, обе новые фразы описывают действующее поведение.

## Проверенное основание и история

До изменений worktree был чистым, checkout — `maintenance/v3.0`,
HEAD — `ee7816bed74fe73dde1f0b71b0c4e0d107ccebad`.
После `git fetch origin` PR #15 проверен через
`gh pr view 15 --json state,mergedAt,mergeCommit,headRefOid,url`:
состояние `MERGED`, merge — `cfb1f0423e8959a2d232f427f0aa355811947eec`.
Единственный новый commit относительно checkout — этот merge.

Общий **BASE**: `cfb1f0423e8959a2d232f427f0aa355811947eec`.
Все три имени веток отсутствовали локально и в полученных remote refs.
Ветки A и B созданы независимо командой `git switch -c <ветка> <BASE>`;
B не создавалась от A. Ветка интеграции также создана непосредственно от BASE.

| Ветка | Commit | Вклад |
|---|---|---|
| `lab6/team-sim-a` | `a82816f5c238ecc916f82f043c11874c4a718b8e` | Уточнение регистра и отдельный Chromium-тест |
| `lab6/team-sim-b` | `bedb7ec93614547f644fcc9585d60ab80b4b07ad` | Уточнение буквальных символов и отдельный параметризованный Chromium-тест |
| `lab6/team-sim-integration` | `fbaa40aa970027d218eaf41202b6c920f3dead45` | Merge A с сохранением commit |
| `lab6/team-sim-integration` | `59786e81ccc55b978aaae3bac9b91ccd03c855d7` | Merge B с разрешением реального конфликта |

Родитель каждого учебного commit A/B — BASE. Родители merge A:

```text
cfb1f0423e8959a2d232f427f0aa355811947eec a82816f5c238ecc916f82f043c11874c4a718b8e
```

Родители merge B (разрешение конфликта):

```text
fbaa40aa970027d218eaf41202b6c920f3dead45 bedb7ec93614547f644fcc9585d60ab80b4b07ad
```

Документация добавляется отдельным последующим commit, не amend/squash/rebase.
Его SHA и окончательный SHA проверяются после commit и указываются в PR:
включить SHA документационного commit в его собственное содержимое невозможно.
Для проверки топологии:

```powershell
git log --graph --oneline cfb1f0423e8959a2d232f427f0aa355811947eec..lab6/team-sim-integration
git show -s --format='%H %P' lab6/team-sim-a lab6/team-sim-b fbaa40a 59786e8
```

## Учебные идентификаторы без изменения конфигурации

| Вклад | Автор и committer |
|---|---|
| A | `Lab6 Simulated Developer A <lab6-dev-a@example.invalid>` |
| B | `Lab6 Simulated Developer B <lab6-dev-b@example.invalid>` |

Для каждого commit применялся следующий PowerShell-порядок; значения `$label`,
`$address` и `$subject` соответствовали A либо B из таблицы. Переменные дат
не задавались; даты не изменялись задним числом.

```powershell
$names = @('GIT_AUTHOR_NAME','GIT_AUTHOR_EMAIL',
           'GIT_COMMITTER_NAME','GIT_COMMITTER_EMAIL')
$saved = @{}
foreach ($name in $names) {
    $saved[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
}
try {
    $env:GIT_AUTHOR_NAME = $label
    $env:GIT_AUTHOR_EMAIL = $address
    $env:GIT_COMMITTER_NAME = $label
    $env:GIT_COMMITTER_EMAIL = $address
    git commit -m $subject `
      -m 'Educational simulation performed by one executor; this identity is not a real person or GitHub account.' `
      -m 'Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>'
    $code = $LASTEXITCODE
} finally {
    foreach ($name in $names) {
        [Environment]::SetEnvironmentVariable($name, $saved[$name], 'Process')
    }
    foreach ($name in $names) {
        if ([Environment]::GetEnvironmentVariable($name, 'Process') -cne $saved[$name]) {
            throw "Environment restoration failed: $name"
        }
    }
    Write-Output 'All four Git identity environment variables restored: True'
}
if ($code -ne 0) { throw "Commit failed: $code" }
```

Оба запуска вывели `All four Git identity environment variables restored: True`.
`git show -s` подтвердил и автора, и committer согласно таблице.
Merge-коммиты использовали обычную существующую идентичность. Команды
`git config` для изменения настроек не выполнялись; credentials, remotes,
GitHub permissions и инструкции агента не менялись.

## Реальный подготовленный конфликт

Первая интеграция:

```powershell
git merge --no-ff lab6/team-sim-a -m 'Merge simulated contribution A for Lab 6' -m 'Prepared educational exercise, not collaboration between two real people.' -m 'Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>'
```

Фактическая строка результата: `Merge made by the 'ort' strategy.`

Затем выполнено:

```powershell
git merge --no-ff lab6/team-sim-b -m 'Merge simulated contribution B and combine catalog help' -m 'Resolve the deliberately prepared educational conflict, preserving both search explanations and tests.' -m 'Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>'
```

Фактический вывод, код возврата и `git status --short`:

```text
Auto-merging app/ui/app.js
CONFLICT (content): Merge conflict in app/ui/app.js
Automatic merge failed; fix conflicts and then commit the result.
Merge B exit code: 1
UU app/ui/app.js
A  tests/test_catalog_literal_help_browser.py
```

`git diff --name-only --diff-filter=U` вывел только `app/ui/app.js`.
Код 1 был ожидаемым результатом конфликта, а не неизвестной ошибкой.
Фрагмент, действительно созданный Git (до разрешения):

> ```javascript
> <<<<<<< HEAD
>     help(search, "Ищет по email, имени и фамилии. Поиск не учитывает регистр. Примените поиск кнопкой или Enter. Возврат из детали и связанных форм загружает эту страницу заново с теми же фильтром и смещением. Пустой результат означает, что совпадений нет, а не ошибку.",
> =======
>     help(search, "Ищет по email, имени и фамилии. Символы %, _ и обратная косая черта ищутся буквально, без подстановочных знаков. Примените поиск кнопкой или Enter. Возврат из детали и связанных форм загружает эту страницу заново с теми же фильтром и смещением. Пустой результат означает, что совпадений нет, а не ошибку.",
> >>>>>>> lab6/team-sim-b
> ```

Обе стороны изменили одну существующую строку, не удаляя прежние пояснения.
Маркеры не добавлялись вручную для имитации конфликта.

## Разрешение

В итоговой строке после «Ищет по email, имени и фамилии.» последовательно
сохранены «Поиск не учитывает регистр.» и «Символы %, _ и обратная косая черта
ищутся буквально, без подстановочных знаков.». Затем без изменений следуют
предложения про кнопку/Enter, возврат со свежими данными и пустой результат.
Это совместимые свойства одного поиска, а не выбор одного вклада вместо другого.
Сохранены `[query]` для `aria-describedby`, прежние DOM/textContent helper-ы,
управление фокусом и обе тестовые реализации.

После редактирования:

```powershell
git grep -n -E '^(<<<<<<< |=======|>>>>>>> )' -- app tests
git add app\ui\app.js
git diff --cached --check
node --check app\ui\app.js
git diff --name-only --diff-filter=U
git commit -m 'Merge simulated contribution B and combine catalog help' -m 'Resolve the deliberately prepared educational conflict, preserving both search explanations and tests. One executor represents two educational identities; these are not real collaborators.' -m 'Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>'
```

Поиск маркеров завершился кодом 1 (совпадений нет), проверка JS и diff — кодом 0,
список неслитых файлов после `git add` пуст. Архивный фрагмент с маркерами в этом
отчете является доказательством, а не неразрешенным конфликтом в исходном коде.
Первоначально `git diff --cached --check` распознал архивные маркеры внутри
обычного code block как оставшийся конфликт. Фрагмент оформлен Markdown-цитатой
без изменения отображаемого текста; проверка не отключалась.
Коммит завершен обычным merge без переписывания учебных commits.

## Тесты

До интеграции на ветке A:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_catalog_case_help_browser.py -v --tb=short --postgres-backend=native --pg-bin='C:\Program Files\PostgreSQL\17\bin'
```

Результат: **1 passed**, 1 warning, 22.38 s.

До интеграции на ветке B:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_catalog_literal_help_browser.py -v --tb=short --postgres-backend=native --pg-bin='C:\Program Files\PostgreSQL\17\bin'
```

Результат: **3 passed**, 1 warning, 19.24 s.
В обоих случаях реальные API создают уникальные тестовые учетные записи.
Chromium проверяет связанный и видимый текст помощи и результат поиска через
Enter. A проверяет нижний/верхний регистр; B проверяет `%`, `_`, `\` отдельно
с похожими записями, которые не должны попасть в результат. Поиск/авторизация
не подменяются. Сравнение не требует жесткого совпадения всего параграфа.

После разрешения конфликта:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_catalog_case_help_browser.py tests\test_catalog_literal_help_browser.py tests\test_ui_contextual_help_browser.py tests\test_admin_catalog_navigation_browser.py tests\test_ui_unsaved_changes_browser.py -q --tb=short --postgres-backend=native --pg-bin='C:\Program Files\PostgreSQL\17\bin'
.\.venv\Scripts\python.exe -m scripts.test_materials_unit
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m compileall -q app tests scripts
node --check app\ui\app.js
git diff --check
```

- Общая целевая группа: **33 passed**, 1 warning, 135.78 s:
  новые 4, помощь 6, навигация 11, черновики 12.
- Unit-тесты: **14 passed**, 0 failed.
- `pip check`: `No broken requirements found`.
- Синтаксис Python/JavaScript и `git diff --check`: код 0.

Полный набор в обычном порядке:

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q --tb=short --postgres-backend=native --pg-bin='C:\Program Files\PostgreSQL\17\bin'
```

Фактический результат: **282 passed**, 1 warning, 664.53 s.
Сохранены 278 предыдущих pytest без изменения assertions; добавлены 4 случая.

Полный набор в обратном порядке:

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q --tb=short --reverse-order --postgres-backend=native --pg-bin='C:\Program Files\PostgreSQL\17\bin'
```

Фактический результат: **282 passed**, 1 warning, 678.42 s.
В обеих полных suite нет failed, skipped или xfailed.
В целевой группе и отдельных запусках A/B также нет failed/skipped/xfail.
После изменения документации повторно проверены синтаксис и diff; документация
не меняет тестируемый код merge `59786e81ccc55b978aaae3bac9b91ccd03c855d7`.

Тесты используют существующие fixtures: собственный PostgreSQL 17 со случайным
портом и тестовыми credentials/JWT, временные файлы, собственный HTTP-сервер
и Chromium. Рабочие `.env`, БД, `uploads/` и процессы пользователя не используются.
Предупреждение `StarletteDeprecationWarning` относится к устаревающему
использованию httpx в TestClient; зависимости для его устранения не менялись.
Результаты трех предшествующих итераций не переименованы в новые запуски.

## Измененные файлы и оставшиеся работы

- `app/ui/app.js`: только строка помощи поиска.
- `tests/test_catalog_case_help_browser.py`: вклад A.
- `tests/test_catalog_literal_help_browser.py`: вклад B.
- `README_web_ui_RU.md`, `README_tests_RU.md`: текущая помощь и новые проверки.
- `docs/lab6/team-simulation.md`: этот отчет и ограниченные реальные доказательства.

Проверки Chromium/ARIA не являются аудитом доступности, проверкой всех браузеров
или screen reader-ов. Симуляция не доказывает участие двух людей.
Lab № 6 не объявляется завершенным: предстоят отдельное согласование версий,
определение наиболее часто изменяемого файла по реальному интервалу commits,
его настоящий log и итоговый отчет. PR не сливается автоматически.
Теги, releases, deployments и scheduler не создаются/не запускаются.
