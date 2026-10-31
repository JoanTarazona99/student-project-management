import codecs
import hashlib
import logging
import re
from pathlib import Path
from typing import Annotated, Literal
from uuid import uuid4
from zipfile import BadZipFile, ZipFile

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import text

from app.auth import UserPublic, get_current_user, require_permission
from app.planning import project_row
from app.projects import audit, database

router = APIRouter(tags=["Материалы, проверка и уведомления"])
UPLOAD_ROOT = Path(__file__).resolve().parents[1] / "uploads"
MAX_BYTES = 20 * 1024 * 1024
CHUNK = 64 * 1024
TYPES = {
    ".txt": "text/plain",
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
PUBLIC_COLUMNS = """
    id, stage_id, version_no, uploaded_by, original_filename,
    media_type, size_bytes, sha256, uploaded_at
"""
logger = logging.getLogger(__name__)


def safe_name(value):
    name = (value or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    if not name or len(name) > 255 or any(ord(c) < 32 or ord(c) == 127 for c in name):
        raise HTTPException(422, "Некорректное имя файла.")
    if Path(name).suffix.lower() not in TYPES:
        raise HTTPException(415, "Допустимы только TXT (UTF-8), PDF и DOCX.")
    return name


def copy_limited(stream, target):
    size = 0
    digest = hashlib.sha256()
    with target.open("xb") as output:
        while data := stream.read(CHUNK):
            size += len(data)
            if size > MAX_BYTES:
                raise HTTPException(413, "Максимальный размер файла — 20 МиБ.")
            output.write(data)
            digest.update(data)
    if size == 0:
        raise HTTPException(422, "Нельзя загрузить пустой файл.")
    return size, digest.hexdigest()


def validate_content(path, extension):
    if extension == ".txt":
        decoder = codecs.getincrementaldecoder("utf-8-sig")("strict")
        try:
            with path.open("rb") as stream:
                while chunk := stream.read(CHUNK):
                    value = decoder.decode(chunk)
                    if "\x00" in value:
                        raise ValueError("binary text")
                decoder.decode(b"", final=True)
        except (UnicodeDecodeError, ValueError):
            raise HTTPException(415, "TXT должен содержать текст в кодировке UTF-8.") from None
    elif extension == ".pdf":
        with path.open("rb") as stream:
            header = stream.read(5)
            stream.seek(max(0, path.stat().st_size - 1024))
            tail = stream.read()
        if header != b"%PDF-" or b"%%EOF" not in tail:
            raise HTTPException(415, "Файл не имеет ожидаемых признаков PDF.")
    else:
        try:
            with ZipFile(path) as archive:
                entries = archive.infolist()
                names = {entry.filename for entry in entries}
                if len(entries) > 2000 or sum(entry.file_size for entry in entries) > 100 * 1024 * 1024:
                    raise ValueError("archive too large")
                if any(entry.flag_bits & 1 for entry in entries):
                    raise ValueError("encrypted archive")
                if not {"[Content_Types].xml", "word/document.xml"}.issubset(names):
                    raise ValueError("not docx")
                if any("vbaproject" in name.lower() for name in names):
                    raise ValueError("macros")
                info = archive.getinfo("[Content_Types].xml")
                if info.file_size > 1024 * 1024:
                    raise ValueError("types too large")
                types = archive.read(info)
                if b"application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml" not in types:
                    raise ValueError("not ordinary docx")
        except (BadZipFile, KeyError, ValueError, RuntimeError, NotImplementedError):
            raise HTTPException(415, "Ожидается обычный незашифрованный DOCX без VBA.") from None


def stage_context(connection, stage_id, actor, write=False):
    project_id = connection.execute(text(
        "SELECT project_id FROM public.stages WHERE id = :id"
    ), {"id": stage_id}).scalar_one_or_none()
    if project_id is None:
        raise HTTPException(404, "Этап не найден или недоступен.")
    project = project_row(connection, project_id, actor, exclusive=write)
    stage = connection.execute(text(
        "SELECT * FROM public.stages WHERE id = :id" + (" FOR UPDATE" if write else "")
    ), {"id": stage_id}).mappings().one_or_none()
    if stage is None:
        raise HTTPException(404, "Этап не найден или недоступен.")
    return project, stage


def notify(connection, project, actor_id, event_key, message, recipients=None):
    if recipients is None:
        recipients = {project["student_id"], project["supervisor_id"], project["teacher_id"]}
    for recipient in sorted(set(recipients) - {None, actor_id}):
        connection.execute(text("""
            INSERT INTO public.notifications (recipient_id, project_id, event_key, message)
            VALUES (:recipient, :project, :key, :message)
            ON CONFLICT (recipient_id, event_key) DO NOTHING
        """), {"recipient": recipient, "project": project["id"], "key": event_key, "message": message})


def remove_temporary(path):
    if path is not None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.exception("Не удалось удалить временный файл загрузки")


@router.post("/stages/{stage_id}/materials", status_code=201)
def upload_material(
    stage_id: int,
    actor: Annotated[UserPublic, Depends(require_permission("materials.upload"))],
    file: Annotated[UploadFile, File()],
):
    temporary = final = None
    inserted = False
    try:
        name = safe_name(file.filename)
        extension = Path(name).suffix.lower()
        with database(True) as connection:
            project, stage = stage_context(connection, stage_id, actor, write=True)
            if actor.id != project["student_id"]:
                raise HTTPException(403, "Материалы загружает студент этого проекта.")
            if project["plan_approved_at"] is None:
                raise HTTPException(409, "Сначала необходимо утвердить план.")
            if stage["status"] not in ("in_progress", "revision"):
                raise HTTPException(409, "Загрузка доступна только для текущего этапа или после возврата на доработку.")
            blocked = connection.execute(text("""
                SELECT EXISTS (SELECT 1 FROM public.stages
                WHERE project_id = :project AND position < :position AND status <> 'completed')
            """), {"project": project["id"], "position": stage["position"]}).scalar_one()
            if blocked:
                raise HTTPException(409, "Сначала завершите предыдущие этапы.")
            version = connection.execute(text("""
                SELECT COALESCE(MAX(version_no), 0) + 1
                FROM public.material_versions WHERE stage_id = :stage
            """), {"stage": stage_id}).scalar_one()
            UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
            key = uuid4().hex + ".bin"
            temporary = UPLOAD_ROOT / (key + ".part")
            final = UPLOAD_ROOT / key
            file.file.seek(0)
            size, digest = copy_limited(file.file, temporary)
            validate_content(temporary, extension)
            temporary.replace(final)
            row = connection.execute(text("""
                INSERT INTO public.material_versions
                    (stage_id, version_no, uploaded_by, original_filename,
                     storage_key, media_type, size_bytes, sha256)
                VALUES (:stage, :version, :actor, :name, :key, :type, :size, :sha)
                RETURNING """ + PUBLIC_COLUMNS), {
                    "stage": stage_id, "version": version, "actor": actor.id,
                    "name": name, "key": key, "type": TYPES[extension], "size": size, "sha": digest,
                }).mappings().one()
            inserted = True
            connection.execute(text("UPDATE public.stages SET status = 'under_review' WHERE id = :id"), {"id": stage_id})
            audit(connection, actor.id, "material_uploaded", "material_versions", row["id"],
                  {"project_id": project["id"], "stage_id": stage_id, "version_no": version, "sha256": digest})
            notify(connection, project, actor.id, f"material-uploaded:{row['id']}",
                   f"На проверку поступила версия {version} материалов этапа «{stage['title']}».",
                   {project["supervisor_id"], project["teacher_id"]})
            result = dict(row)
        return result
    except OSError:
        raise HTTPException(503, "Файловое хранилище временно недоступно.") from None
    finally:
        file.file.close()
        remove_temporary(temporary)
        # Preserve the file if commit outcome may be uncertain; orphan reconciliation is a separate task.
        if not inserted:
            remove_temporary(final)


@router.get("/stages/{stage_id}/materials")
def list_materials(
    stage_id: int,
    actor: Annotated[UserPublic, Depends(require_permission("projects.read"))],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    with database() as connection:
        stage_context(connection, stage_id, actor)
        return connection.execute(text("""
            SELECT mv.id, mv.stage_id, mv.version_no, mv.uploaded_by,
                   mv.original_filename, mv.media_type, mv.size_bytes, mv.sha256, mv.uploaded_at,
                   r.id AS review_id, r.reviewer_id, r.decision, r.remarks, r.reviewed_at
            FROM public.material_versions mv
            LEFT JOIN public.reviews r ON r.material_version_id = mv.id
            WHERE mv.stage_id = :stage
            ORDER BY mv.version_no DESC LIMIT :limit OFFSET :offset
        """), {"stage": stage_id, "limit": limit, "offset": offset}).mappings().all()


@router.get("/materials/{material_id}/download")
def download_material(
    material_id: int,
    actor: Annotated[UserPublic, Depends(require_permission("projects.read"))],
):
    with database() as connection:
        row = connection.execute(text("SELECT * FROM public.material_versions WHERE id = :id"),
                                 {"id": material_id}).mappings().one_or_none()
        if row is None:
            raise HTTPException(404, "Версия не найдена или недоступна.")
        stage_context(connection, row["stage_id"], actor)
        if not re.fullmatch(r"[0-9a-f]{32}\.bin", row["storage_key"]):
            raise HTTPException(503, "Некорректная запись файлового хранилища.")
        path = UPLOAD_ROOT / row["storage_key"]
        if not path.is_file():
            raise HTTPException(503, "Файл временно недоступен в хранилище.")
        return FileResponse(path, filename=row["original_filename"], media_type=row["media_type"],
                            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


class ReviewInput(BaseModel):
    decision: Literal["accepted", "revision"]
    remarks: str = Field(default="", max_length=10000)

    @model_validator(mode="after")
    def validate_remarks(self):
        self.remarks = self.remarks.strip()
        if self.decision == "revision" and not self.remarks:
            raise ValueError("При возврате на доработку укажите замечания")
        return self


@router.post("/materials/{material_id}/review", status_code=201)
def review_material(
    material_id: int,
    data: ReviewInput,
    actor: Annotated[UserPublic, Depends(require_permission("reviews.write"))],
):
    with database(True) as connection:
        material = connection.execute(text("SELECT * FROM public.material_versions WHERE id = :id"),
                                      {"id": material_id}).mappings().one_or_none()
        if material is None:
            raise HTTPException(404, "Версия не найдена или недоступна.")
        project, stage = stage_context(connection, material["stage_id"], actor, write=True)
        if actor.id == project["student_id"] or actor.id not in (project["supervisor_id"], project["teacher_id"]):
            raise HTTPException(403, "Проверку выполняет назначенный руководитель или преподаватель.")
        if project["plan_approved_at"] is None:
            raise HTTPException(409, "План не утвержден.")
        reviewed = connection.execute(text("SELECT 1 FROM public.reviews WHERE material_version_id = :id"),
                                      {"id": material_id}).scalar_one_or_none()
        if reviewed:
            raise HTTPException(409, "Эта версия уже проверена.")
        latest = connection.execute(text("""
            SELECT id FROM public.material_versions WHERE stage_id = :stage
            ORDER BY version_no DESC LIMIT 1
        """), {"stage": stage["id"]}).scalar_one()
        if latest != material_id or stage["status"] != "under_review":
            raise HTTPException(409, "Можно проверить только последнюю версию этапа на проверке.")
        next_stage = None
        if data.decision == "accepted":
            next_stage = connection.execute(text("""
                SELECT id, status FROM public.stages
                WHERE project_id = :project AND position > :position
                ORDER BY position LIMIT 1 FOR UPDATE
            """), {"project": project["id"], "position": stage["position"]}).mappings().one_or_none()
            if next_stage and next_stage["status"] != "planned":
                raise HTTPException(409, "Следующий этап имеет неожиданное состояние.")
        review = connection.execute(text("""
            INSERT INTO public.reviews (material_version_id, reviewer_id, decision, remarks)
            VALUES (:material, :actor, :decision, :remarks) RETURNING *
        """), {"material": material_id, "actor": actor.id,
                 "decision": data.decision, "remarks": data.remarks}).mappings().one()
        if data.decision == "accepted":
            connection.execute(text("""
                UPDATE public.stages SET status = 'completed', completed_at = CURRENT_TIMESTAMP
                WHERE id = :id
            """), {"id": stage["id"]})
            if next_stage:
                connection.execute(text("UPDATE public.stages SET status = 'in_progress' WHERE id = :id"),
                                   {"id": next_stage["id"]})
        else:
            connection.execute(text("UPDATE public.stages SET status = 'revision' WHERE id = :id"), {"id": stage["id"]})
        audit(connection, actor.id, "material_reviewed", "reviews", review["id"],
              {"project_id": project["id"], "stage_id": stage["id"],
               "material_version_id": material_id, "decision": data.decision,
               "next_stage_id": next_stage["id"] if next_stage else None})
        message = "Материалы приняты" if data.decision == "accepted" else "Материалы возвращены на доработку"
        notify(connection, project, actor.id, f"material-reviewed:{material_id}",
               f"{message}: этап «{stage['title']}», версия {material['version_no']}.")
        result = dict(review)
        result["stage_id"] = stage["id"]
        result["stage_status"] = "completed" if data.decision == "accepted" else "revision"
        result["next_stage_id"] = next_stage["id"] if next_stage else None
    return result


@router.get("/notifications")
def list_notifications(
    actor: Annotated[UserPublic, Depends(get_current_user)],
    unread_only: bool = False,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    with database() as connection:
        return connection.execute(text("""
            SELECT id, recipient_id, project_id, event_key, message, created_at, read_at
            FROM public.notifications WHERE recipient_id = :actor
            AND (NOT :unread OR read_at IS NULL)
            ORDER BY created_at DESC, id DESC LIMIT :limit OFFSET :offset
        """), {"actor": actor.id, "unread": unread_only, "limit": limit, "offset": offset}).mappings().all()


@router.patch("/notifications/{notification_id}/read")
def mark_notification_read(
    notification_id: int,
    actor: Annotated[UserPublic, Depends(get_current_user)],
):
    with database(True) as connection:
        row = connection.execute(text("""
            UPDATE public.notifications SET read_at = COALESCE(read_at, CURRENT_TIMESTAMP)
            WHERE id = :id AND recipient_id = :actor RETURNING *
        """), {"id": notification_id, "actor": actor.id}).mappings().one_or_none()
        if row is None:
            raise HTTPException(404, "Уведомление не найдено или недоступно.")
        result = dict(row)
    return result
