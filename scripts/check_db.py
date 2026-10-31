from pathlib import Path
import sys

from dotenv import dotenv_values
from sqlalchemy import URL, create_engine, text
from sqlalchemy.exc import SQLAlchemyError


def main():
    root = Path(__file__).resolve().parents[1]
    config = dotenv_values(root / ".env", interpolate=False)

    required = ("DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD")
    if any(not config.get(key) for key in required):
        print("ОШИБКА: заполните обязательные параметры в файле .env.")
        return 1

    url = URL.create(
        drivername="postgresql+psycopg",
        username=config["DB_USER"],
        password=config["DB_PASSWORD"],
        host=config["DB_HOST"],
        port=int(config["DB_PORT"]),
        database=config["DB_NAME"],
    )

    engine = create_engine(
        url,
        connect_args={"connect_timeout": 5},
        echo=False,
    )

    try:
        with engine.connect() as connection:
            connection.execute(text("SET TRANSACTION READ ONLY"))

            user, database = connection.execute(
                text("SELECT current_user, current_database()")
            ).one()

            tables = connection.execute(text("""
                SELECT tablename
                FROM pg_catalog.pg_tables
                WHERE schemaname = 'public'
                ORDER BY tablename
            """)).scalars().all()

            counts = connection.execute(text("""
                SELECT
                    (SELECT count(*) FROM public.roles),
                    (SELECT count(*) FROM public.permissions),
                    (SELECT count(*) FROM public.users)
            """)).one()

            student_hex = connection.execute(text("""
                SELECT encode(convert_to(name, 'UTF8'), 'hex')
                FROM public.roles
                WHERE code = 'student'
            """)).scalar_one()

            print("ПОДКЛЮЧЕНИЕ К POSTGRESQL: УСПЕШНО")
            print(f"Пользователь: {user}")
            print(f"База данных: {database}")
            print(f"Количество таблиц: {len(tables)}")
            print(f"Количество ролей: {counts[0]}")
            print(f"Количество разрешений: {counts[1]}")
            print(f"Количество пользователей: {counts[2]}")
            print(
                "Кодировка названия роли: "
                + (
                    "КОРРЕКТНА"
                    if student_hex == "d0a1d182d183d0b4d0b5d0bdd182"
                    else "ТРЕБУЕТ ПРОВЕРКИ"
                )
            )
            print("Изменения в БД не выполнялись.")
            return 0

    except SQLAlchemyError as error:
        print(f"ОШИБКА ПОДКЛЮЧЕНИЯ ИЛИ ЗАПРОСА: {type(error).__name__}")
        print("Проверьте сервер, параметры .env и структуру БД.")
        return 1
    finally:
        engine.dispose()


if __name__ == "__main__":
    sys.exit(main())
