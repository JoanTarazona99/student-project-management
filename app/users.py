import json
import re
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, ConfigDict, Field, SecretStr, StrictBool, field_validator, model_validator
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.auth import UserPublic, require_permission
from app.database import engine
from app.projects import audit
from app.security import hash_password

router = APIRouter(prefix="/admin", tags=["Пользователи и роли"])
MAX_USER_ID = 9_223_372_036_854_775_807
ADMIN_LOCK_NAMESPACE = 5_463_984
ADMIN_LOCK_KEY = 1


def normalize_email_value(value):
    value = value.strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value):
        raise ValueError("Некорректный email")
    return value


def normalize_name(value):
    value = value.strip()
    if not value:
        raise ValueError("Имя и фамилия не должны быть пустыми")
    return value


class UserCreate(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: SecretStr
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    middle_name: str | None = Field(default=None, max_length=100)
    study_group: str | None = Field(default=None, max_length=50)
    role_codes: list[str] = Field(min_length=1, max_length=5)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value):
        return normalize_email_value(value)

    @field_validator("first_name", "last_name")
    @classmethod
    def validate_name(cls, value):
        return normalize_name(value)

    @field_validator("role_codes")
    @classmethod
    def normalize_roles(cls, values):
        return sorted(set(value.strip() for value in values))


class UserSummary(BaseModel):
    id: int
    email: str
    first_name: str
    last_name: str
    middle_name: str | None
    study_group: str | None
    is_active: bool
    roles: list[str]


class UserPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str | None = Field(default=None, min_length=3, max_length=254)
    first_name: str | None = Field(default=None, min_length=1, max_length=100)
    last_name: str | None = Field(default=None, min_length=1, max_length=100)
    middle_name: str | None = Field(default=None, max_length=100)
    study_group: str | None = Field(default=None, max_length=50)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value):
        return normalize_email_value(value) if value is not None else value

    @field_validator("first_name", "last_name")
    @classmethod
    def validate_name(cls, value):
        return normalize_name(value) if value is not None else value

    @model_validator(mode="after")
    def validate_patch(self):
        if not self.model_fields_set:
            raise ValueError("Укажите хотя бы одно поле для изменения.")
        if any(
            field in self.model_fields_set and getattr(self, field) is None
            for field in ("email", "first_name", "last_name")
        ):
            raise ValueError("Email, имя и фамилия не могут быть пустыми.")
        return self


class UserStatusPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_active: StrictBool


class RoleSummary(BaseModel):
    code: str
    name: str


USER_SELECT = """
    SELECT u.id, u.email, u.first_name, u.last_name,
           u.middle_name, u.study_group, u.is_active,
           COALESCE(
               array_agg(r.code ORDER BY r.code)
                   FILTER (WHERE r.code IS NOT NULL),
               ARRAY[]::varchar[]
           ) AS roles
    FROM public.users u
    LEFT JOIN public.user_roles ur ON ur.user_id = u.id
    LEFT JOIN public.roles r ON r.id = ur.role_id
"""


def read_user_summary(connection, user_id):
    row = connection.execute(
        text(USER_SELECT + " WHERE u.id = :uid GROUP BY u.id"),
        {"uid": user_id},
    ).mappings().one_or_none()
    if row is None:
        raise HTTPException(404, "Пользователь не найден.")
    return UserSummary(**dict(row))


@router.get("/roles", response_model=list[RoleSummary])
def list_roles(
    actor: Annotated[UserPublic, Depends(require_permission("roles.manage"))],
):
    try:
        with engine.connect() as connection:
            return connection.execute(
                text("SELECT code, name FROM public.roles ORDER BY code")
            ).mappings().all()
    except SQLAlchemyError:
        raise HTTPException(503, "База данных временно недоступна.") from None


@router.get("/users", response_model=list[UserSummary])
def list_users(
    actor: Annotated[UserPublic, Depends(require_permission("users.manage"))],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    try:
        with engine.connect() as connection:
            return connection.execute(
                text(USER_SELECT + " GROUP BY u.id ORDER BY u.id LIMIT :limit OFFSET :offset"),
                {"limit": limit, "offset": offset},
            ).mappings().all()
    except SQLAlchemyError:
        raise HTTPException(503, "База данных временно недоступна.") from None


@router.post("/users", response_model=UserSummary, status_code=201)
def create_user(
    data: UserCreate,
    actor: Annotated[UserPublic, Depends(require_permission("users.manage"))],
):
    if "roles.manage" not in actor.permissions:
        raise HTTPException(403, "Недостаточно прав для назначения ролей.")
    password = data.password.get_secret_value()
    if not 12 <= len(password) <= 1024:
        raise HTTPException(422, "Длина пароля должна составлять от 12 до 1024 символов.")
    stored_hash = hash_password(password)

    try:
        with engine.begin() as connection:
            roles = connection.execute(
                text("SELECT id, code FROM public.roles WHERE code = ANY(:codes)"),
                {"codes": data.role_codes},
            ).mappings().all()
            if {row["code"] for row in roles} != set(data.role_codes):
                raise HTTPException(422, "Указана неизвестная роль.")

            existing = connection.execute(
                text("SELECT id FROM public.users WHERE lower(email) = :email"),
                {"email": data.email},
            ).scalar_one_or_none()
            if existing is not None:
                raise HTTPException(409, "Пользователь с таким email уже существует.")

            user_id = connection.execute(
                text("""
                    INSERT INTO public.users
                        (email, password_hash, first_name, last_name,
                         middle_name, study_group)
                    VALUES
                        (:email, :password_hash, :first_name, :last_name,
                         :middle_name, :study_group)
                    RETURNING id
                """),
                {
                    "email": data.email,
                    "password_hash": stored_hash,
                    "first_name": data.first_name,
                    "last_name": data.last_name,
                    "middle_name": data.middle_name,
                    "study_group": data.study_group,
                },
            ).scalar_one()

            connection.execute(
                text("INSERT INTO public.user_roles (user_id, role_id) VALUES (:uid, :rid)"),
                [{"uid": user_id, "rid": row["id"]} for row in roles],
            )
            connection.execute(
                text("""
                    INSERT INTO public.audit_events
                        (actor_id, action, entity_type, entity_id, details)
                    VALUES
                        (:actor, 'user_created', 'users', :uid, CAST(:details AS jsonb))
                """),
                {
                    "actor": actor.id,
                    "uid": user_id,
                    "details": json.dumps({"roles": data.role_codes}),
                },
            )
            output = read_user_summary(connection, user_id)
        return output
    except IntegrityError:
        raise HTTPException(409, "Конфликт данных: проверьте уникальность email.") from None
    except SQLAlchemyError:
        raise HTTPException(503, "База данных временно недоступна.") from None


@router.patch("/users/{user_id}", response_model=UserSummary)
def update_user(
    user_id: Annotated[int, Path(ge=1, le=MAX_USER_ID)],
    data: UserPatch,
    actor: Annotated[UserPublic, Depends(require_permission("users.manage"))],
):
    changes = data.model_dump(exclude_unset=True)
    allowed_columns = {
        "email": "email",
        "first_name": "first_name",
        "last_name": "last_name",
        "middle_name": "middle_name",
        "study_group": "study_group",
    }
    try:
        with engine.begin() as connection:
            current = connection.execute(text("""
                SELECT email, first_name, last_name, middle_name, study_group
                FROM public.users WHERE id = :id FOR UPDATE
            """), {"id": user_id}).mappings().one_or_none()
            if current is None:
                raise HTTPException(404, "Пользователь не найден.")

            effective = {
                field: value
                for field, value in changes.items()
                if current[field] != value
            }
            if effective:
                assignments = ", ".join(
                    f"{allowed_columns[field]} = :{field}"
                    for field in effective
                )
                connection.execute(
                    text(f"UPDATE public.users SET {assignments} WHERE id = :id"),
                    {**effective, "id": user_id},
                )
                audit(connection, actor.id, "user_updated", "users", user_id, {
                    "changes": {
                        field: {"old": current[field], "new": value}
                        for field, value in effective.items()
                    },
                })
            output = read_user_summary(connection, user_id)
        return output
    except IntegrityError:
        raise HTTPException(409, "Конфликт данных: проверьте уникальность email.") from None
    except SQLAlchemyError:
        raise HTTPException(503, "База данных временно недоступна.") from None


@router.patch("/users/{user_id}/status", response_model=UserSummary)
def update_user_status(
    user_id: Annotated[int, Path(ge=1, le=MAX_USER_ID)],
    data: UserStatusPatch,
    actor: Annotated[UserPublic, Depends(require_permission("users.manage"))],
):
    if not data.is_active and actor.id == user_id and "admin" in actor.roles:
        raise HTTPException(403, "Администратор не может отключить собственную учетную запись.")

    try:
        with engine.begin() as connection:
            current = connection.execute(text("""
                SELECT id, is_active FROM public.users WHERE id = :id FOR UPDATE
            """), {"id": user_id}).mappings().one_or_none()
            if current is None:
                raise HTTPException(404, "Пользователь не найден.")
            if current["is_active"] == data.is_active:
                return read_user_summary(connection, user_id)

            is_admin = connection.execute(text("""
                SELECT EXISTS (
                    SELECT 1 FROM public.user_roles ur
                    JOIN public.roles r ON r.id = ur.role_id
                    WHERE ur.user_id = :id AND r.code = 'admin'
                )
            """), {"id": user_id}).scalar_one()
            if not data.is_active and is_admin:
                connection.execute(text(
                    "SELECT pg_advisory_xact_lock(:namespace, :lock_key)"
                ), {
                    "namespace": ADMIN_LOCK_NAMESPACE,
                    "lock_key": ADMIN_LOCK_KEY,
                })
                active_admins = connection.execute(text("""
                    SELECT COUNT(DISTINCT u.id)
                    FROM public.users u
                    JOIN public.user_roles ur ON ur.user_id = u.id
                    JOIN public.roles r ON r.id = ur.role_id
                    WHERE u.is_active AND r.code = 'admin'
                """)).scalar_one()
                if active_admins <= 1:
                    raise HTTPException(
                        409, "Нельзя отключить последнюю активную учетную запись администратора."
                    )

            connection.execute(text("""
                UPDATE public.users SET is_active = :is_active WHERE id = :id
            """), {"is_active": data.is_active, "id": user_id})
            audit(connection, actor.id, "user_status_changed", "users", user_id, {
                "changes": {
                    "is_active": {"old": current["is_active"], "new": data.is_active},
                },
            })
            output = read_user_summary(connection, user_id)
        return output
    except SQLAlchemyError:
        raise HTTPException(503, "База данных временно недоступна.") from None
