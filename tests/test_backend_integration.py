import hashlib
import io
from uuid import uuid4
from zipfile import ZipFile

import pytest
from sqlalchemy import text


def test_no_token_and_real_login(scenario):
    case = scenario
    response = case.client.get(f"/projects/{case.project_id}")
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    response = case.client.post("/auth/login", json={
        "email": case.actors["student"]["email"], "password": "wrong-test-password",
    })
    assert response.status_code == 401
    me = case.request("student", "GET", "/auth/me", 200)
    assert me["id"] == case.actors["student"]["id"]
    assert me["roles"] == ["student"]
    assert "reviews.write" not in me["permissions"]


@pytest.mark.parametrize("actor,expected", [("student", 403), ("outsider", 403), ("unassigned", 404)])
def test_review_authorization(scenario, actor, expected):
    case = scenario
    case.approve()
    material = case.upload()
    me = case.request(actor, "GET", "/auth/me", 200)
    if actor == "unassigned":
        assert "reviews.write" in me["permissions"]
    before = case.snapshot()
    case.review(material, actor=actor, status=expected)
    assert case.snapshot() == before


@pytest.mark.parametrize("operation", [
    "project", "plan", "status", "history", "download", "upload", "propose", "approve", "assign",
])
def test_other_project_is_hidden(scenario, operation):
    case = scenario
    case.approve()
    material = case.upload()
    assert case.other_project_id != case.project_id
    before = case.snapshot()
    project = f"/projects/{case.project_id}"
    stage = f"/stages/{case.stages[0]['id']}/materials"
    if operation in ("project", "plan", "status"):
        path = project + {"project": "", "plan": "/plan", "status": "/status"}[operation]
        case.request("outsider", "GET", path, 404)
    elif operation == "history":
        case.request("outsider", "GET", stage, 404)
    elif operation == "download":
        case.request("outsider", "GET", f"/materials/{material['id']}/download", 404)
    elif operation == "upload":
        case.upload(actor="outsider", status=404)
    elif operation == "propose":
        case.request("outsider", "PUT", project + "/plan", 404, json=case.plan_input)
    elif operation == "approve":
        case.request("unassigned", "POST", project + "/plan/approve", 404)
    else:
        # Supervisor has no global supervisors.assign permission.
        case.request("unassigned", "PUT", project + "/supervisor", 403, json={
            "supervisor_id": case.actors["unassigned"]["id"],
        })
    assert case.snapshot() == before


def test_plan_approval_and_conflicts(scenario):
    case = scenario
    assert not case.plan["approved"]
    assert [s["status"] for s in case.stages] == ["planned"] * 3
    before = case.snapshot()
    case.request("student", "POST", f"/projects/{case.project_id}/plan/approve", 403)
    assert case.snapshot() == before
    approved = case.approve()
    assert approved["approved"] is True
    assert approved["approved_by"] == case.actors["supervisor"]["id"]
    assert approved["approved_at"] is not None
    assert [s["status"] for s in approved["stages"]] == ["in_progress", "planned", "planned"]
    before = case.snapshot()
    case.request("supervisor", "POST", f"/projects/{case.project_id}/plan/approve", 409)
    assert case.snapshot() == before
    case.request("student", "PUT", f"/projects/{case.project_id}/plan", 409, json=case.plan_input)
    assert case.snapshot() == before


@pytest.mark.parametrize("extension", ["txt", "pdf", "docx"])
def test_upload_metadata_and_download(scenario, extension):
    case = scenario
    case.approve()
    data = "Материалы учебного проекта\n".encode("utf-8")
    media_type = "text/plain"
    if extension == "pdf":
        data = b"%PDF-1.4\nIntegration test\n%%EOF\n"
        media_type = "application/pdf"
    elif extension == "docx":
        stream = io.BytesIO()
        with ZipFile(stream, "w") as archive:
            archive.writestr("[Content_Types].xml",
                            '<Types ContentType="application/vnd.openxmlformats-officedocument.'
                            'wordprocessingml.document.main+xml"/>')
            archive.writestr("word/document.xml", "<document/>")
        data = stream.getvalue()
        media_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    material = case.upload(data=data, filename=f"report.{extension}")
    assert material["version_no"] == 1
    assert material["size_bytes"] == len(data)
    assert material["sha256"] == hashlib.sha256(data).hexdigest()
    assert material["uploaded_by"] == case.actors["student"]["id"]
    assert material["media_type"] == media_type
    assert "storage_key" not in material
    with case.engine.connect() as connection:
        row = connection.execute(text("SELECT * FROM public.material_versions WHERE id = :id"),
                                 {"id": material["id"]}).mappings().one()
    assert row["size_bytes"] == len(data)
    assert row["sha256"] == hashlib.sha256(data).hexdigest()
    assert (case.storage / row["storage_key"]).read_bytes() == data
    for actor in ("student", "supervisor", "teacher"):
        response = case.client.get(f"/materials/{material['id']}/download",
                                   headers=case.actors[actor]["headers"])
        assert response.status_code == 200
        assert response.content == data
        assert response.headers["cache-control"] == "no-store"
    assert case.status()["stages"][0]["status"] == "under_review"


@pytest.mark.parametrize("state", ["planned", "completed", "under_review"])
def test_upload_blocked_by_stage_state(scenario, state):
    case = scenario
    case.approve()
    stage = 1 if state == "planned" else 0
    if state != "planned":
        material = case.upload()
        if state == "completed":
            case.review(material)
    assert case.status()["stages"][stage]["status"] == state
    before = case.snapshot()
    case.upload(stage=stage, status=409)
    assert case.snapshot() == before


@pytest.mark.parametrize("kind,expected", [
    ("empty", 422), ("extension", 415), ("oversized", 413),
    ("invalid_utf8", 415), ("fake_pdf", 415),
])
def test_invalid_upload_is_atomic(scenario, kind, expected):
    case = scenario
    case.approve()
    data, filename = b"Valid text", "report.txt"
    if kind == "empty":
        data = b""
    elif kind == "extension":
        filename = "report.exe"
    elif kind == "oversized":
        # Exercise the actual production limit, without replacing the constant.
        from app.materials import MAX_BYTES
        data = b"a" * (MAX_BYTES + 1)
    elif kind == "invalid_utf8":
        data = b"\xff\xfe"
    else:
        filename = "report.pdf"
    before = case.snapshot()
    case.upload(data=data, filename=filename, status=expected)
    assert case.snapshot() == before
    assert case.request("student", "GET", f"/stages/{case.stages[0]['id']}/materials", 200) == []
    assert case.upload()["version_no"] == 1


@pytest.mark.parametrize("remarks", ["", "   ", "\n\t"])
def test_revision_requires_nonblank_remarks(scenario, remarks):
    case = scenario
    case.approve()
    material = case.upload()
    before = case.snapshot()
    case.review(material, decision="revision", remarks=remarks, status=422)
    assert case.snapshot() == before


def test_version_history_and_repeat_review(scenario):
    case = scenario
    case.approve()
    v1 = case.upload(data=b"Version one\n")
    review1 = case.review(v1, "revision", " Исправить требования ")
    assert review1["stage_status"] == "revision"
    assert review1["remarks"] == "Исправить требования"
    assert case.status()["stages"][0]["status"] == "revision"
    before = case.snapshot()
    case.review(v1, status=409)
    assert case.snapshot() == before
    v2 = case.upload(data=b"Version two\n")
    assert v2["version_no"] == 2
    before = case.snapshot()
    case.review(v1, status=409)
    assert case.snapshot() == before
    review2 = case.review(v2)
    assert review2["stage_status"] == "completed"
    stages = case.status()["stages"]
    assert [s["status"] for s in stages] == ["completed", "in_progress", "planned"]
    assert stages[0]["completed_at"] is not None
    assert stages[1]["completed_at"] is None and stages[2]["completed_at"] is None
    before = case.snapshot()
    case.review(v2, status=409)
    assert case.snapshot() == before
    history = case.request("student", "GET", f"/stages/{case.stages[0]['id']}/materials", 200)
    assert [row["id"] for row in history] == [v2["id"], v1["id"]]
    assert [row["version_no"] for row in history] == [2, 1]
    assert [row["decision"] for row in history] == ["accepted", "revision"]
    assert [row["review_id"] for row in history] == [review2["id"], review1["id"]]
    assert history[1]["remarks"] == "Исправить требования"
    for material, content in ((v1, b"Version one\n"), (v2, b"Version two\n")):
        response = case.client.get(f"/materials/{material['id']}/download",
                                   headers=case.actors["student"]["headers"])
        assert response.status_code == 200
        assert response.content == content


def test_only_latest_pending_version_can_be_reviewed(scenario):
    case = scenario
    case.approve()
    v1 = case.upload()
    # Normal API prevents two pending versions. Seed this SQL-valid guard case
    # explicitly, without changing schema or production state transitions.
    with case.engine.begin() as connection:
        v2 = dict(connection.execute(text("""
            INSERT INTO public.material_versions
                (stage_id, version_no, uploaded_by, original_filename, storage_key,
                 media_type, size_bytes, sha256)
            SELECT stage_id, 2, uploaded_by, original_filename, :key,
                   media_type, size_bytes, sha256
            FROM public.material_versions WHERE id = :id RETURNING *
        """), {"id": v1["id"], "key": uuid4().hex + ".bin"}).mappings().one())
    (case.storage / v2["storage_key"]).write_bytes(b"Integration material\n")
    before = case.snapshot()
    case.review(v1, status=409)
    assert case.snapshot() == before
    case.review(v2)
    with case.engine.connect() as connection:
        reviewed = connection.execute(text("""
            SELECT material_version_id FROM public.reviews WHERE material_version_id = ANY(:ids)
        """), {"ids": [v1["id"], v2["id"]]}).scalars().all()
    assert reviewed == [v2["id"]]


def test_complete_three_stage_api_journey(scenario):
    case = scenario
    case.approve()
    v1 = case.upload(data=b"First draft")
    case.review(v1, "revision", "Уточнить описание")
    v2 = case.upload(data=b"Corrected draft")
    assert v2["version_no"] == 2
    case.review(v2)
    status = case.status()
    assert status["progress_percent"] == 33.33
    assert [s["status"] for s in status["stages"]] == ["completed", "in_progress", "planned"]
    # All starts_at are in 2099; enabled stages can be worked on immediately.
    case.review(case.upload(stage=1))
    status = case.status()
    assert status["progress_percent"] == 66.67
    assert [s["status"] for s in status["stages"]] == ["completed", "completed", "in_progress"]
    case.review(case.upload(stage=2))
    status = case.status()
    assert status["progress_percent"] == 100
    assert status["status"] == "completed"
    assert status["completed_stages"] == status["total_stages"] == 3
    assert all(s["status"] == "completed" and s["completed_at"] for s in status["stages"])
    with case.engine.connect() as connection:
        dates = connection.execute(text("""
            SELECT completed_at FROM public.stages WHERE project_id = :id ORDER BY position
        """), {"id": case.project_id}).scalars().all()
    assert len(dates) == 3 and all(dates)


@pytest.mark.parametrize("decision", ["revision", "accepted"])
def test_notification_recipients_events_and_idempotence(scenario, decision):
    case = scenario
    case.approve()
    material = case.upload()
    case.review(material, decision, "Доработать" if decision == "revision" else "")
    expected = {
        "student": {f"plan-approved:{case.project_id}", f"material-reviewed:{material['id']}"},
        "supervisor": {f"material-uploaded:{material['id']}"},
        "teacher": {f"plan-approved:{case.project_id}", f"material-uploaded:{material['id']}",
                    f"material-reviewed:{material['id']}"},
        "outsider": set(), "unassigned": set(), "teacher2": set(),
        "department": set(), "teacher_department": set(), "admin": set(),
        "reports_only": set(),
    }
    for actor, keys in expected.items():
        rows = case.request(actor, "GET", "/notifications", 200)
        assert {row["event_key"] for row in rows} == keys
        assert len(rows) == len(keys)
        assert all(row["recipient_id"] == case.actors[actor]["id"] for row in rows)
        assert all(row["project_id"] == case.project_id and row["message"] for row in rows)
    notification = case.request("student", "GET", "/notifications", 200)[0]
    assert notification["read_at"] is None
    before = case.snapshot()
    case.request("outsider", "PATCH", f"/notifications/{notification['id']}/read", 404)
    assert case.snapshot() == before
    first = case.request("student", "PATCH", f"/notifications/{notification['id']}/read", 200)
    assert first["read_at"] is not None
    before = case.snapshot()
    second = case.request("student", "PATCH", f"/notifications/{notification['id']}/read", 200)
    assert second["read_at"] == first["read_at"]
    assert case.snapshot() == before
    unread = case.request("student", "GET", "/notifications?unread_only=true", 200)
    assert notification["id"] not in {row["id"] for row in unread}
    case.request("outsider", "PATCH", f"/notifications/{notification['id']}/read", 404)
    assert case.snapshot() == before
    case.review(material, status=409)
    assert case.snapshot() == before
    case.request("supervisor", "POST", f"/projects/{case.project_id}/plan/approve", 409)
    assert case.snapshot() == before
