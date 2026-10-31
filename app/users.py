import json
import re
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, SecretStr, field_validator
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.auth import UserPublic, require_permission
from app.database import engine
from app.security import hash_password

router = APIRouter(prefix="/admin", tags=["Пользователи и роли"])


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
        value = value.strip().lower()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value):
            raise ValueError("Некорректный email")
        return value

    @field_validator("first_name", "last_name")
    @classmethod
    def validate_name(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("Имя и фамилия не должны быть пустыми")
        return value

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
            result = connection.execute(
                text(USER_SELECT + " WHERE u.id = :uid GROUP BY u.id"),
                {"uid": user_id},
            ).mappings().one()
            output = UserSummary(**dict(result))
        return output
    except IntegrityError:
        raise HTTPException(409, "Конфликт данных: проверьте уникальность email.") from None
    except SQLAlchemyError:
        raise HTTPException(503, "База данных временно недоступна.") from None
