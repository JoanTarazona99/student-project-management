from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import text

from app.admin_lock import require_current_actor
from app.auth import UserPublic, require_permission
from app.deadlines import utc_timestamp
from app.projects import PROJECT_SCOPE, audit, database, has_role

router = APIRouter(tags=["Планирование и состояние"])

PROJECT_STATUS_LABELS = {
    "planning": "Планирование",
    "in_progress": "В работе",
    "under_review": "На проверке",
    "revision": "На доработке",
    "completed": "Завершен",
}
PROJECT_STATUS_RULES = (
    ("completed", "completed_stages = total_stages"),
    ("revision", "has_revision"),
    ("under_review", "has_under_review"),
)


def project_status_case():
    rules = " ".join(
        f"WHEN {condition} THEN '{status}'"
        for status, condition in PROJECT_STATUS_RULES
    )
    return (
        "CASE WHEN NOT plan_approved OR total_stages = 0 THEN 'planning' "
        f"{rules} ELSE 'in_progress' END"
    )


def project_status(plan_approved, total_stages, completed_stages,
                   has_revision, has_under_review):
    if not plan_approved or not total_stages:
        return "planning"
    conditions = {
        "completed": completed_stages == total_stages,
        "revision": has_revision,
        "under_review": has_under_review,
    }
    return next(
        (status for status, _ in PROJECT_STATUS_RULES if conditions[status]),
        "in_progress",
    )


class StageInput(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    starts_at: AwareDatetime
    due_at: AwareDatetime

    @field_validator("title")
    @classmethod
    def nonblank(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("Название этапа не должно быть пустым")
        return value

    @field_validator("starts_at", "due_at")
    @classmethod
    def to_utc(cls, value):
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def valid_dates(self):
        if self.due_at <= self.starts_at:
            raise ValueError("Срок завершения должен быть позже начала")
        return self


class PlanInput(BaseModel):
    stages: list[StageInput] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def ordered_stages(self):
        for previous, current in zip(self.stages, self.stages[1:]):
            if current.starts_at < previous.due_at:
                raise ValueError("Этапы должны следовать по порядку без пересечения сроков")
        return self


class StageDatesInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage_id: int = Field(ge=1, le=9_223_372_036_854_775_807)
    expected_starts_at: AwareDatetime
    expected_due_at: AwareDatetime
    starts_at: AwareDatetime
    due_at: AwareDatetime

    @field_validator("expected_starts_at", "expected_due_at", "starts_at", "due_at")
    @classmethod
    def to_utc(cls, value):
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def valid_dates(self):
        if self.due_at <= self.starts_at:
            raise ValueError("Срок завершения должен быть позже начала.")
        return self


class PlanDatesInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=10, max_length=2000)
    stages: list[StageDatesInput] = Field(min_length=1, max_length=50)

    @field_validator("reason", mode="before")
    @classmethod
    def strip_reason(cls, value):
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def unique_stages(self):
        if len({stage.stage_id for stage in self.stages}) != len(self.stages):
            raise ValueError("Повторяющиеся ID этапов запрещены.")
        return self


def project_row(connection, project_id, actor, exclusive=False):
    row = connection.execute(text(
        "SELECT * FROM public.projects WHERE id = :id AND " + PROJECT_SCOPE +
        (" FOR UPDATE" if exclusive else " FOR SHARE")
    ), {"id": project_id, "actor": actor.id,
        "department": "department" in actor.roles}).mappings().one_or_none()
    if row is None:
        raise HTTPException(404, "Проект не найден или недоступен.")
    return row


def read_stages(connection, project_id):
    return connection.execute(text("""
        SELECT id, project_id, position, title, starts_at, due_at,
               status, completed_at, created_at
        FROM public.stages WHERE project_id = :id ORDER BY position
    """), {"id": project_id}).mappings().all()


def plan_output(project, stages):
    return {
        "project_id": project["id"],
        "approved": project["plan_approved_at"] is not None,
        "approved_by": project["plan_approved_by"],
        "approved_at": project["plan_approved_at"],
        "stages": [dict(stage) for stage in stages],
    }


@router.get("/projects/{project_id}/plan")
def get_plan(
    project_id: int,
    actor: Annotated[UserPublic, Depends(require_permission("projects.read"))],
):
    with database() as connection:
        project = project_row(connection, project_id, actor)
        return plan_output(project, read_stages(connection, project_id))


@router.put("/projects/{project_id}/plan")
def propose_plan(
    project_id: int,
    data: PlanInput,
    actor: Annotated[UserPublic, Depends(require_permission("plans.propose"))],
):
    with database(True) as connection:
        project = project_row(connection, project_id, actor, exclusive=True)
        if actor.id not in (project["student_id"], project["supervisor_id"], project["teacher_id"]):
            raise HTTPException(403, "Нет полномочий изменять план этого проекта.")
        if project["plan_approved_at"] is not None:
            raise HTTPException(409, "Утвержденный план нельзя заменить этим действием.")
        has_materials = connection.execute(text("""
            SELECT EXISTS (
                SELECT 1 FROM public.material_versions mv
                JOIN public.stages s ON s.id = mv.stage_id
                WHERE s.project_id = :id
            )
        """), {"id": project_id}).scalar_one()
        if has_materials:
            raise HTTPException(409, "План содержит материалы и не может быть заменен.")

        before = [
            {"id": row["id"], "position": row["position"], "title": row["title"],
             "starts_at": row["starts_at"].isoformat(), "due_at": row["due_at"].isoformat()}
            for row in read_stages(connection, project_id)
        ]
        connection.execute(text("DELETE FROM public.stages WHERE project_id = :id"), {"id": project_id})
        connection.execute(text("""
            INSERT INTO public.stages (project_id, position, title, starts_at, due_at)
            VALUES (:project, :position, :title, :starts, :due)
        """), [
            {"project": project_id, "position": position, "title": stage.title,
             "starts": stage.starts_at, "due": stage.due_at}
            for position, stage in enumerate(data.stages, 1)
        ])
        audit(connection, actor.id, "plan_proposed", "projects", project_id,
              {"before": before, "after": data.model_dump(mode="json")["stages"]})
        return plan_output(project, read_stages(connection, project_id))


@router.post("/projects/{project_id}/plan/approve")
def approve_plan(
    project_id: int,
    actor: Annotated[UserPublic, Depends(require_permission("plans.approve"))],
):
    with database(True) as connection:
        project = project_row(connection, project_id, actor, exclusive=True)
        if actor.id not in (project["supervisor_id"], project["teacher_id"]):
            raise HTTPException(403, "План утверждает назначенный руководитель или преподаватель.")
        if project["plan_approved_at"] is not None:
            raise HTTPException(409, "План уже утвержден.")
        if project["supervisor_id"] is None or not has_role(connection, project["supervisor_id"], "supervisor"):
            raise HTTPException(409, "Сначала назначьте активного руководителя.")
        stages = read_stages(connection, project_id)
        if not stages:
            raise HTTPException(409, "План не содержит этапов.")
        if any(row["status"] != "planned" for row in stages):
            raise HTTPException(409, "Состояние этапов не соответствует первоначальному плану.")

        updated = connection.execute(text("""
            UPDATE public.projects
            SET plan_approved_by = :actor, plan_approved_at = CURRENT_TIMESTAMP
            WHERE id = :id RETURNING *
        """), {"actor": actor.id, "id": project_id}).mappings().one()
        connection.execute(text("""
            UPDATE public.stages SET status = 'in_progress'
            WHERE id = :stage
        """), {"stage": stages[0]["id"]})
        for recipient in sorted({project["student_id"], project["supervisor_id"], project["teacher_id"]} - {actor.id}):
            connection.execute(text("""
                INSERT INTO public.notifications (recipient_id, project_id, event_key, message)
                VALUES (:recipient, :project, :key, :message)
                ON CONFLICT (recipient_id, event_key) DO NOTHING
            """), {"recipient": recipient, "project": project_id,
                     "key": f"plan-approved:{project_id}",
                     "message": f"План проекта № {project_id} утвержден."})
        audit(connection, actor.id, "plan_approved", "projects", project_id)
        return plan_output(updated, read_stages(connection, project_id))


@router.put("/projects/{project_id}/plan/dates")
def change_plan_dates(
    project_id: Annotated[int, Path(ge=1, le=9_223_372_036_854_775_807)],
    data: PlanDatesInput,
    actor: Annotated[UserPublic, Depends(require_permission("plans.approve"))],
):
    with database(True) as connection:
        connection.execute(text("SET LOCAL lock_timeout = 5000"))
        connection.execute(text("SET LOCAL statement_timeout = 30000"))
        connection.execute(text("SET LOCAL transaction_timeout = 60000"))
        project = project_row(connection, project_id, actor, exclusive=True)
        stages = connection.execute(text("""
            SELECT * FROM public.stages WHERE project_id = :id ORDER BY id FOR UPDATE
        """), {"id": project_id}).mappings().all()
        require_current_actor(connection, actor.id, {"plans.approve"})
        if (
            actor.id == project["student_id"]
            or actor.id not in (project["supervisor_id"], project["teacher_id"])
        ):
            raise HTTPException(403, "Изменение дат разрешает назначенный руководитель или преподаватель, не студент проекта.")
        if project["plan_approved_at"] is None:
            raise HTTPException(409, "Сначала необходимо утвердить план.")
        if not stages or all(stage["status"] == "completed" for stage in stages):
            raise HTTPException(409, "План не содержит незавершенных этапов.")
        requested = {stage.stage_id: stage for stage in data.stages}
        if set(requested) != {stage["id"] for stage in stages}:
            raise HTTPException(422, "Укажите ровно все текущие этапы этого проекта.")
        ordered = sorted(stages, key=lambda stage: stage["position"])
        changes = []
        for stage in ordered:
            proposed = requested[stage["id"]]
            if (
                proposed.expected_starts_at != stage["starts_at"]
                or proposed.expected_due_at != stage["due_at"]
            ):
                raise HTTPException(409, "Даты плана изменились после чтения. Получите актуальный план.")
            if proposed.starts_at != stage["starts_at"] or proposed.due_at != stage["due_at"]:
                if stage["status"] == "completed":
                    raise HTTPException(409, "Даты завершенного этапа нельзя изменить.")
                changes.append({
                    "stage_id": stage["id"], "position": stage["position"],
                    "before": {
                        "starts_at": utc_timestamp(stage["starts_at"]),
                        "due_at": utc_timestamp(stage["due_at"]),
                    },
                    "after": {
                        "starts_at": utc_timestamp(proposed.starts_at),
                        "due_at": utc_timestamp(proposed.due_at),
                    },
                })
        for previous, current in zip(ordered, ordered[1:]):
            if requested[current["id"]].starts_at < requested[previous["id"]].due_at:
                raise HTTPException(422, "Этапы должны следовать по порядку без пересечения сроков.")
        if changes:
            connection.execute(text("""
                UPDATE public.stages SET starts_at = :starts, due_at = :due WHERE id = :id
            """), [
                {"id": change["stage_id"], "starts": requested[change["stage_id"]].starts_at,
                 "due": requested[change["stage_id"]].due_at}
                for change in changes
            ])
            operation_id = str(uuid4())
            changed_at = connection.execute(text("SELECT clock_timestamp()")).scalar_one()
            audit(connection, actor.id, "plan_dates_changed", "projects", project_id, {
                "project_id": project_id, "operation_id": operation_id, "reason": data.reason,
                "changed_at": utc_timestamp(changed_at), "changes": changes,
            })
            recipients = sorted({
                project["student_id"], project["supervisor_id"], project["teacher_id"],
            } - {None, actor.id})
            connection.execute(text("""
                INSERT INTO public.notifications (recipient_id, project_id, event_key, message)
                SELECT id, :project, :key, :message FROM public.users
                WHERE id = ANY(:recipients) AND is_active ORDER BY id
                ON CONFLICT (recipient_id, event_key) DO NOTHING
            """), {
                "project": project_id, "key": f"plan-dates-changed:{operation_id}",
                "recipients": recipients,
                "message": f"Изменены даты этапов проекта № {project_id}. Получите актуальный план.",
            })
        return plan_output(project, read_stages(connection, project_id))


@router.get("/projects/{project_id}/status")
def get_status(
    project_id: int,
    actor: Annotated[UserPublic, Depends(require_permission("statuses.read"))],
):
    with database() as connection:
        project = project_row(connection, project_id, actor)
        stages = read_stages(connection, project_id)
        now = connection.execute(text("SELECT CURRENT_TIMESTAMP")).scalar_one()
        total = len(stages)
        completed = sum(row["status"] == "completed" for row in stages)
        status = project_status(
            project["plan_approved_at"] is not None,
            total,
            completed,
            any(row["status"] == "revision" for row in stages),
            any(row["status"] == "under_review" for row in stages),
        )
        name = PROJECT_STATUS_LABELS[status]
        progress = (
            float((Decimal(completed) * 100 / Decimal(total)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
            if total else None
        )
        stage_results = []
        for row in stages:
            item = dict(row)
            item["is_overdue"] = row["status"] != "completed" and now > row["due_at"]
            item["completed_late"] = bool(row["completed_at"] and row["completed_at"] > row["due_at"])
            stage_results.append(item)
        return {
            "project_id": project_id,
            "status": status,
            "status_name": name,
            "plan_approved": project["plan_approved_at"] is not None,
            "total_stages": total,
            "completed_stages": completed,
            "progress_percent": progress,
            "is_overdue": any(row["is_overdue"] for row in stage_results),
            "calculated_at": now,
            "stages": stage_results,
        }
