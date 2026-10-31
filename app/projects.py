from contextlib import contextmanager
from datetime import datetime
import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.auth import UserPublic, require_permission
from app.database import engine

router = APIRouter(tags=["Темы и проекты"])


@contextmanager
def database(write=False):
    try:
        with (engine.begin() if write else engine.connect()) as connection:
            yield connection
    except IntegrityError:
        raise HTTPException(409, "Конфликт данных. Проверьте связанные записи.") from None
    except SQLAlchemyError:
        raise HTTPException(503, "База данных временно недоступна.") from None


def audit(connection, actor_id, action, entity_type, entity_id, details=None):
    connection.execute(text("""
        INSERT INTO public.audit_events
            (actor_id, action, entity_type, entity_id, details)
        VALUES (:actor, :action, :kind, :id, CAST(:details AS jsonb))
    """), {"actor": actor_id, "action": action, "kind": entity_type,
             "id": entity_id, "details": json.dumps(details or {})})


def has_role(connection, user_id, role_code):
    return connection.execute(text("""
        SELECT EXISTS (
            SELECT 1 FROM public.users u
            JOIN public.user_roles ur ON ur.user_id = u.id
            JOIN public.roles r ON r.id = ur.role_id
            WHERE u.id = :id AND u.is_active AND r.code = :role
        )
    """), {"id": user_id, "role": role_code}).scalar_one()


class TopicCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    description: str = Field(default="", max_length=10000)

    @field_validator("title")
    @classmethod
    def title_not_blank(cls, value):
        if not value.strip():
            raise ValueError("Название темы не должно быть пустым")
        return value.strip()


class TopicOut(BaseModel):
    id: int
    title: str
    description: str
    created_by: int
    approved_by: int | None
    approved_at: datetime | None
    created_at: datetime


class ProjectCreate(BaseModel):
    topic_id: int = Field(gt=0)
    teacher_id: int = Field(gt=0)
    student_id: int | None = Field(default=None, gt=0)


class ProjectOut(BaseModel):
    id: int
    topic_id: int
    student_id: int
    supervisor_id: int | None
    teacher_id: int
    plan_approved_by: int | None
    plan_approved_at: datetime | None
    created_at: datetime


class SupervisorAssign(BaseModel):
    supervisor_id: int = Field(gt=0)


PROJECT_SCOPE = """
    (student_id = :actor OR supervisor_id = :actor
     OR teacher_id = :actor OR :department)
"""


@router.post("/topics", response_model=TopicOut, status_code=201)
def create_topic(
    data: TopicCreate,
    actor: Annotated[UserPublic, Depends(require_permission("topics.manage"))],
):
    with database(True) as connection:
        row = connection.execute(text("""
            INSERT INTO public.topics (title, description, created_by)
            VALUES (:title, :description, :actor) RETURNING *
        """), {"title": data.title, "description": data.description,
                 "actor": actor.id}).mappings().one()
        audit(connection, actor.id, "topic_created", "topics", row["id"])
        result = TopicOut(**dict(row))
    return result


@router.post("/topics/{topic_id}/approve", response_model=TopicOut)
def approve_topic(
    topic_id: int,
    actor: Annotated[UserPublic, Depends(require_permission("topics.manage"))],
):
    with database(True) as connection:
        row = connection.execute(text("""
            SELECT * FROM public.topics WHERE id = :id FOR UPDATE
        """), {"id": topic_id}).mappings().one_or_none()
        if row is None:
            raise HTTPException(404, "Тема не найдена.")
        if row["approved_at"] is not None:
            raise HTTPException(409, "Тема уже утверждена.")
        row = connection.execute(text("""
            UPDATE public.topics
            SET approved_by = :actor, approved_at = CURRENT_TIMESTAMP
            WHERE id = :id RETURNING *
        """), {"actor": actor.id, "id": topic_id}).mappings().one()
        audit(connection, actor.id, "topic_approved", "topics", topic_id)
        result = TopicOut(**dict(row))
    return result


@router.get("/topics", response_model=list[TopicOut])
def list_topics(
    actor: Annotated[UserPublic, Depends(require_permission("projects.read"))],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    with database() as connection:
        return connection.execute(text("""
            SELECT * FROM public.topics
            WHERE approved_at IS NOT NULL OR :manage
            ORDER BY id LIMIT :limit OFFSET :offset
        """), {"manage": "topics.manage" in actor.permissions,
                 "limit": limit, "offset": offset}).mappings().all()


@router.post("/projects", response_model=ProjectOut, status_code=201)
def create_project(
    data: ProjectCreate,
    actor: Annotated[UserPublic, Depends(require_permission("projects.create"))],
):
    if "teacher" in actor.roles:
        if data.teacher_id != actor.id:
            raise HTTPException(403, "Преподаватель регистрирует проекты только в своей области ответственности.")
        student_id = data.student_id
        if student_id is None:
            raise HTTPException(422, "Укажите студента.")
    else:
        student_id = actor.id if data.student_id is None else data.student_id
        if student_id != actor.id or "student" not in actor.roles:
            raise HTTPException(403, "Студент может зарегистрировать только собственный проект.")

    with database(True) as connection:
        topic = connection.execute(text("""
            SELECT id, approved_at FROM public.topics WHERE id = :id FOR SHARE
        """), {"id": data.topic_id}).mappings().one_or_none()
        if topic is None:
            raise HTTPException(404, "Тема не найдена.")
        if topic["approved_at"] is None:
            raise HTTPException(409, "Для регистрации проекта тема должна быть утверждена.")
        if not has_role(connection, student_id, "student"):
            raise HTTPException(422, "Студент не найден или учетная запись неактивна.")
        if not has_role(connection, data.teacher_id, "teacher"):
            raise HTTPException(422, "Преподаватель не найден или учетная запись неактивна.")
        if student_id == data.teacher_id:
            raise HTTPException(422, "Студент и преподаватель должны быть разными пользователями.")
        row = connection.execute(text("""
            INSERT INTO public.projects (topic_id, student_id, teacher_id)
            VALUES (:topic, :student, :teacher) RETURNING *
        """), {"topic": data.topic_id, "student": student_id,
                 "teacher": data.teacher_id}).mappings().one()
        audit(connection, actor.id, "project_created", "projects", row["id"])
        result = ProjectOut(**dict(row))
    return result


@router.get("/projects", response_model=list[ProjectOut])
def list_projects(
    actor: Annotated[UserPublic, Depends(require_permission("projects.read"))],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    with database() as connection:
        return connection.execute(text(
            "SELECT * FROM public.projects WHERE " + PROJECT_SCOPE +
            " ORDER BY id LIMIT :limit OFFSET :offset"
        ), {"actor": actor.id, "department": "department" in actor.roles,
            "limit": limit, "offset": offset}).mappings().all()


@router.get("/projects/{project_id}", response_model=ProjectOut)
def get_project(
    project_id: int,
    actor: Annotated[UserPublic, Depends(require_permission("projects.read"))],
):
    with database() as connection:
        row = connection.execute(text(
            "SELECT * FROM public.projects WHERE id = :id AND " + PROJECT_SCOPE
        ), {"id": project_id, "actor": actor.id,
            "department": "department" in actor.roles}).mappings().one_or_none()
        if row is None:
            raise HTTPException(404, "Проект не найден или недоступен.")
        return row


@router.put("/projects/{project_id}/supervisor", response_model=ProjectOut)
def assign_supervisor(
    project_id: int,
    data: SupervisorAssign,
    actor: Annotated[UserPublic, Depends(require_permission("supervisors.assign"))],
):
    with database(True) as connection:
        row = connection.execute(text("""
            SELECT * FROM public.projects
            WHERE id = :id AND (teacher_id = :actor OR :department)
            FOR UPDATE
        """), {"id": project_id, "actor": actor.id,
                 "department": "department" in actor.roles}).mappings().one_or_none()
        if row is None:
            raise HTTPException(404, "Проект не найден или недоступен.")
        if row["plan_approved_at"] is not None:
            raise HTTPException(409, "Смена руководителя после утверждения плана требует отдельной процедуры.")
        if data.supervisor_id == row["student_id"]:
            raise HTTPException(422, "Студент не может быть руководителем собственного проекта.")
        if not has_role(connection, data.supervisor_id, "supervisor"):
            raise HTTPException(422, "Руководитель не найден или учетная запись неактивна.")
        updated = connection.execute(text("""
            UPDATE public.projects SET supervisor_id = :supervisor
            WHERE id = :id RETURNING *
        """), {"supervisor": data.supervisor_id, "id": project_id}).mappings().one()
        audit(connection, actor.id, "supervisor_assigned", "projects", project_id,
              {"previous": row["supervisor_id"], "current": data.supervisor_id})
        result = ProjectOut(**dict(updated))
    return result
