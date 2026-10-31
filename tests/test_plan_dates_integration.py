from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from threading import Barrier, Event
from time import monotonic

import pytest
from sqlalchemy import event, text


REASON = " Уточнение сроков по согласованию с руководителем. "
REFERENCE = datetime(2020, 5, 10, 12, tzinfo=timezone.utc)


@pytest.fixture
def dates_case(scenario):
    # Only the disposable server is affected; restore shared approvals so the
    # global deadline job cannot introduce cross-scenario dependencies.
    with scenario.engine.begin() as connection:
        previous = connection.execute(text("""
            SELECT id, plan_approved_at, plan_approved_by FROM public.projects
            WHERE plan_approved_at IS NOT NULL ORDER BY id
        """)).mappings().all()
        connection.execute(text("""
            UPDATE public.projects SET plan_approved_at = NULL, plan_approved_by = NULL
            WHERE id = ANY(:ids)
        """), {"ids": [row["id"] for row in previous]})
    try:
        scenario.approve()
        yield scenario
    finally:
        with scenario.engine.begin() as connection:
            for row in previous:
                connection.execute(text("""
                    UPDATE public.projects SET plan_approved_at = :at, plan_approved_by = :by WHERE id = :id
                """), {"id": row["id"], "at": row["plan_approved_at"], "by": row["plan_approved_by"]})


def plan(case):
    return case.request("student", "GET", f"/projects/{case.project_id}/plan", 200)


def payload(case, delta=timedelta(days=10)):
    return {
        "reason": REASON,
        "stages": [
            {
                "stage_id": stage["id"],
                "expected_starts_at": stage["starts_at"],
                "expected_due_at": stage["due_at"],
                "starts_at": (datetime.fromisoformat(stage["starts_at"]) + delta).isoformat(),
                "due_at": (datetime.fromisoformat(stage["due_at"]) + delta).isoformat(),
            }
            for stage in plan(case)["stages"]
        ],
    }


def put(case, data, actor="supervisor", expected=200):
    response = case.client.put(
        f"/projects/{case.project_id}/plan/dates", json=data,
        headers=case.actors[actor]["headers"],
    )
    assert response.status_code == expected, response.text
    return response.json()


def audit_rows(case):
    with case.engine.connect() as connection:
        return connection.execute(text("""
            SELECT * FROM public.audit_events
            WHERE action = 'plan_dates_changed' AND entity_id = :id ORDER BY id
        """), {"id": case.project_id}).mappings().all()


def notice_rows(case, prefix="plan-dates-changed:"):
    with case.engine.connect() as connection:
        return connection.execute(text("""
            SELECT * FROM public.notifications
            WHERE project_id = :id AND event_key LIKE :prefix ORDER BY id
        """), {"id": case.project_id, "prefix": prefix + "%"}).mappings().all()


def assert_rejected_unchanged(case, data, expected, actor="supervisor"):
    before = case.snapshot()
    put(case, data, actor=actor, expected=expected)
    assert case.snapshot() == before


def test_authentication_roles_and_project_scope(dates_case):
    case = dates_case
    data = payload(case)
    before = case.snapshot()
    assert case.client.put(f"/projects/{case.project_id}/plan/dates", json=data).status_code == 401
    for actor in ("student", "department", "admin"):
        put(case, data, actor=actor, expected=403)
    for actor in ("unassigned", "teacher2"):
        put(case, data, actor=actor, expected=404)
    assert case.snapshot() == before
    assert case.client.put("/projects/9223372036854775807/plan/dates", json=data,
                           headers=case.actors["supervisor"]["headers"]).status_code == 404


@pytest.mark.parametrize("actor", ["supervisor", "teacher"])
def test_assigned_authority_changes_all_dates_in_position_order(dates_case, actor):
    case = dates_case
    before = plan(case)
    data = payload(case)
    data["stages"].reverse()
    response = put(case, data, actor=actor)
    assert response == plan(case)
    assert {key: response[key] for key in ("approved", "approved_by", "approved_at")} == {
        key: before[key] for key in ("approved", "approved_by", "approved_at")
    }
    expected = {stage["stage_id"]: stage for stage in data["stages"]}
    assert [stage["position"] for stage in response["stages"]] == [1, 2, 3]
    for old, new in zip(before["stages"], response["stages"], strict=True):
        for key in old.keys() - {"starts_at", "due_at"}:
            assert new[key] == old[key]
        assert datetime.fromisoformat(new["starts_at"]) == datetime.fromisoformat(expected[new["id"]]["starts_at"])
        assert datetime.fromisoformat(new["due_at"]) == datetime.fromisoformat(expected[new["id"]]["due_at"])


def test_student_with_teacher_permission_cannot_authorize_self(dates_case):
    case = dates_case
    user = case.actors["student"]["id"]
    with case.engine.begin() as connection:
        role = connection.execute(text("SELECT id FROM public.roles WHERE code = 'teacher'")).scalar_one()
        connection.execute(text("INSERT INTO public.user_roles VALUES (:id, :role)"), {"id": user, "role": role})
    try:
        assert "plans.approve" in case.request("student", "GET", "/auth/me", 200)["permissions"]
        assert_rejected_unchanged(case, payload(case), 403, "student")
    finally:
        with case.engine.begin() as connection:
            connection.execute(text("DELETE FROM public.user_roles WHERE user_id = :id AND role_id = :role"),
                               {"id": user, "role": role})


def test_plan_must_be_approved_and_have_uncompleted_stages(scenario):
    case = scenario
    assert_rejected_unchanged(case, payload(case), 409)
    case.approve()
    for index in range(3):
        case.review(case.upload(stage=index))
    assert case.status()["status"] == "completed"
    assert_rejected_unchanged(case, payload(case, timedelta(0)), 409)


def test_approved_empty_plan_is_rejected(dates_case):
    case = dates_case
    data = payload(case)
    with case.engine.begin() as connection:
        connection.execute(text("DELETE FROM public.stages WHERE project_id = :id"), {"id": case.project_id})
    assert_rejected_unchanged(case, data, 409)


@pytest.mark.parametrize("kind", [
    "naive", "naive_expected", "equal", "reversed", "overlap",
    "duplicate", "missing", "unknown", "foreign",
    "stage_zero", "stage_negative", "stage_overflow",
    "extra_stage", "extra_root", "reason_short", "reason_blank", "reason_long", "reason_missing",
    "empty", "too_many", "date_missing",
])
def test_validation_is_atomic(dates_case, kind):
    case = dates_case
    data = payload(case)
    first = data["stages"][0]
    if kind == "naive":
        first["starts_at"] = "2099-01-11T00:00:00"
    elif kind == "naive_expected":
        first["expected_due_at"] = "2099-01-02T00:00:00"
    elif kind == "equal":
        first["due_at"] = first["starts_at"]
    elif kind == "reversed":
        first["due_at"] = "2099-01-01T00:00:00Z"
    elif kind == "overlap":
        data["stages"][1]["starts_at"] = first["starts_at"]
    elif kind == "duplicate":
        data["stages"][1]["stage_id"] = first["stage_id"]
    elif kind == "missing":
        data["stages"].pop()
    elif kind == "unknown":
        first["stage_id"] = 9_223_372_036_854_775_807
    elif kind == "foreign":
        other = case.request("outsider", "PUT", f"/projects/{case.other_project_id}/plan", 200,
                             json=case.plan_input)
        first["stage_id"] = other["stages"][0]["id"]
    elif kind.startswith("stage_"):
        first["stage_id"] = {"stage_zero": 0, "stage_negative": -1,
                             "stage_overflow": 9_223_372_036_854_775_808}[kind]
    elif kind == "extra_stage":
        first["title"] = "Must not rename"
    elif kind == "extra_root":
        data["approved_by"] = case.actors["student"]["id"]
    elif kind.startswith("reason_"):
        if kind == "reason_missing":
            del data["reason"]
        else:
            data["reason"] = {"reason_short": " 123456789 ", "reason_blank": " " * 20,
                              "reason_long": "x" * 2001}[kind]
    elif kind == "empty":
        data["stages"] = []
    elif kind == "too_many":
        data["stages"] = [deepcopy(first) for _ in range(51)]
    else:
        del first["expected_starts_at"]
    assert_rejected_unchanged(case, data, 422)


def test_project_id_must_fit_positive_bigint(dates_case):
    case = dates_case
    before = case.snapshot()
    for user_id in (0, -1, 9_223_372_036_854_775_808):
        response = case.client.put(f"/projects/{user_id}/plan/dates", json=payload(case),
                                   headers=case.actors["supervisor"]["headers"])
        assert response.status_code == 422
    assert case.snapshot() == before


def test_completed_dates_immutable_but_other_dates_can_change(dates_case):
    case = dates_case
    case.review(case.upload())
    data = payload(case)
    assert_rejected_unchanged(case, data, 409)
    first = payload(case, timedelta(0))["stages"][0]
    data["stages"][0] = first
    before = plan(case)["stages"][0]
    result = put(case, data)
    assert result["stages"][0] == before
    assert [change["stage_id"] for change in audit_rows(case)[0]["details"]["changes"]] == [
        stage["id"] for stage in case.stages[1:]
    ]


@pytest.mark.parametrize("state", ["under_review", "revision"])
def test_materials_reviews_files_and_state_preserved(dates_case, state):
    case = dates_case
    material = case.upload()
    if state == "revision":
        case.review(material, "revision", "Исправить описание")
    before = case.snapshot()
    status = case.status()
    put(case, payload(case))
    after = case.snapshot()
    assert before[1] == after[1]
    assert before[0][2:4] == after[0][2:4]
    assert before[0][0] == after[0][0]
    now = case.status()
    assert now["status"] == status["status"] == state
    assert now["progress_percent"] == status["progress_percent"] == 0
    assert [stage["status"] for stage in now["stages"]] == [stage["status"] for stage in status["stages"]]


def test_noop_offsets_stale_expected_and_initial_contract(dates_case):
    case = dates_case
    data = payload(case, timedelta(0))
    for stage in data["stages"]:
        for field in ("starts_at", "due_at", "expected_starts_at", "expected_due_at"):
            stage[field] = datetime.fromisoformat(stage[field]).astimezone(
                timezone(timedelta(hours=3))
            ).isoformat()
    before = case.snapshot()
    assert put(case, data) == plan(case)
    assert case.snapshot() == before
    original = payload(case)
    put(case, original)
    assert_rejected_unchanged(case, original, 409)
    fresh = payload(case, timedelta(0))
    before = case.snapshot()
    put(case, fresh)
    assert case.snapshot() == before
    case.request("student", "PUT", f"/projects/{case.project_id}/plan", 409, json=case.plan_input)
    case.request("supervisor", "POST", f"/projects/{case.project_id}/plan/approve", 409)
    assert case.snapshot() == before


def test_stale_last_stage_rejects_without_partial_writes(dates_case):
    case = dates_case
    data = payload(case)
    data["stages"][-1]["expected_due_at"] = "2099-01-05T00:00:00Z"
    assert_rejected_unchanged(case, data, 409)
    assert audit_rows(case) == notice_rows(case) == []


def test_changed_dates_accept_equivalent_expected_offsets(dates_case):
    case = dates_case
    data = payload(case)
    for stage in data["stages"]:
        for field in ("expected_starts_at", "expected_due_at", "starts_at", "due_at"):
            stage[field] = datetime.fromisoformat(stage[field]).astimezone(
                timezone(timedelta(hours=-5))
            ).isoformat()
    result = put(case, data)
    assert result == plan(case)
    assert all(datetime.fromisoformat(stage["starts_at"]).utcoffset() == timedelta(0)
               for stage in result["stages"])
    assert len(audit_rows(case)) == 1


@pytest.mark.parametrize("recipients", ["normal", "duplicate", "inactive"])
def test_audit_and_notification_linkage(dates_case, recipients):
    case = dates_case
    if recipients == "duplicate":
        with case.engine.begin() as connection:
            connection.execute(text("UPDATE public.projects SET supervisor_id = teacher_id WHERE id = :id"),
                               {"id": case.project_id})
        actor = "teacher"
        expected_names = {"student"}
    else:
        actor = "supervisor"
        expected_names = {"student", "teacher"}
        if recipients == "inactive":
            case.request("admin", "PATCH", f"/admin/users/{case.actors['teacher']['id']}/status", 200,
                         json={"is_active": False})
            expected_names = {"student"}
    old = plan(case)
    data = payload(case)
    put(case, data, actor)
    events = audit_rows(case)
    assert len(events) == 1
    row = events[0]
    assert row["actor_id"] == case.actors[actor]["id"]
    assert row["entity_type"] == "projects"
    details = row["details"]
    assert details["reason"] == REASON.strip()
    assert datetime.fromisoformat(details["changed_at"]).tzinfo is not None
    assert [change["position"] for change in details["changes"]] == [1, 2, 3]
    for change, stage, proposed in zip(details["changes"], old["stages"], data["stages"], strict=True):
        assert change["stage_id"] == stage["id"]
        for field in ("starts_at", "due_at"):
            assert datetime.fromisoformat(change["before"][field]) == datetime.fromisoformat(stage[field])
            assert datetime.fromisoformat(change["after"][field]) == datetime.fromisoformat(proposed[field])
            assert change["before"][field].endswith("Z") and change["after"][field].endswith("Z")
    notices = notice_rows(case)
    assert {row["recipient_id"] for row in notices} == {case.actors[name]["id"] for name in expected_names}
    assert len(notices) == len(expected_names)
    assert {row["event_key"] for row in notices} == {f"plan-dates-changed:{details['operation_id']}"}
    assert all(len(row["event_key"]) <= 200 and str(case.project_id) in row["message"] for row in notices)
    assert not any(word in str(details).lower() for word in ("password", "jwt", "authorization", "storage_key"))
    case.request("student", "PATCH", f"/notifications/{notices[0]['id']}/read", 200)
    history = [dict(row) for row in notice_rows(case)]
    fresh = payload(case)
    put(case, fresh, actor)
    assert len(audit_rows(case)) == 2
    assert len({row["event_key"] for row in notice_rows(case)}) == 2
    assert [dict(row) for row in notice_rows(case)[:len(history)]] == history


def wait_for_waiters(engine, expected, timeout=4):
    until = monotonic() + timeout
    while monotonic() < until:
        with engine.connect() as connection:
            count = connection.execute(text("""
                SELECT COUNT(*) FROM pg_stat_activity WHERE wait_event_type = 'Lock'
                AND query LIKE '%public.projects%' AND query LIKE '%FOR UPDATE%'
                AND pid <> pg_backend_pid()
            """)).scalar_one()
        if count >= expected:
            return
        Event().wait(0.01)
    raise AssertionError(f"Expected {expected} real project lock waiters")


def test_two_clients_with_same_expected_dates_cannot_overwrite(dates_case):
    case = dates_case
    data = payload(case)
    barrier = Barrier(2)
    with case.engine.connect() as blocker:
        transaction = blocker.begin()
        blocker.execute(text("SELECT id FROM public.projects WHERE id = :id FOR UPDATE"), {"id": case.project_id})
        with ThreadPoolExecutor(max_workers=2) as pool:
            def execute():
                barrier.wait(timeout=4)
                return case.client.put(
                    f"/projects/{case.project_id}/plan/dates", json=data,
                    headers=case.actors["supervisor"]["headers"],
                )
            futures = [pool.submit(execute) for _ in range(2)]
            try:
                wait_for_waiters(case.engine, 2)
            finally:
                transaction.rollback()
            responses = [future.result(timeout=12) for future in futures]
    assert sorted(response.status_code for response in responses) == [200, 409]
    assert len(audit_rows(case)) == 1 and len(notice_rows(case)) == 2


@pytest.mark.parametrize("change", ["inactive", "permission", "assignment", "completion"])
def test_revalidation_after_wait(dates_case, change):
    case = dates_case
    data = payload(case)
    actor_id = case.actors["supervisor"]["id"]
    permission_link = None
    with case.engine.connect() as blocker:
        transaction = blocker.begin()
        blocker.execute(text("SELECT id FROM public.projects WHERE id = :id FOR UPDATE"), {"id": case.project_id})
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(put, case, data, "supervisor",
                                     409 if change == "completion" else 404 if change == "assignment" else 403)
                try:
                    wait_for_waiters(case.engine, 1)
                    if change == "inactive":
                        blocker.execute(text("UPDATE public.users SET is_active = false WHERE id = :id"), {"id": actor_id})
                    elif change == "permission":
                        permission_link = blocker.execute(text("""
                            DELETE FROM public.role_permissions
                            WHERE role_id = (SELECT id FROM public.roles WHERE code = 'supervisor')
                              AND permission_id = (SELECT id FROM public.permissions WHERE code = 'plans.approve')
                            RETURNING role_id, permission_id
                        """)).one()
                    elif change == "assignment":
                        blocker.execute(text("UPDATE public.projects SET supervisor_id = :actor WHERE id = :id"),
                                        {"actor": case.actors["unassigned"]["id"], "id": case.project_id})
                    else:
                        blocker.execute(text("""
                            UPDATE public.stages SET status = 'completed', completed_at = clock_timestamp() WHERE id = :id
                        """), {"id": case.stages[0]["id"]})
                    transaction.commit()
                    before = case.snapshot()
                    future.result(timeout=12)
                    assert case.snapshot() == before
                finally:
                    if transaction.is_active:
                        transaction.rollback()
        finally:
            if change == "inactive":
                with case.engine.begin() as connection:
                    connection.execute(text("UPDATE public.users SET is_active = true WHERE id = :id"), {"id": actor_id})
            if permission_link is not None:
                with case.engine.begin() as connection:
                    connection.execute(text("INSERT INTO public.role_permissions VALUES (:r, :p)"),
                                       {"r": permission_link.role_id, "p": permission_link.permission_id})


@pytest.mark.parametrize("winner", ["review", "dates"])
def test_date_changes_race_with_real_acceptance(dates_case, winner):
    case = dates_case
    material = case.upload()
    data = payload(case)
    held, release = Event(), Event()
    def hold(connection, cursor, statement, parameters, context, executemany):
        if (
            "SELECT * FROM public.stages" in statement and "FOR UPDATE" in statement
            and (("project_id" in statement) == (winner == "dates")) and not held.is_set()
        ):
            held.set()
            assert release.wait(timeout=8)

    event.listen(case.engine, "after_cursor_execute", hold)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            def operation(name):
                return case.review(material) if name == "review" else put(
                    case, data, expected=409 if winner == "review" else 200,
                )
            first = pool.submit(operation, winner)
            try:
                assert held.wait(timeout=4)
                second = pool.submit(operation, "dates" if winner == "review" else "review")
                wait_for_waiters(case.engine, 1)
            finally:
                release.set()
            first.result(timeout=12)
            second.result(timeout=12)
        assert case.status()["stages"][0]["status"] == "completed"
        assert len(audit_rows(case)) == (0 if winner == "review" else 1)
    finally:
        release.set()
        event.remove(case.engine, "after_cursor_execute", hold)


def timeline_payload(case, due):
    data = payload(case, timedelta(0))
    for index, stage in enumerate(data["stages"]):
        start = due - timedelta(days=1) if index == 0 else due + timedelta(days=index - 1)
        stage["starts_at"] = start.isoformat()
        stage["due_at"] = (due + timedelta(days=index)).isoformat()
    return data


@pytest.mark.parametrize("winner", ["dates", "deadlines"])
def test_date_changes_race_with_deadline_generation(dates_case, winner):
    from app.deadlines import check_deadlines, event_key
    case = dates_case
    old_due = REFERENCE + timedelta(hours=1)
    put(case, timeline_payload(case, old_due))
    data = timeline_payload(case, REFERENCE + timedelta(hours=2))
    held, release = Event(), Event()
    def hold(connection, cursor, statement, parameters, context, executemany):
        if (
            "SELECT * FROM public.projects" in statement and "FOR UPDATE" in statement
            and parameters.get("id") == case.project_id
            and (" AND " in statement) == (winner == "dates") and not held.is_set()
        ):
            held.set()
            assert release.wait(timeout=8)

    event.listen(case.engine, "after_cursor_execute", hold)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            def operation(name):
                return put(case, data) if name == "dates" else check_deadlines(
                    case.engine, apply=True, reference_time=REFERENCE,
                )
            first = pool.submit(operation, winner)
            try:
                assert held.wait(timeout=4)
                second = pool.submit(operation, "dates" if winner == "deadlines" else "deadlines")
                wait_for_waiters(case.engine, 1)
            finally:
                release.set()
            first_value, second_value = first.result(timeout=12), second.result(timeout=12)
        job = first_value if winner == "deadlines" else second_value
        assert job.errors == [] and job.counts.upcoming == 3
        expected_due = old_due if winner == "deadlines" else REFERENCE + timedelta(hours=2)
        assert {row["event_key"] for row in notice_rows(case, "deadline:")} == {
            event_key("upcoming", case.stages[0]["id"], expected_due)
        }
    finally:
        release.set()
        event.remove(case.engine, "after_cursor_execute", hold)


def test_report_state_and_deadline_history_use_current_dates(dates_case, monkeypatch):
    from app.deadlines import check_deadlines, event_key
    import app.reports
    case = dates_case
    monkeypatch.setattr(app.reports, "report_now", lambda: REFERENCE)
    due = REFERENCE + timedelta(hours=1)
    put(case, timeline_payload(case, due))
    first = check_deadlines(case.engine, apply=True, reference_time=REFERENCE)
    assert first.errors == [] and first.counts.upcoming == 3
    notice = notice_rows(case, "deadline:")[0]
    case.request("student", "PATCH", f"/notifications/{notice['id']}/read", 200)
    old_history = [dict(row) for row in notice_rows(case, "deadline:")]
    start_only = payload(case, timedelta(0))
    start_only["stages"][0]["starts_at"] = (due - timedelta(days=2)).isoformat()
    put(case, start_only)
    repeated = check_deadlines(case.engine, apply=True, reference_time=REFERENCE)
    assert repeated.errors == [] and repeated.counts.upcoming == 0 and repeated.counts.existing == 3
    assert [dict(row) for row in notice_rows(case, "deadline:")] == old_history
    put(case, timeline_payload(case, REFERENCE - timedelta(hours=1)))
    report = case.request("teacher", "GET", "/reports/projects", 200)
    assert report["items"][0]["is_overdue"] is True
    status = case.status()
    assert status["is_overdue"] and status["stages"][0]["is_overdue"]
    assert status["progress_percent"] == 0 and status["status"] == "in_progress"
    latest = check_deadlines(case.engine, apply=True, reference_time=REFERENCE)
    assert latest.errors == [] and latest.counts.overdue == 3
    assert [dict(row) for row in notice_rows(case, "deadline:")[:3]] == old_history
    assert event_key("overdue", case.stages[0]["id"], REFERENCE - timedelta(hours=1)) in {
        row["event_key"] for row in notice_rows(case, "deadline:")
    }
    put(case, timeline_payload(case, datetime(2099, 5, 10, tzinfo=timezone.utc)))
    assert not case.status()["is_overdue"]
    assert not case.request("teacher", "GET", "/reports/projects", 200)["items"][0]["is_overdue"]


@pytest.mark.parametrize("failure", ["audit", "notification"])
def test_sql_failure_rolls_back_dates_audit_and_notifications(dates_case, failure):
    case = dates_case
    def fail(connection, cursor, statement, parameters, context, executemany):
        target = "public.audit_events" if failure == "audit" else "public.notifications"
        if f"INSERT INTO {target}" in statement:
            connection.exec_driver_sql("SELECT 1 / 0")

    data = payload(case)
    before = case.snapshot()
    event.listen(case.engine, "before_cursor_execute", fail)
    try:
        put(case, data, expected=503)
    finally:
        event.remove(case.engine, "before_cursor_execute", fail)
    assert case.snapshot() == before
