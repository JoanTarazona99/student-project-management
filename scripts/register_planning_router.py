from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    path = root / "app" / "main.py"
    source = path.read_text(encoding="utf-8")
    import_line = "from app.planning import router as planning_router"
    register_line = "app.include_router(planning_router)"
    import_anchor = "from app.auth import router as auth_router"
    register_anchor = "app.include_router(auth_router)"
    if import_line in source and register_line in source:
        print("Маршруты планирования уже подключены.")
        return
    if import_anchor not in source or register_anchor not in source:
        raise SystemExit("Структура main.py отличается. Файл не изменен.")
    result = source
    if import_line not in result:
        result = result.replace(import_anchor, import_anchor + "\n" + import_line, 1)
    if register_line not in result:
        result = result.replace(register_anchor, register_anchor + "\n" + register_line, 1)
    backup = root / "backups" / "main_before_planning.py"
    backup.parent.mkdir(exist_ok=True)
    if not backup.exists():
        backup.write_text(source, encoding="utf-8")
    path.write_text(result, encoding="utf-8")
    print("Маршруты планирования подключены.")


if __name__ == "__main__":
    main()
