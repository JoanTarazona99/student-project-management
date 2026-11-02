# Lab № 6 — предложение версий, без создания тегов

## Статус предложения

Теги ниже **не созданы и не опубликованы**. После `git fetch origin`
`git tag --list` и `git ls-remote --tags origin` не показали ни одного тега;
следовательно, `lab6-v1.0`, `lab6-v2.0`, `lab6-v3.0` доступны на момент проверки.
Перед будущим созданием это необходимо проверить повторно.
Если имя занято, нельзя двигать/перезаписывать его; нужно остановиться
и согласовать имя/действия.

Предлагаются **annotated tags на исторических merge commits** трех итераций,
не на текущее main и не на heads до интеграции.
Это учебные обозначения итераций, а не утверждение о соблюдении SemVer
для всей системы или несовместимых API-изменениях.
Создание выполнялось бы сейчас после отдельного разрешения, с реальной текущей
датой tagger. Нельзя менять даты для имитации прежней публикации.
Тег не равен развертыванию. GitHub releases в этом задании не создаются.

## Точные цели

| Тег | Ветка итерации / PR | SHA merge, цель annotated tag | Содержание |
|---|---|---|---|
| `lab6-v1.0` | `maintenance/v1.0`, [#13](https://github.com/JoanTarazona99/student-project-management/pull/13) | `42ffbf5d453d57d3b05afacad9cd2cc1f57bd350` | Возврат к фильтру/offset каталога, свежие GET после переходов/мутаций, ограниченная проверка membership, очистка при logout |
| `lab6-v2.0` | `maintenance/v2.0`, [#14](https://github.com/JoanTarazona99/student-project-management/pull/14) | `4ebc1277819996a76f5e66e2b2aaa5723eee3e82` | Краткая русская помощь, ARIA/focus и базовая клавиатурная доступность, исправление footer, отдельный возврат каталога при 360 px |
| `lab6-v3.0` | `maintenance/v3.0`, [#15](https://github.com/JoanTarazona99/student-project-management/pull/15) | `cfb1f0423e8959a2d232f427f0aa355811947eec` | Предупреждение об измененном черновике/подтверждении, различение отправки/отказа/успеха/неопределенного результата, очистка logout/401 |

Merge PR #16 `24946dd8873615ab01a86bde9eed839f0307b98f` — дополнительное
доказательство симуляции команды и подготовленного конфликта.
Это **не четвертая итерация** и **не ретроактивная часть v3.0**.
Новый документационный PR также не включается в цели этих тегов.

## Исторические тесты и CI: какой SHA действительно проверен

| Итерация | Head SHA, указанный CI как `headSha` | Исторические локальные результаты | Историческая CI |
|---|---|---|---|
| 1 | `0ed2111e23e85c3e6aaf6e4f55d6392d4a299a1b` | 14 unit; 259 normal и 259 reverse; 10 навигационных | [run 41382436485](https://github.com/JoanTarazona99/student-project-management/actions/runs/41382436485): success, 14 unit и 259 pytest |
| 2 | `387d661993e98203e861de52e1978fa94eba01f1` | 14 unit; 266 normal и 266 reverse; 17 помощь/навигация | [run 41391535030](https://github.com/JoanTarazona99/student-project-management/actions/runs/41391535030): success, 14 unit и 266 pytest |
| 3 | `ee7816bed74fe73dde1f0b71b0c4e0d107ccebad` | 14 unit; 278 normal и 278 reverse; 17 фокусных | [run 41398696439](https://github.com/JoanTarazona99/student-project-management/actions/runs/41398696439): success, 14 unit и 278 pytest |

Локальные данные взяты из сохраненных
[iteration-1.md](iteration-1.md), [iteration-2.md](iteration-2.md),
[iteration-3.md](iteration-3.md), без переписывания их истории.
В этом задании тесты локально **не перезапускались**.
Состояния CI/точные heads повторно прочитаны через `gh run list`;
из logs извлечены реальные counts, см.
[historical-ci.txt](evidence/historical-ci.txt).
Каждый pytest run имеет одно предупреждение Starlette/TestClient deprecation.

`headSha` workflow PR не является автоматически SHA merge в main.
Он идентифицирует head PR, связанный с запуском. По реальным checkout logs
`git log -1 --format=%H` runner исполнял следующие **synthetic merge** commits:

| PR | Реальный checkout SHA CI |
|---|---|
| #13 | `f2d31fe9579c064397ec65e548abb7f5a4fc8a77` |
| #14 | `f8302c57d762a4682c76097cd7f0a53a83868da1` |
| #15 | `83d73564f0e21f6b9cc85b6879a2d5a6f837245b` |
| #16 (дополнительный) | `2be9db7f2c17a5c06e80fe2952ec0c61ff2b080c` |

Источник — сохраненные checkout-фрагменты в historical-ci.txt, а не предположение.
Эти synthetic commits не добавляются в академический граф и ranking.
Их наличие в локальной object database не требуется и не заявляется:
доказательство checkout — исторический log runner.
Локальный запуск, head PR, synthetic checkout CI и историческая цель тега
разделены явно. Точные parents настоящих PR merges сохранены в
[merge-log.txt](evidence/merge-log.txt).
Нельзя объявлять новый запуск на tag/merge только по success исторического PR.

Для справки, дополнительная симуляция проверена исторической CI
[run 41509171794](https://github.com/JoanTarazona99/student-project-management/actions/runs/41509171794)
на `headSha=66879b0ab34e2bf1d705d3bc806e319446303d1f`:
14 unit, 282 pytest, success. Она не изменяет counts версий 1–3.

## Точный предлагаемый текст аннотаций

В `-m` будет передано ровно по одной строке:

**lab6-v1.0**

```text
Lab № 6, итерация 1: возврат к контексту каталога учетных записей. Исторический merge PR #13: 42ffbf5d453d57d3b05afacad9cd2cc1f57bd350. Тег создан после итерации; не является развертыванием.
```

**lab6-v2.0**

```text
Lab № 6, итерация 2: контекстная помощь на русском и доступность базового уровня. Исторический merge PR #14: 4ebc1277819996a76f5e66e2b2aaa5723eee3e82. Тег создан после итерации; не является развертыванием.
```

**lab6-v3.0**

```text
Lab № 6, итерация 3: предупреждение о несохраненных изменениях и состояния отправки. Исторический merge PR #15: cfb1f0423e8959a2d232f427f0aa355811947eec. Дополнительная симуляция PR #16 не входит в эту версию. Тег создан после итерации; не является развертыванием.
```

Слово «создан» — часть будущей аннотации после разрешенного выполнения,
не заявление о текущем наличии тегов.

## Предлагаемые команды — НЕ выполнены

После отдельного разрешения пользователя, сначала чтение состояния:

```powershell
git fetch origin
git tag --list 'lab6-v*'
git ls-remote --tags origin 'lab6-v*'
git show -s --format='%H %P %s' 42ffbf5d453d57d3b05afacad9cd2cc1f57bd350 4ebc1277819996a76f5e66e2b2aaa5723eee3e82 cfb1f0423e8959a2d232f427f0aa355811947eec
```

Только если все три имени по-прежнему свободны и цели подтверждены:

```powershell
git tag -a lab6-v1.0 42ffbf5d453d57d3b05afacad9cd2cc1f57bd350 -m 'Lab № 6, итерация 1: возврат к контексту каталога учетных записей. Исторический merge PR #13: 42ffbf5d453d57d3b05afacad9cd2cc1f57bd350. Тег создан после итерации; не является развертыванием.'
git tag -a lab6-v2.0 4ebc1277819996a76f5e66e2b2aaa5723eee3e82 -m 'Lab № 6, итерация 2: контекстная помощь на русском и доступность базового уровня. Исторический merge PR #14: 4ebc1277819996a76f5e66e2b2aaa5723eee3e82. Тег создан после итерации; не является развертыванием.'
git tag -a lab6-v3.0 cfb1f0423e8959a2d232f427f0aa355811947eec -m 'Lab № 6, итерация 3: предупреждение о несохраненных изменениях и состояния отправки. Исторический merge PR #15: cfb1f0423e8959a2d232f427f0aa355811947eec. Дополнительная симуляция PR #16 не входит в эту версию. Тег создан после итерации; не является развертыванием.'
git show --no-patch lab6-v1.0 lab6-v2.0 lab6-v3.0
git push origin refs/tags/lab6-v1.0 refs/tags/lab6-v2.0 refs/tags/lab6-v3.0
git ls-remote --tags origin 'lab6-v*'
```

Не использовать `-f`, `--force`, переписывание/удаление тегов или подмену
`GIT_COMMITTER_DATE`. При отказе любой команды остановиться и сообщить
результат, не обходить права/политику. Команды не являются инструкцией
делать merge, release, deploy или запускать scheduler.
