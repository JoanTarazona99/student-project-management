from datetime import datetime, timedelta, timezone
from typing import Annotated
import secrets

import jwt
from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field, SecretStr
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.database import config, engine
from app.security import hash_password, verify_password

SECRET = config.get("JWT_SECRET") or ""
if len(SECRET) < 32:
    raise RuntimeError("Run: python -m scripts.configure_auth")

ALGORITHM = "HS256"
ISSUER = "student-project-management"
AUDIENCE = "spm-local-app"
TOKEN_MINUTES = 30
DUMMY_HASH = hash_password(secrets.token_urlsafe(32))
router = APIRouter(prefix="/auth", tags=["Аутентификация"])
bearer = HTTPBearer(auto_error=False)


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: SecretStr = Field(min_length=1, max_length=1024)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class UserPublic(BaseModel):
    id: int
    email: str
    first_name: str
    last_name: str
    roles: list[str]
    permissions: list[str]


def unauthorized():
    return HTTPException(
        status_code=401,
        detail="Неверные учетные данные или недействительный токен.",
        headers={"WWW-Authenticate": "Bearer"},
    )


@router.post("/login", response_model=TokenResponse)
def login(data: LoginRequest, response: Response):
    email = data.email.strip().lower()
    try:
        with engine.begin() as connection:
            user = connection.execute(
                text("""
                    SELECT id, password_hash, is_active
                    FROM public.users WHERE lower(email) = :email
                """),
                {"email": email},
            ).mappings().one_or_none()

            stored_hash = user["password_hash"] if user else DUMMY_HASH
            valid = verify_password(data.password.get_secret_value(), stored_hash)
            if not valid or user is None or not user["is_active"]:
                raise unauthorized()

            now = datetime.now(timezone.utc)
            token = jwt.encode(
                {
                    "sub": str(user["id"]),
                    "iat": now,
                    "exp": now + timedelta(minutes=TOKEN_MINUTES),
                    "iss": ISSUER,
                    "aud": AUDIENCE,
                    "jti": secrets.token_urlsafe(18),
                    "type": "access",
                },
                SECRET,
                algorithm=ALGORITHM,
            )
            connection.execute(
                text("""
                    INSERT INTO public.audit_events
                        (actor_id, action, entity_type, entity_id, details)
                    VALUES
                        (:id, 'login_success', 'users', :id, '{}'::jsonb)
                """),
                {"id": user["id"]},
            )
    except SQLAlchemyError:
        raise HTTPException(503, "База данных временно недоступна.") from None

    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return TokenResponse(access_token=token, expires_in=TOKEN_MINUTES * 60)


def get_current_user(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(bearer)
    ],
) -> UserPublic:
    if credentials is None:
        raise unauthorized()
    try:
        payload = jwt.decode(
            credentials.credentials,
            SECRET,
            algorithms=[ALGORITHM],
            issuer=ISSUER,
            audience=AUDIENCE,
            options={"require": ["sub", "iat", "exp", "iss", "aud", "jti", "type"]},
        )
        if payload["type"] != "access":
            raise unauthorized()
        user_id = int(payload["sub"])
        if user_id <= 0:
            raise unauthorized()
    except (jwt.InvalidTokenError, ValueError, TypeError):
        raise unauthorized() from None

    try:
        with engine.connect() as connection:
            user = connection.execute(
                text("""
                    SELECT id, email, first_name, last_name
                    FROM public.users WHERE id = :id AND is_active = true
                """),
                {"id": user_id},
            ).mappings().one_or_none()
            if user is None:
                raise unauthorized()
            roles = connection.execute(
                text("""
                    SELECT r.code
                    FROM public.user_roles ur
                    JOIN public.roles r ON r.id = ur.role_id
                    WHERE ur.user_id = :id ORDER BY r.code
                """),
                {"id": user_id},
            ).scalars().all()
            permissions = connection.execute(
                text("""
                    SELECT DISTINCT p.code
                    FROM public.user_roles ur
                    JOIN public.role_permissions rp ON rp.role_id = ur.role_id
                    JOIN public.permissions p ON p.id = rp.permission_id
                    WHERE ur.user_id = :id ORDER BY p.code
                """),
                {"id": user_id},
            ).scalars().all()
            return UserPublic(**dict(user), roles=roles, permissions=permissions)
    except SQLAlchemyError:
        raise HTTPException(503, "База данных временно недоступна.") from None


def require_permission(code: str):
    def dependency(
        user: Annotated[UserPublic, Depends(get_current_user)],
    ) -> UserPublic:
        if code not in user.permissions:
            raise HTTPException(403, "Недостаточно прав для выполнения операции.")
        return user
    return dependency


@router.get("/me", response_model=UserPublic)
def me(user: Annotated[UserPublic, Depends(get_current_user)]):
    return user
