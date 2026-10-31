import argparse
import sys

from app.deadlines import DeadlineResult, check_deadlines, utc_timestamp


def warning_hours(value):
    try:
        hours = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("warning-hours: нужен целый час от 1 до 168.") from None
    if not 1 <= hours <= 168:
        raise argparse.ArgumentTypeError("warning-hours: допустимо от 1 до 168.")
    return hours


def parser():
    result = argparse.ArgumentParser(description="Проверка сроков учебных проектов.")
    mode = result.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Симуляция без записи (по умолчанию).")
    mode.add_argument("--apply", action="store_true", help="Сохранить новые внутренние уведомления.")
    result.add_argument("--warning-hours", type=warning_hours, default=24, help="Окно предупреждения: 1–168 часов.")
    return result


def print_result(result: DeadlineResult):
    print("Применение" if result.apply else "Симуляция: записи не выполнялись")
    print(f"Момент UTC: {utc_timestamp(result.reference_time)}")
    print(f"Проектов рассмотрено: {result.projects_examined}")
    print(f"Этапов рассмотрено в успешных транзакциях: {result.counts.stages_examined}")
    label = "Вставлено" if result.apply else "Новых кандидатов"
    print(f"{label}, срок приближается: {result.counts.upcoming}")
    print(f"{label}, срок истек: {result.counts.overdue}")
    print(f"Уже существующих уведомлений: {result.counts.existing}")
    print(f"Неактивных получателей пропущено (на этап): {result.counts.inactive_recipients}")
    action = "Подтверждено транзакций проектов" if result.apply else "Успешных снимков проектов"
    print(f"{action}: {len(result.successful_projects)}")
    print(f"Ошибок: {len(result.errors)}")
    for error in result.errors:
        target = f"проект № {error.project_id}" if error.project_id is not None else "обход проектов"
        outcome = "результат подтверждения неизвестен" if error.outcome == "unknown" else "транзакция не подтверждена"
        print(f"Ошибка БД: {target}; код {error.code}; {outcome}.", file=sys.stderr)
    if result.errors and result.apply:
        print(f"Подтвержденные проекты: {result.successful_projects}", file=sys.stderr)


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        from app.database import engine
    except (RuntimeError, ValueError):
        print("Ошибка конфигурации БД. Проверьте обязательные параметры .env.", file=sys.stderr)
        return 1
    try:
        result = check_deadlines(engine, apply=args.apply, warning_hours=args.warning_hours)
        print_result(result)
        return 1 if result.errors else 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    sys.exit(main())
