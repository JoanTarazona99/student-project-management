from getpass import getpass, GetPassWarning
import re
import sys
import warnings

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.database import engine
from app.security import hash_password


def main():
    warnings.simplefilter("error", GetPassWarning)

    email = input("Email администратора: ").strip().lower()
    if len(email) > 254 or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        print("ОШИБКА: некорректный email.")
        return 1

    password = getpass("Пароль администратора: ")
    confirmation = getpass("Повторите пароль: ")

    if len(password) < 12:
        print("ОШИБКА: используйте пароль длиной не менее 12 символов.")
        return 1

    if password != confirmation:
        print("ОШИБКА: пароли не совпадают.")
        return 1

    stored_hash = hash_password(password)

    try:
        with engine.begin() as connection:
            connection.execute(
                text("SELECT pg_advisory_xact_lock(73405201)")
            )

            admin_role = connection.execute(
                text("SELECT id FROM public.roles WHERE code = 'admin'")
            ).scalar_one()

            existing_admin = connection.execute(
                text("""
                    SELECT 1
                    FROM public.user_roles
                    WHERE role_id = :role_id
                    LIMIT 1
                """),
                {"role_id": admin_role},
            ).scalar_one_or_none()

            if existing_admin is not None:
                print("Администратор уже существует. Изменения не выполнены.")
                return 1

            existing_user = connection.execute(
                text("""
                    SELECT id FROM public.users
                    WHERE lower(email) = :email
                """),
                {"email": email},
            ).scalar_one_or_none()

            if existing_user is not None:
                print("Email уже используется. Изменения не выполнены.")
                return 1

            user_id = connection.execute(
                text("""
                    INSERT INTO public.users
                        (email, password_hash, last_name, first_name)
                    VALUES
                        (:email, :password_hash, :last_name, :first_name)
                    RETURNING id
                """),
                {
                    "email": email,
                    "password_hash": stored_hash,
                    "last_name": "Администратор",
                    "first_name": "Системы",
                },
            ).scalar_one()

            connection.execute(
                text("""
                    INSERT INTO public.user_roles (user_id, role_id)
                    VALUES (:user_id, :role_id)
                """),
                {"user_id": user_id, "role_id": admin_role},
            )

            connection.execute(
                text("""
                    INSERT INTO public.audit_events
                        (actor_id, action, entity_type, entity_id, details)
                    VALUES
                        (:user_id, 'bootstrap_admin', 'users', :user_id,
                         '{"source": "local_cli"}'::jsonb)
                """),
                {"user_id": user_id},
            )

        print("АДМИНИСТРАТОР СОЗДАН.")
        print("Назначен существующий ролью admin набор разрешений.")
        print("Пароль сохранен только в виде хеша.")
        return 0

    except SQLAlchemyError as error:
        print(f"ОШИБКА БД: {type(error).__name__}")
        print("Создание администратора не завершено.")
        return 1
    finally:
        engine.dispose()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except GetPassWarning:
        print("Ввод скрытого пароля недоступен. Операция отменена.")
        sys.exit(1)
