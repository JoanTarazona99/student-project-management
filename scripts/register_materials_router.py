from importlib.metadata import PackageNotFoundError, version
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    try:
        multipart_version = version("python-multipart")
    except PackageNotFoundError:
        raise SystemExit("Сначала выполните: python -m pip install python-multipart")
    path = root / "app" / "main.py"
    source = path.read_text(encoding="utf-8")
    import_line = "from app.materials import router as materials_router"
    register_line = "app.include_router(materials_router)"
    import_anchor = "from app.auth import router as auth_router"
    register_anchor = "app.include_router(auth_router)"
    if import_anchor not in source or register_anchor not in source:
        raise SystemExit("Структура main.py отличается. Файлы не изменены.")
    result = source
    if import_line not in result:
        result = result.replace(import_anchor, import_anchor + "\n" + import_line, 1)
    if register_line not in result:
        result = result.replace(register_anchor, register_anchor + "\n" + register_line, 1)
    requirements = root / "requirements.txt"
    lines = [line for line in requirements.read_text(encoding="utf-8").splitlines()
             if not line.strip().lower().startswith("python-multipart")]
    lines.append("python-multipart==" + multipart_version)
    backup = root / "backups" / "main_before_materials.py"
    backup.parent.mkdir(exist_ok=True)
    if not backup.exists():
        backup.write_text(source, encoding="utf-8")
    path.write_text(result, encoding="utf-8")
    requirements.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("Маршруты материалов, проверки и уведомлений подключены.")
    print("Версия python-multipart добавлена в requirements.txt.")


if __name__ == "__main__":
    main()
