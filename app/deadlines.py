from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Literal

from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

from app.audit import audit


DeadlineKind = Literal["upcoming", "overdue"]
LOCK_TIMEOUT_MS = 5_000
STATEMENT_TIMEOUT_MS = 30_000
TRANSACTION_TIMEOUT_MS = 60_000


def utc_timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Нужен момент времени с часовым поясом.")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def deadline_kind(reference: datetime, due: datetime, hours: int) -> DeadlineKind | None:
    if reference > due:
        return "overdue"
    if reference < due <= reference + timedelta(hours=hours):
        return "upcoming"
    return None


def event_key(kind: DeadlineKind, stage_id: int, due: datetime) -> str:
    return f"deadline:{kind}:{stage_id}:{utc_timestamp(due)}"


@dataclass
class Counts:
    stages_examined: int = 0
    upcoming: int = 0
    overdue: int = 0
    existing: int = 0
    inactive_recipients: int = 0


@dataclass
class ProcessingError:
    project_id: int | None
    code: str
    outcome: str


@dataclass
class DeadlineResult:
    reference_time: datetime
    apply: bool
    counts: Counts = field(default_factory=Counts)
    projects_examined: int = 0
    successful_projects: list[int] = field(default_factory=list)
    errors: list[ProcessingError] = field(default_factory=list)


def database_error(error: SQLAlchemyError, project_id: int | None) -> ProcessingError:
    original = getattr(error, "orig", None)
    code = getattr(original, "sqlstate", None) or "database_unavailable"
    # Connection loss around COMMIT cannot prove whether PostgreSQL committed.
    uncertain = getattr(error, "connection_invalidated", False) or code.startswith("08")
    return ProcessingError(project_id, code, "unknown" if uncertain else "rolled_back")


def process_project(connection, project_id, reference, warning_hours, apply):
    counts = Counts()
    project = connection.execute(text(
        "SELECT * FROM public.projects WHERE id = :id" + (" FOR UPDATE" if apply else "")
    ), {"id": project_id}).mappings().one_or_none()
    if project is None or project["plan_approved_at"] is None:
        return counts
    stages = connection.execute(text("""
        SELECT id, project_id, title, due_at, status
        FROM public.stages WHERE project_id = :id ORDER BY id
    """ + (" FOR UPDATE" if apply else "")), {"id": project_id}).mappings().all()
    recipients = sorted({
        project["student_id"], project["supervisor_id"], project["teacher_id"],
    } - {None})
    users = connection.execute(text("""
        SELECT id, is_active FROM public.users WHERE id = ANY(:ids) ORDER BY id
    """), {"ids": recipients}).mappings().all()
    active = [user["id"] for user in users if user["is_active"]]
    inactive = sum(not user["is_active"] for user in users)
    for stage in stages:
        counts.stages_examined += 1
        kind = deadline_kind(reference, stage["due_at"], warning_hours)
        if stage["status"] == "completed" or kind is None:
            continue
        counts.inactive_recipients += inactive
        key = event_key(kind, stage["id"], stage["due_at"])
        if not apply:
            existing = connection.execute(text("""
                SELECT recipient_id FROM public.notifications
                WHERE recipient_id = ANY(:ids) AND event_key = :key
            """), {"ids": active, "key": key}).scalars().all()
            counts.existing += len(existing)
            setattr(counts, kind, getattr(counts, kind) + len(active) - len(existing))
            continue
        label = "Срок этапа приближается" if kind == "upcoming" else "Срок этапа истек"
        message = (
            f"{label}: проект № {project_id}, этап «{stage['title']}» "
            f"(№ {stage['id']}), срок {utc_timestamp(stage['due_at'])}."
        )
        inserted = []
        for recipient in active:
            notification_id = connection.execute(text("""
                INSERT INTO public.notifications (recipient_id, project_id, event_key, message)
                VALUES (:recipient, :project, :key, :message)
                ON CONFLICT (recipient_id, event_key) DO NOTHING RETURNING id
            """), {
                "recipient": recipient, "project": project_id, "key": key, "message": message,
            }).scalar_one_or_none()
            if notification_id is not None:
                inserted.append(notification_id)
            else:
                counts.existing += 1
        setattr(counts, kind, getattr(counts, kind) + len(inserted))
        if inserted:
            audit(connection, None, "deadline_notifications_generated", "stages", stage["id"], {
                "project_id": project_id, "stage_id": stage["id"], "kind": kind,
                "due_at": utc_timestamp(stage["due_at"]),
                "reference_time": utc_timestamp(reference),
                "notification_ids": inserted, "count": len(inserted),
            })
    return counts


def check_deadlines(engine: Engine, *, apply: bool = False, warning_hours: int = 24,
                    reference_time: datetime | None = None) -> DeadlineResult:
    if type(warning_hours) is not int or not 1 <= warning_hours <= 168:
        raise ValueError("warning-hours должен быть целым числом от 1 до 168.")
    reference = reference_time if reference_time is not None else datetime.now(timezone.utc)
    utc_timestamp(reference)
    reference = reference.astimezone(timezone.utc)
    result = DeadlineResult(reference, apply)
    try:
        with engine.connect() as connection, connection.begin():
            connection.execute(text("SET TRANSACTION READ ONLY"))
            connection.execute(text(f"SET LOCAL statement_timeout = {STATEMENT_TIMEOUT_MS}"))
            project_ids = connection.execute(text(
                "SELECT id FROM public.projects ORDER BY id"
            )).scalars().all()
    except SQLAlchemyError as error:
        result.errors.append(database_error(error, None))
        return result

    for project_id in project_ids:
        result.projects_examined += 1
        try:
            isolation = "READ COMMITTED" if apply else "REPEATABLE READ"
            with engine.connect().execution_options(isolation_level=isolation) as connection:
                with connection.begin():
                    if not apply:
                        connection.execute(text("SET TRANSACTION READ ONLY"))
                    connection.execute(text(f"SET LOCAL lock_timeout = {LOCK_TIMEOUT_MS}"))
                    connection.execute(text(f"SET LOCAL statement_timeout = {STATEMENT_TIMEOUT_MS}"))
                    connection.execute(text(f"SET LOCAL transaction_timeout = {TRANSACTION_TIMEOUT_MS}"))
                    counts = process_project(connection, project_id, reference, warning_hours, apply)
            for name in Counts.__dataclass_fields__:
                setattr(result.counts, name, getattr(result.counts, name) + getattr(counts, name))
            result.successful_projects.append(project_id)
        except SQLAlchemyError as error:
            result.errors.append(database_error(error, project_id))
    return result
