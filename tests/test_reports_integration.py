from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import text


UTC = timezone.utc
REFERENCE_TIME = datetime(2026, 1, 10, 12, 0, tzinfo=UTC)
KNOWN_STATUSES = {"planning", "in_progress", "under_review", "revision", "completed"}


@pytest.fixture
def report_time(backend, monkeypatch):
    import app.reports

    monkeypatch.setattr(app.reports, "report_now", lambda: REFERENCE_TIME)
    return REFERENCE_TIME


def report(case, actor="teacher", expected=200, **params):
    response = case.client.get(
        "/reports/projects",
        headers=case.actors[actor]["headers"],
        params=params,
    )
    assert response.status_code == expected, response.text
    return response.json() if expected == 200 else response


def configured_project(case, statuses, *, teacher="teacher", due_at=None, completed_at=None):
    project_id = case.create_project("student", "supervisor", teacher)
    stages = [
        {
            "title": f"Report stage {index}",
            "starts_at": f"2099-03-{index:02}T00:00:00Z",
            "due_at": f"2099-03-{index + 1:02}T00:00:00Z",
        }
        for index in range(1, len(statuses) + 1)
    ]
    proposed = case.request(
        "student", "PUT", f"/projects/{project_id}/plan", 200, json={"stages": stages},
    )
    case.request("supervisor", "POST", f"/projects/{project_id}/plan/approve", 200)
    due_dates = due_at or [REFERENCE_TIME + timedelta(days=1)] * len(statuses)
    completed_dates = completed_at or [
        REFERENCE_TIME - timedelta(days=1) if status == "completed" else None
        for status in statuses
    ]
    with case.engine.begin() as connection:
        for stage, status, due, completed in zip(
            proposed["stages"], statuses, due_dates, completed_dates, strict=True,
        ):
            connection.execute(text("""
                UPDATE public.stages
                SET status = :status, due_at = :due,
                    starts_at = LEAST(starts_at, :due - INTERVAL '1 second'),
                    completed_at = :completed
                WHERE id = :id
            """), {
                "status": status, "due": due, "completed": completed, "id": stage["id"],
            })
    return project_id


def set_created_at(case, project_id, value):
    with case.engine.begin() as connection:
        connection.execute(text("""
            UPDATE public.projects SET created_at = :created WHERE id = :id
        """), {"created": value, "id": project_id})


def test_report_authentication_permission_and_role_scope(scenario, report_time):
    case = scenario
    own_period = datetime(2040, 1, 1, tzinfo=UTC)
    set_created_at(case, case.project_id, own_period)
    set_created_at(case, case.other_project_id, own_period + timedelta(seconds=1))
    assert case.client.get("/reports/projects").status_code == 401
    for actor in ("student", "supervisor", "admin"):
        response = case.client.get(
            "/reports/projects", headers=case.actors[actor]["headers"],
        )
        assert response.status_code == 403
    # This account has reports.read, but no teacher/department scope.
    assert "reports.read" in case.request("reports_only", "GET", "/auth/me", 200)["permissions"]
    assert report(case, "reports_only", expected=403).status_code == 403

    teacher_result = report(case, "teacher")
    assert [row["project_id"] for row in teacher_result["items"]] == [case.project_id]
    assert teacher_result["total_matching"] == 1
    second_teacher_result = report(case, "teacher2")
    assert [row["project_id"] for row in second_teacher_result["items"]] == [case.other_project_id]
    scoped_period = {
        "created_from": own_period.isoformat(),
        "created_before": (own_period + timedelta(days=1)).isoformat(),
    }
    department_result = report(case, "department", **scoped_period)
    assert {row["project_id"] for row in department_result["items"]} == {
        case.project_id, case.other_project_id,
    }
    assert department_result["filters"]["access_scope"] == "department"
    multi_role_result = report(case, "teacher_department", **scoped_period)
    assert multi_role_result["total_matching"] == department_result["total_matching"]
    assert multi_role_result["filters"]["access_scope"] == "department"
    openapi = case.client.get("/openapi.json").json()
    assert "текущее состояние" in openapi["paths"]["/reports/projects"]["get"]["description"]

    # A filter for another teacher's project narrows, but never expands, scope.
    hidden = report(case, "teacher", student_id=case.actors["outsider"]["id"])
    assert hidden["items"] == []
    assert hidden["total_matching"] == 0
    assert all(value == 0 for value in hidden["summary"]["status_counts"].values())
    assert hidden["summary"]["total_stages"] == 0
    for filters in (
        {"teacher_id": case.actors["teacher2"]["id"]},
        {"supervisor_id": case.actors["unassigned"]["id"]},
    ):
        filtered_foreign = report(case, "teacher", **filters)
        assert filtered_foreign["items"] == []
        assert filtered_foreign["total_matching"] == 0
        assert all(
            value == 0
            for value in filtered_foreign["summary"]["status_counts"].values()
        )


def test_project_filters_and_unmatched_identity_do_not_leak(scenario, report_time):
    case = scenario
    matching = report(case, "teacher", student_id=case.actors["student"]["id"])
    assert [item["project_id"] for item in matching["items"]] == [case.project_id]
    matching = report(case, "teacher", supervisor_id=case.actors["supervisor"]["id"])
    assert [item["project_id"] for item in matching["items"]] == [case.project_id]
    matching = report(case, "teacher", teacher_id=case.actors["teacher"]["id"])
    assert [item["project_id"] for item in matching["items"]] == [case.project_id]
    missing_id = 9_000_000_001
    assert report(case, "teacher", student_id=missing_id)["total_matching"] == 0
    assert report(case, "teacher", supervisor_id=missing_id)["total_matching"] == 0
    assert report(case, "teacher", teacher_id=missing_id)["total_matching"] == 0
    assert report(case, "teacher", student_id=0, expected=422).status_code == 422
    assert report(case, "teacher", supervisor_id=-1, expected=422).status_code == 422
    assert report(case, "teacher", teacher_id="not-an-id", expected=422).status_code == 422
    assert report(case, "teacher", limit=0, expected=422).status_code == 422
    assert report(case, "teacher", limit=101, expected=422).status_code == 422
    assert report(case, "teacher", offset=-1, expected=422).status_code == 422
    assert report(case, "teacher", status="unknown", expected=422).status_code == 422


def test_created_period_is_utc_normalized_inclusive_exclusive(scenario, report_time):
    case = scenario
    at_start = REFERENCE_TIME.replace(hour=0, minute=0)
    at_end = at_start + timedelta(days=2)
    at_middle = at_start + timedelta(days=1)
    set_created_at(case, case.project_id, at_start)
    set_created_at(case, case.other_project_id, at_middle)
    extra_id = case.create_project("student", "supervisor", "teacher")
    set_created_at(case, extra_id, at_end)

    result = report(
        case, "department",
        created_from="2026-01-09T19:00:00-05:00",
        created_before="2026-01-12T02:00:00+02:00",
    )
    assert {item["project_id"] for item in result["items"]} == {
        case.project_id, case.other_project_id,
    }
    assert result["total_matching"] == 2
    assert datetime.fromisoformat(
        result["filters"]["created_from"].replace("Z", "+00:00")
    ) == at_start
    assert datetime.fromisoformat(
        result["filters"]["created_before"].replace("Z", "+00:00")
    ) == at_end

    # A lower bound includes a project exactly at the boundary; upper is exclusive.
    lower_only = report(case, "teacher", created_from=at_start.isoformat())
    assert {item["project_id"] for item in lower_only["items"]} == {
        case.project_id, extra_id,
    }
    upper_only = report(case, "department", created_before=at_start.isoformat())
    assert upper_only["items"] == []
    assert upper_only["total_matching"] == 0


@pytest.mark.parametrize("params", [
    {"created_from": "2026-01-11T00:00:00Z", "created_before": "2026-01-11T00:00:00Z"},
    {"created_from": "2026-01-12T00:00:00Z", "created_before": "2026-01-11T00:00:00Z"},
    {"created_from": "2026-01-10T00:00:00"},
    {"created_before": "2026-01-10T00:00:00"},
])
def test_invalid_period_is_rejected(scenario, report_time, params):
    assert report(scenario, "teacher", expected=422, **params).status_code == 422


def test_state_progress_and_labels_match_existing_status_endpoint(scenario, report_time):
    case = scenario
    ids_by_completion_count = []
    for completed in range(4):
        statuses = ["completed"] * completed + ["in_progress"] * (3 - completed)
        completed_at = [REFERENCE_TIME - timedelta(days=1)] * completed + [None] * (3 - completed)
        ids_by_completion_count.append(configured_project(
            case, statuses, completed_at=completed_at,
        ))
    result = report(case, "teacher")
    rows = {item["project_id"]: item for item in result["items"]}
    expected_percent = [0.0, 33.33, 66.67, 100.0]
    for project_id, expected in zip(ids_by_completion_count, expected_percent, strict=True):
        item = rows[project_id]
        status = case.request("student", "GET", f"/projects/{project_id}/status", 200)
        assert item["status"] == status["status"]
        assert item["status_name"] == status["status_name"]
        assert item["progress_percent"] == status["progress_percent"] == expected

    no_plan = rows[case.project_id]
    no_stages = report(case, "teacher2")["items"][0]
    assert no_plan["status"] == "planning" and no_plan["progress_percent"] == 0.0
    assert no_stages["status"] == "planning"
    assert no_stages["total_stages"] == no_stages["completed_stages"] == 0
    assert no_stages["progress_percent"] is None
    assert set(result["summary"]["status_counts"]) == KNOWN_STATUSES


def test_status_filter_uses_existing_priority_and_counts_all_matches(scenario, report_time):
    case = scenario
    mixed_id = configured_project(case, ["under_review", "revision", "in_progress"])
    completed_id = configured_project(case, ["completed", "completed", "completed"])
    result = report(case, "teacher", status="revision")
    assert [item["project_id"] for item in result["items"]] == [mixed_id]
    assert result["summary"]["status_counts"]["revision"] == 1
    assert report(case, "teacher", status="completed")["items"][0]["project_id"] == completed_id
    assert report(case, "teacher", status="planning")["total_matching"] == 1
    assert report(case, "teacher", status="in_progress")["total_matching"] == 0


def test_overdue_is_strict_and_completed_late_does_not_count(scenario, report_time):
    due_past = REFERENCE_TIME - timedelta(seconds=1)
    due_equal = REFERENCE_TIME
    completed_late_project = configured_project(
        scenario, ["completed", "in_progress"],
        due_at=[due_past, REFERENCE_TIME + timedelta(days=1)],
        completed_at=[REFERENCE_TIME + timedelta(seconds=1), None],
    )
    pending_overdue_project = configured_project(
        scenario, ["revision"], due_at=[due_past],
    )
    exactly_due = configured_project(
        scenario, ["in_progress"], due_at=[due_equal],
    )
    overdue = report(scenario, "teacher", overdue_only=True)
    assert [item["project_id"] for item in overdue["items"]] == [pending_overdue_project]
    item = next(item for item in report(scenario, "teacher")["items"]
                if item["project_id"] == completed_late_project)
    assert item["is_overdue"] is False
    equality_item = next(item for item in report(scenario, "teacher")["items"]
                         if item["project_id"] == exactly_due)
    assert equality_item["is_overdue"] is False
    assert report(scenario, "teacher", overdue_only="false")["total_matching"] > 0
    assert report(scenario, "teacher", overdue_only=True, status="completed")["total_matching"] == 0


def test_summary_and_matching_count_precede_pagination(scenario, report_time):
    case = scenario
    configured_project(case, ["completed", "in_progress"])
    configured_project(case, ["under_review"])
    full = report(case, "teacher", limit=100)
    first = report(case, "teacher", limit=1, offset=0)
    next_page = report(case, "teacher", limit=1, offset=1)
    beyond = report(case, "teacher", limit=1, offset=10_000)
    assert full["total_matching"] == first["total_matching"] == next_page["total_matching"]
    assert full["summary"] == first["summary"] == next_page["summary"] == beyond["summary"]
    assert len(first["items"]) == len(next_page["items"]) == 1
    assert first["items"][0]["project_id"] < next_page["items"][0]["project_id"]
    assert first["items"][0]["project_id"] != next_page["items"][0]["project_id"]
    assert beyond["items"] == []
    assert beyond["total_matching"] == full["total_matching"]


def test_empty_result_has_all_zero_aggregates(scenario, report_time):
    result = report(scenario, "teacher", student_id=9_000_000_002)
    assert result["items"] == []
    assert result["total_matching"] == 0
    assert result["summary"] == {
        "status_counts": {
            "planning": 0, "in_progress": 0, "under_review": 0,
            "revision": 0, "completed": 0,
        },
        "overdue_projects": 0,
        "total_stages": 0,
        "completed_stages": 0,
    }


def test_response_has_no_sensitive_data_and_does_not_mutate_database(scenario, report_time):
    case = scenario
    before = case.snapshot()
    result = report(case, "teacher")
    assert case.snapshot() == before
    payload = str(result).lower()
    for forbidden in ("password_hash", "storage_key", "jwt", "db_password", "file content"):
        assert forbidden not in payload
    allowed = {
        "project_id", "topic_id", "topic_title", "student_id", "supervisor_id",
        "teacher_id", "created_at", "plan_approved", "status", "status_name",
        "total_stages", "completed_stages", "progress_percent", "is_overdue",
    }
    assert set(result["items"][0]) == allowed
    assert datetime.fromisoformat(
        result["generated_at"].replace("Z", "+00:00")
    ) == REFERENCE_TIME
    assert result["filters"] == {
        "access_scope": "teacher",
        "created_from": None, "created_before": None, "student_id": None,
        "supervisor_id": None, "teacher_id": None, "status": None,
        "overdue_only": False,
    }
