from datetime import datetime, timezone
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.auth import UserPublic, require_permission
from app.database import engine
from app.planning import PROJECT_STATUS_LABELS, project_status_case

router = APIRouter(prefix="/reports", tags=["Отчетность"])

ProjectStatus = Literal[
    "planning", "in_progress", "under_review", "revision", "completed",
]


class ReportFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    access_scope: Literal["teacher", "department"]
    created_from: AwareDatetime | None
    created_before: AwareDatetime | None
    student_id: int | None
    supervisor_id: int | None
    teacher_id: int | None
    status: ProjectStatus | None
    overdue_only: bool

    @field_validator("created_from", "created_before")
    @classmethod
    def normalize_utc(cls, value):
        return value.astimezone(timezone.utc) if value is not None else None


class ProjectStatusCounts(BaseModel):
    planning: int
    in_progress: int
    under_review: int
    revision: int
    completed: int


class ProjectReportSummary(BaseModel):
    status_counts: ProjectStatusCounts
    overdue_projects: int
    total_stages: int
    completed_stages: int


class ProjectReportItem(BaseModel):
    project_id: int
    topic_id: int
    topic_title: str
    student_id: int
    supervisor_id: int | None
    teacher_id: int
    created_at: datetime
    plan_approved: bool
    status: ProjectStatus
    status_name: str
    total_stages: int
    completed_stages: int
    progress_percent: float | None
    is_overdue: bool


class ProjectReportResponse(BaseModel):
    generated_at: datetime
    filters: ReportFilters
    limit: int
    offset: int
    total_matching: int
    summary: ProjectReportSummary
    items: list[ProjectReportItem]


def report_now():
    return datetime.now(timezone.utc)


def report_query():
    status_case = project_status_case()
    return text(f"""
        WITH report_clock AS (
            SELECT CAST(:generated_at AS timestamptz) AS generated_at
        ),
        base_projects AS (
            SELECT p.id AS project_id, p.topic_id, t.title AS topic_title,
                   p.student_id, p.supervisor_id, p.teacher_id, p.created_at,
                   (p.plan_approved_at IS NOT NULL) AS plan_approved,
                   COUNT(s.id)::integer AS total_stages,
                   (COUNT(s.id) FILTER (WHERE s.status = 'completed'))::integer
                       AS completed_stages,
                   COALESCE(BOOL_OR(s.status = 'revision'), false) AS has_revision,
                   COALESCE(BOOL_OR(s.status = 'under_review'), false)
                       AS has_under_review,
                   COALESCE(BOOL_OR(
                       s.status <> 'completed' AND report_clock.generated_at > s.due_at
                   ), false) AS is_overdue
            FROM public.projects p
            JOIN public.topics t ON t.id = p.topic_id
            CROSS JOIN report_clock
            LEFT JOIN public.stages s ON s.project_id = p.id
            WHERE (:department OR p.teacher_id = :actor_id)
              AND (CAST(:created_from AS timestamptz) IS NULL
                   OR p.created_at >= CAST(:created_from AS timestamptz))
              AND (CAST(:created_before AS timestamptz) IS NULL
                   OR p.created_at < CAST(:created_before AS timestamptz))
              AND (CAST(:student_id AS bigint) IS NULL
                   OR p.student_id = CAST(:student_id AS bigint))
              AND (CAST(:supervisor_id AS bigint) IS NULL
                   OR p.supervisor_id = CAST(:supervisor_id AS bigint))
              AND (CAST(:teacher_id AS bigint) IS NULL
                   OR p.teacher_id = CAST(:teacher_id AS bigint))
            GROUP BY p.id, t.title, report_clock.generated_at
        ),
        project_states AS (
            SELECT base_projects.*,
                   {status_case} AS status
            FROM base_projects
        ),
        matching AS (
            SELECT * FROM project_states
            WHERE (CAST(:status AS text) IS NULL OR status = CAST(:status AS text))
              AND (NOT :overdue_only OR is_overdue)
        ),
        summary AS (
            SELECT COUNT(*)::integer AS total_matching,
                   (COUNT(*) FILTER (WHERE status = 'planning'))::integer
                       AS planning_count,
                   (COUNT(*) FILTER (WHERE status = 'in_progress'))::integer
                       AS in_progress_count,
                   (COUNT(*) FILTER (WHERE status = 'under_review'))::integer
                       AS under_review_count,
                   (COUNT(*) FILTER (WHERE status = 'revision'))::integer
                       AS revision_count,
                   (COUNT(*) FILTER (WHERE status = 'completed'))::integer
                       AS completed_count,
                   (COUNT(*) FILTER (WHERE is_overdue))::integer AS overdue_projects,
                   COALESCE(SUM(total_stages), 0)::integer AS total_stages,
                   COALESCE(SUM(completed_stages), 0)::integer AS completed_stages
            FROM matching
        ),
        page AS (
            SELECT * FROM matching
            ORDER BY project_id
            LIMIT :limit OFFSET :offset
        )
        SELECT report_clock.generated_at, summary.*,
               page.project_id, page.topic_id, page.topic_title,
               page.student_id, page.supervisor_id, page.teacher_id,
               page.created_at, page.plan_approved, page.status,
               page.total_stages AS item_total_stages,
               page.completed_stages AS item_completed_stages,
               CASE WHEN page.total_stages = 0 THEN NULL
                    ELSE ROUND(
                        page.completed_stages::numeric * 100 / page.total_stages, 2
                    )::double precision
               END AS progress_percent,
               page.is_overdue
        FROM report_clock
        CROSS JOIN summary
        LEFT JOIN page ON true
        ORDER BY page.project_id
    """)


@router.get(
    "/projects",
    response_model=ProjectReportResponse,
    summary="Сводный отчет по учебным проектам",
    description=(
        "Отчет показывает текущее состояние проектов, созданных в выбранном "
        "периоде projects.created_at; он не восстанавливает историческое состояние. "
        "Преподаватель видит только собственные проекты, представитель кафедры — "
        "проекты общего пространства. При обеих ролях применяется доступ кафедры. "
        "Все агрегаты относятся к отфильтрованному набору до пагинации."
    ),
)
def get_project_report(
    actor: Annotated[UserPublic, Depends(require_permission("reports.read"))],
    created_from: Annotated[
        AwareDatetime | None,
        Query(description="Нижняя граница projects.created_at включительно; нужен часовой пояс."),
    ] = None,
    created_before: Annotated[
        AwareDatetime | None,
        Query(description="Верхняя граница projects.created_at исключительно; нужен часовой пояс."),
    ] = None,
    student_id: Annotated[int | None, Query(gt=0, description="Положительный ID студента.")] = None,
    supervisor_id: Annotated[
        int | None, Query(gt=0, description="Положительный ID руководителя.")
    ] = None,
    teacher_id: Annotated[
        int | None, Query(gt=0, description="Положительный ID преподавателя.")
    ] = None,
    status: Annotated[
        ProjectStatus | None,
        Query(description="Текущий статус проекта."),
    ] = None,
    overdue_only: Annotated[
        bool, Query(description="Оставить проекты с просроченным незавершенным этапом.")
    ] = False,
    limit: Annotated[int, Query(ge=1, le=100, description="Размер страницы.")] = 50,
    offset: Annotated[int, Query(ge=0, description="Смещение страницы.")] = 0,
):
    department = "department" in actor.roles
    if not department and "teacher" not in actor.roles:
        raise HTTPException(403, "Недостаточно прав для выполнения операции.")
    if created_from is not None:
        created_from = created_from.astimezone(timezone.utc)
    if created_before is not None:
        created_before = created_before.astimezone(timezone.utc)
    if created_from is not None and created_before is not None and created_from >= created_before:
        raise HTTPException(422, "Начало периода должно быть раньше его окончания.")

    filters = ReportFilters(
        access_scope="department" if department else "teacher",
        created_from=created_from,
        created_before=created_before,
        student_id=student_id,
        supervisor_id=supervisor_id,
        teacher_id=teacher_id,
        status=status,
        overdue_only=overdue_only,
    )
    parameters = {
        **filters.model_dump(),
        "generated_at": report_now(),
        "actor_id": actor.id,
        "department": department,
        "limit": limit,
        "offset": offset,
    }

    try:
        with engine.connect().execution_options(
            isolation_level="REPEATABLE READ"
        ) as connection:
            with connection.begin():
                connection.execute(text("SET TRANSACTION READ ONLY"))
                rows = connection.execute(report_query(), parameters).mappings().all()
    except SQLAlchemyError:
        raise HTTPException(503, "База данных временно недоступна.") from None

    first = rows[0]
    items = [
        ProjectReportItem(
            project_id=row["project_id"],
            topic_id=row["topic_id"],
            topic_title=row["topic_title"],
            student_id=row["student_id"],
            supervisor_id=row["supervisor_id"],
            teacher_id=row["teacher_id"],
            created_at=row["created_at"],
            plan_approved=row["plan_approved"],
            status=row["status"],
            status_name=PROJECT_STATUS_LABELS[row["status"]],
            total_stages=row["item_total_stages"],
            completed_stages=row["item_completed_stages"],
            progress_percent=row["progress_percent"],
            is_overdue=row["is_overdue"],
        )
        for row in rows
        if row["project_id"] is not None
    ]
    return ProjectReportResponse(
        generated_at=first["generated_at"],
        filters=filters,
        limit=limit,
        offset=offset,
        total_matching=first["total_matching"],
        summary=ProjectReportSummary(
            status_counts=ProjectStatusCounts(
                planning=first["planning_count"],
                in_progress=first["in_progress_count"],
                under_review=first["under_review_count"],
                revision=first["revision_count"],
                completed=first["completed_count"],
            ),
            overdue_projects=first["overdue_projects"],
            total_stages=first["total_stages"],
            completed_stages=first["completed_stages"],
        ),
        items=items,
    )
