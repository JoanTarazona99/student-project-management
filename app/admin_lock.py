from fastapi import HTTPException
from sqlalchemy import text


ADMIN_MUTATION_LOCK_ID = 73_405_201


def acquire_admin_mutation_lock(connection):
    connection.execute(
        text("SELECT pg_advisory_xact_lock(:lock_id)"),
        {"lock_id": ADMIN_MUTATION_LOCK_ID},
    )


def current_actor_state(connection, actor_id):
    return connection.execute(text("""
        SELECT u.is_active,
               COALESCE(
                   array_agg(DISTINCT r.code)
                       FILTER (WHERE r.code IS NOT NULL),
                   ARRAY[]::varchar[]
               ) AS roles,
               COALESCE(
                   array_agg(DISTINCT p.code)
                       FILTER (WHERE p.code IS NOT NULL),
                   ARRAY[]::varchar[]
               ) AS permissions
        FROM public.users u
        LEFT JOIN public.user_roles ur ON ur.user_id = u.id
        LEFT JOIN public.roles r ON r.id = ur.role_id
        LEFT JOIN public.role_permissions rp ON rp.role_id = r.id
        LEFT JOIN public.permissions p ON p.id = rp.permission_id
        WHERE u.id = :actor_id
        GROUP BY u.id
    """), {"actor_id": actor_id}).mappings().one_or_none()


def require_current_actor(connection, actor_id, required_permissions, require_admin=False):
    state = current_actor_state(connection, actor_id)
    if (
        state is None
        or not state["is_active"]
        or not set(required_permissions).issubset(state["permissions"])
        or (require_admin and "admin" not in state["roles"])
    ):
        raise HTTPException(403, "Недостаточно прав для выполнения операции.")
    return state
