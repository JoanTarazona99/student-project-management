import json
import re
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import (
    BaseModel, ConfigDict, Field, SecretStr, StrictBool,
    field_validator, model_validator,
)
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.auth import UserPublic, require_permission
from app.admin_lock import (
    acquire_admin_mutation_lock,
    require_current_actor,
)
from app.database import engine
from app.projects import audit
from app.security import hash_password

router = APIRouter(prefix="/admin", tags=["Пользователи и роли"])
MAX_USER_ID = 9_223_372_036_854_775_807
APPLICATION_ROLE_CODES = frozenset({
    "student", "supervisor", "teacher", "department", "admin",
})
ROLE_PERMISSION_CEILING = {
    "student": frozenset({
        "projects.create", "projects.read", "plans.propose",
        "materials.upload", "statuses.read",
    }),
    "supervisor": frozenset({
        "projects.read", "plans.propose", "plans.approve",
        "reviews.write", "statuses.read",
    }),
    "teacher": frozenset({
        "topics.manage", "projects.create", "projects.read",
        "supervisors.assign", "plans.propose", "plans.approve",
        "reviews.write", "statuses.read", "reports.read",
    }),
    "department": frozenset({
        "topics.manage", "projects.read", "supervisors.assign",
        "statuses.read", "reports.read",
    }),
    "admin": frozenset({"users.manage", "roles.manage"}),
}
ADMIN_REQUIRED_PERMISSIONS = ROLE_PERMISSION_CEILING["admin"]


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


class UserPage(BaseModel):
    total: int
    page: int
    limit: int
    offset: int
    items: list[UserSummary]


class UserEffectivePermissions(BaseModel):
    user_id: int
    role_codes: list[str]
    permission_codes: list[str]


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


class PermissionSummary(BaseModel):
    code: str
    name: str


class RolePermissionsSummary(BaseModel):
    role_code: str
    permission_codes: list[str]


class UserRolesSummary(BaseModel):
    user_id: int
    role_codes: list[str]


def normalize_codes(values):
    normalized = [value.strip() for value in values]
    if any(not value for value in normalized):
        raise ValueError("Коды не должны быть пустыми.")
    if len(set(normalized)) != len(normalized):
        raise ValueError("Повторяющиеся коды запрещены.")
    return normalized


class RoleCodesReplace(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role_codes: list[str] = Field(min_length=1, max_length=5)

    @field_validator("role_codes")
    @classmethod
    def normalize_role_codes(cls, values):
        return normalize_codes(values)


class PermissionCodesReplace(BaseModel):
    model_config = ConfigDict(extra="forbid")

    permission_codes: list[str] = Field(max_length=12)

    @field_validator("permission_codes")
    @classmethod
    def normalize_permission_codes(cls, values):
        return normalize_codes(values)


def require_role_administrator(
    actor: Annotated[UserPublic, Depends(require_permission("roles.manage"))],
):
    if "admin" not in actor.roles:
        raise HTTPException(403, "Требуется активная учетная запись администратора.")
    return actor


def require_admin_inside_transaction(connection, actor_id):
    return require_current_actor(
        connection, actor_id, {"roles.manage"}, require_admin=True
    )


def read_application_role(connection, role_code, lock=False):
    role_code = role_code.strip()
    if not role_code or role_code not in APPLICATION_ROLE_CODES:
        raise HTTPException(404, "Роль не найдена.")
    query = "SELECT id, code FROM public.roles WHERE code = :code"
    if lock:
        query += " FOR UPDATE"
    role = connection.execute(
        text(query), {"code": role_code}
    ).mappings().one_or_none()
    if role is None:
        raise HTTPException(404, "Роль не найдена.")
    return role


def read_permission_codes(connection, role_id):
    return connection.execute(text("""
        SELECT p.code
        FROM public.role_permissions rp
        JOIN public.permissions p ON p.id = rp.permission_id
        WHERE rp.role_id = :role_id
        ORDER BY p.code
    """), {"role_id": role_id}).scalars().all()


def validate_permission_codes(connection, permission_codes):
    if not permission_codes:
        return {}
    rows = connection.execute(text("""
        SELECT id, code FROM public.permissions WHERE code = ANY(:codes)
    """), {"codes": permission_codes}).mappings().all()
    permission_ids = {row["code"]: row["id"] for row in rows}
    if set(permission_ids) != set(permission_codes):
        raise HTTPException(422, "Указан неизвестный код разрешения.")
    return permission_ids


def active_admin_count(connection):
    return connection.execute(text("""
        SELECT COUNT(DISTINCT u.id)
        FROM public.users u
        JOIN public.user_roles ur ON ur.user_id = u.id
        JOIN public.roles r ON r.id = ur.role_id
        WHERE u.is_active AND r.code = 'admin'
    """)).scalar_one()


def validate_project_role_removal(connection, user_id, removed_roles):
    project_columns = {
        "student": "student_id",
        "teacher": "teacher_id",
        "supervisor": "supervisor_id",
    }
    for role_code in sorted(removed_roles & project_columns.keys()):
        column = project_columns[role_code]
        assigned = connection.execute(text(
            f"SELECT EXISTS (SELECT 1 FROM public.projects WHERE {column} = :user_id)"
        ), {"user_id": user_id}).scalar_one()
        if assigned:
            raise HTTPException(
                409,
                f"Нельзя снять роль {role_code}: пользователь назначен в существующем проекте.",
            )


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


@router.get("/permissions", response_model=list[PermissionSummary])
def list_permissions(
    actor: Annotated[UserPublic, Depends(require_role_administrator)],
):
    try:
        with engine.connect() as connection:
            require_admin_inside_transaction(connection, actor.id)
            rows = connection.execute(text("""
                SELECT code, name FROM public.permissions ORDER BY code
            """)).mappings().all()
            return [PermissionSummary(**dict(row)) for row in rows]
    except SQLAlchemyError:
        raise HTTPException(503, "База данных временно недоступна.") from None


@router.get(
    "/roles/{role_code}/permissions",
    response_model=RolePermissionsSummary,
)
def get_role_permissions(
    role_code: str,
    actor: Annotated[UserPublic, Depends(require_role_administrator)],
):
    try:
        with engine.connect() as connection:
            require_admin_inside_transaction(connection, actor.id)
            role = read_application_role(connection, role_code)
            return RolePermissionsSummary(
                role_code=role["code"],
                permission_codes=read_permission_codes(connection, role["id"]),
            )
    except SQLAlchemyError:
        raise HTTPException(503, "База данных временно недоступна.") from None


@router.put(
    "/roles/{role_code}/permissions",
    response_model=RolePermissionsSummary,
)
def replace_role_permissions(
    role_code: str,
    data: PermissionCodesReplace,
    actor: Annotated[UserPublic, Depends(require_role_administrator)],
):
    try:
        with engine.begin() as connection:
            acquire_admin_mutation_lock(connection)
            require_admin_inside_transaction(connection, actor.id)
            role = read_application_role(connection, role_code, lock=True)
            new_codes = set(data.permission_codes)
            permission_ids = validate_permission_codes(connection, data.permission_codes)
            ceiling = ROLE_PERMISSION_CEILING[role["code"]]
            if not new_codes.issubset(ceiling):
                raise HTTPException(
                    422, "Разрешения выходят за пределы политики выбранной роли."
                )
            if (
                role["code"] == "admin"
                and not ADMIN_REQUIRED_PERMISSIONS.issubset(new_codes)
            ):
                raise HTTPException(
                    409, "Роль admin должна сохранять users.manage и roles.manage."
                )

            old_codes = read_permission_codes(connection, role["id"])
            if set(old_codes) != new_codes:
                connection.execute(text("""
                    DELETE FROM public.role_permissions WHERE role_id = :role_id
                """), {"role_id": role["id"]})
                if data.permission_codes:
                    connection.execute(text("""
                        INSERT INTO public.role_permissions (role_id, permission_id)
                        VALUES (:role_id, :permission_id)
                    """), [
                        {"role_id": role["id"], "permission_id": permission_ids[code]}
                        for code in sorted(new_codes)
                    ])
                audit(connection, actor.id, "role_permissions_replaced",
                      "role_permissions", role["id"], {
                          "before": old_codes,
                          "after": sorted(new_codes),
                          "added": sorted(new_codes - set(old_codes)),
                          "removed": sorted(set(old_codes) - new_codes),
                      })
            return RolePermissionsSummary(
                role_code=role["code"],
                permission_codes=sorted(new_codes),
            )
    except IntegrityError:
        raise HTTPException(409, "Конфликт целостности при изменении разрешений.") from None
    except SQLAlchemyError:
        raise HTTPException(503, "База данных временно недоступна.") from None


@router.get("/users/{user_id}/roles", response_model=UserRolesSummary)
def get_user_roles(
    user_id: Annotated[int, Path(ge=1, le=MAX_USER_ID)],
    actor: Annotated[UserPublic, Depends(require_role_administrator)],
):
    try:
        with engine.connect() as connection:
            require_admin_inside_transaction(connection, actor.id)
            exists = connection.execute(text("""
                SELECT EXISTS (SELECT 1 FROM public.users WHERE id = :user_id)
            """), {"user_id": user_id}).scalar_one()
            if not exists:
                raise HTTPException(404, "Пользователь не найден.")
            role_codes = connection.execute(text("""
                SELECT r.code
                FROM public.user_roles ur
                JOIN public.roles r ON r.id = ur.role_id
                WHERE ur.user_id = :user_id ORDER BY r.code
            """), {"user_id": user_id}).scalars().all()
            return UserRolesSummary(user_id=user_id, role_codes=role_codes)
    except SQLAlchemyError:
        raise HTTPException(503, "База данных временно недоступна.") from None


@router.put("/users/{user_id}/roles", response_model=UserRolesSummary)
def replace_user_roles(
    user_id: Annotated[int, Path(ge=1, le=MAX_USER_ID)],
    data: RoleCodesReplace,
    actor: Annotated[UserPublic, Depends(require_role_administrator)],
):
    try:
        with engine.begin() as connection:
            acquire_admin_mutation_lock(connection)
            require_admin_inside_transaction(connection, actor.id)
            target = connection.execute(text("""
                SELECT id, is_active FROM public.users
                WHERE id = :user_id FOR UPDATE
            """), {"user_id": user_id}).mappings().one_or_none()
            if target is None:
                raise HTTPException(404, "Пользователь не найден.")
            if not set(data.role_codes).issubset(APPLICATION_ROLE_CODES):
                raise HTTPException(422, "Недоступный код роли.")
            role_rows = connection.execute(text("""
                SELECT id, code FROM public.roles
                WHERE code = ANY(:role_codes)
            """), {"role_codes": data.role_codes}).mappings().all()
            role_ids = {row["code"]: row["id"] for row in role_rows}
            if set(role_ids) != set(data.role_codes):
                raise HTTPException(422, "Указан неизвестный или недоступный код роли.")

            old_codes = connection.execute(text("""
                SELECT r.code
                FROM public.user_roles ur
                JOIN public.roles r ON r.id = ur.role_id
                WHERE ur.user_id = :user_id ORDER BY r.code
            """), {"user_id": user_id}).scalars().all()
            old_set = set(old_codes)
            new_set = set(data.role_codes)
            if old_set == new_set:
                return UserRolesSummary(user_id=user_id, role_codes=sorted(new_set))
            if actor.id == user_id:
                raise HTTPException(403, "Администратор не может изменять собственные роли.")

            removed_roles = old_set - new_set
            validate_project_role_removal(connection, user_id, removed_roles)
            if (
                target["is_active"]
                and "admin" in removed_roles
                and active_admin_count(connection) <= 1
            ):
                raise HTTPException(
                    409, "Нельзя снять роль у последней активной учетной записи администратора."
                )

            connection.execute(text("""
                DELETE FROM public.user_roles WHERE user_id = :user_id
            """), {"user_id": user_id})
            connection.execute(text("""
                INSERT INTO public.user_roles (user_id, role_id)
                VALUES (:user_id, :role_id)
            """), [
                {"user_id": user_id, "role_id": role_ids[code]}
                for code in sorted(new_set)
            ])
            audit(connection, actor.id, "user_roles_replaced",
                  "user_roles", user_id, {
                      "before": old_codes,
                      "after": sorted(new_set),
                      "added": sorted(new_set - old_set),
                      "removed": sorted(removed_roles),
                  })
            return UserRolesSummary(user_id=user_id, role_codes=sorted(new_set))
    except IntegrityError:
        raise HTTPException(409, "Конфликт целостности при изменении ролей пользователя.") from None
    except SQLAlchemyError:
        raise HTTPException(503, "База данных временно недоступна.") from None


@router.get("/users", response_model=list[UserSummary] | UserPage)
def list_users(
    actor: Annotated[UserPublic, Depends(require_permission("users.manage"))],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    q: Annotated[str, Query(max_length=254)] = "",
    paginated: bool = False,
):
    pattern = "%" + q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    parameters = {"pattern": pattern, "limit": limit, "offset": offset}
    predicate = """
        WHERE (u.email ILIKE :pattern ESCAPE '\\'
            OR u.first_name ILIKE :pattern ESCAPE '\\'
            OR u.last_name ILIKE :pattern ESCAPE '\\')
    """
    try:
        with engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
            with connection.begin():
                connection.execute(text("SET TRANSACTION READ ONLY"))
                require_current_actor(connection, actor.id, {"users.manage"})
                rows = connection.execute(
                    text(USER_SELECT + predicate + " GROUP BY u.id ORDER BY u.id LIMIT :limit OFFSET :offset"),
                    parameters,
                ).mappings().all()
                items = [UserSummary(**dict(row)) for row in rows]
                if not paginated:
                    return items
                total = connection.execute(text(
                    "SELECT count(*) FROM public.users u " + predicate
                ), parameters).scalar_one()
                return UserPage(total=total, page=offset // limit + 1,
                                limit=limit, offset=offset, items=items)
    except SQLAlchemyError:
        raise HTTPException(503, "База данных временно недоступна.") from None


@router.get("/users/{user_id}", response_model=UserSummary)
def get_user(
    user_id: Annotated[int, Path(ge=1, le=MAX_USER_ID)],
    actor: Annotated[UserPublic, Depends(require_permission("users.manage"))],
):
    try:
        with engine.connect() as connection:
            return read_user_summary(connection, user_id)
    except SQLAlchemyError:
        raise HTTPException(503, "База данных временно недоступна.") from None


@router.get("/users/{user_id}/effective-permissions", response_model=UserEffectivePermissions)
def get_user_effective_permissions(
    user_id: Annotated[int, Path(ge=1, le=MAX_USER_ID)],
    actor: Annotated[UserPublic, Depends(require_role_administrator)],
):
    try:
        with engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
            with connection.begin():
                connection.execute(text("SET TRANSACTION READ ONLY"))
                require_current_actor(connection, actor.id, {"users.manage", "roles.manage"},
                                      require_admin=True)
                user = read_user_summary(connection, user_id)
                permissions = connection.execute(text("""
                    SELECT DISTINCT p.code FROM public.user_roles ur
                    JOIN public.role_permissions rp ON rp.role_id = ur.role_id
                    JOIN public.permissions p ON p.id = rp.permission_id
                    WHERE ur.user_id = :id ORDER BY p.code
                """), {"id": user_id}).scalars().all()
                return UserEffectivePermissions(user_id=user.id, role_codes=user.roles,
                                                permission_codes=permissions)
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
            acquire_admin_mutation_lock(connection)
            require_current_actor(
                connection, actor.id, {"users.manage", "roles.manage"}
            )
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
    try:
        with engine.begin() as connection:
            acquire_admin_mutation_lock(connection)
            actor_state = require_current_actor(
                connection, actor.id, {"users.manage"}
            )
            current = connection.execute(text("""
                SELECT id, is_active FROM public.users WHERE id = :id FOR UPDATE
            """), {"id": user_id}).mappings().one_or_none()
            if current is None:
                raise HTTPException(404, "Пользователь не найден.")
            if (
                not data.is_active
                and actor.id == user_id
                and "admin" in actor_state["roles"]
            ):
                raise HTTPException(
                    403, "Администратор не может отключить собственную учетную запись."
                )
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
                if active_admin_count(connection) <= 1:
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
