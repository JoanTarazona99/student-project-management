import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier, Event
from time import monotonic

import pytest
from sqlalchemy import event, text

from app.deadlines import check_deadlines, event_key


REFERENCE = datetime(2035, 5, 10, 12, tzinfo=timezone.utc)


@pytest.fixture
def deadline_case(scenario):
    # The shared server retains earlier scenarios. Temporarily exclude their plans
    # from this global job, restoring them even if the test fails.
    with scenario.engine.begin() as connection:
        previous = connection.execute(text("""
            SELECT id, plan_approved_at, plan_approved_by FROM public.projects
            WHERE plan_approved_at IS NOT NULL AND id <> :id ORDER BY id
        """), {"id": scenario.project_id}).mappings().all()
        connection.execute(text("""
            UPDATE public.projects SET plan_approved_at = NULL, plan_approved_by = NULL
            WHERE id = ANY(:ids)
        """), {"ids": [row["id"] for row in previous]})
    try:
        scenario.approve()
        set_stage(scenario, due=REFERENCE + timedelta(hours=1))
        yield scenario
    finally:
        with scenario.engine.begin() as connection:
            for row in previous:
                connection.execute(text("""
                    UPDATE public.projects SET plan_approved_at = :at, plan_approved_by = :by
                    WHERE id = :id
                """), {"id": row["id"], "at": row["plan_approved_at"], "by": row["plan_approved_by"]})


def set_stage(case, *, due, status="in_progress", completed_at=None, index=0):
    with case.engine.begin() as connection:
        connection.execute(text("""
            UPDATE public.stages
            SET due_at = :due, starts_at = :due - INTERVAL '1 day',
                status = :status, completed_at = :completed WHERE id = :id
        """), {
            "id": case.stages[index]["id"], "due": due, "status": status, "completed": completed_at,
        })


def run(case, **kwargs):
    result = check_deadlines(case.engine, reference_time=REFERENCE, **kwargs)
    assert result.errors == []
    return result


def notifications(case):
    with case.engine.connect() as connection:
        return connection.execute(text("""
            SELECT * FROM public.notifications
            WHERE project_id = :id AND event_key LIKE 'deadline:%' ORDER BY id
        """), {"id": case.project_id}).mappings().all()


def audits(case):
    with case.engine.connect() as connection:
        return connection.execute(text("""
            SELECT * FROM public.audit_events
            WHERE action = 'deadline_notifications_generated'
              AND details->>'project_id' = :id ORDER BY id
        """), {"id": str(case.project_id)}).mappings().all()


def whole_database(case):
    with case.engine.connect() as connection:
        tables = connection.execute(text("""
            SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename
        """)).scalars().all()
        return {
            table: connection.execute(text(f'SELECT * FROM public."{table}" ORDER BY 1, 2')).all()
            for table in tables
        }


@pytest.mark.parametrize(("delta", "upcoming", "overdue"), [
    (timedelta(microseconds=1), 3, 0),
    (timedelta(hours=24), 3, 0),
    (timedelta(hours=24, microseconds=1), 0, 0),
    (timedelta(microseconds=-1), 0, 3),
    (timedelta(0), 0, 0),
])
def test_time_boundaries(deadline_case, delta, upcoming, overdue):
    case = deadline_case
    set_stage(case, due=REFERENCE + delta)
    result = run(case, apply=True)
    assert (result.counts.upcoming, result.counts.overdue) == (upcoming, overdue)
    assert len(notifications(case)) == upcoming + overdue


@pytest.mark.parametrize("status", ["planned", "in_progress", "under_review", "revision"])
def test_all_uncompleted_states_are_eligible(deadline_case, status):
    set_stage(deadline_case, due=REFERENCE + timedelta(hours=1), status=status)
    assert run(deadline_case, apply=True).counts.upcoming == 3


@pytest.mark.parametrize("late", [False, True])
def test_completed_stage_never_generates(deadline_case, late):
    due = REFERENCE - timedelta(hours=2)
    completed = due + timedelta(hours=1 if late else -1)
    set_stage(deadline_case, due=due, status="completed", completed_at=completed)
    assert run(deadline_case, apply=True).counts.overdue == 0
    assert notifications(deadline_case) == audits(deadline_case) == []


def test_unapproved_and_empty_projects_are_ignored(deadline_case):
    case = deadline_case
    with case.engine.begin() as connection:
        connection.execute(text("""
            UPDATE public.projects SET plan_approved_at = NULL, plan_approved_by = NULL WHERE id = :id
        """), {"id": case.project_id})
        connection.execute(text("""
            UPDATE public.projects SET plan_approved_at = :at, plan_approved_by = :by WHERE id = :id
        """), {"at": REFERENCE, "by": case.actors["teacher2"]["id"], "id": case.other_project_id})
    result = run(case, apply=True)
    assert result.counts.stages_examined == 0
    assert result.counts.upcoming == result.counts.overdue == 0
    assert notifications(case) == audits(case) == []


def test_equivalent_offsets_have_identical_classification_and_key(deadline_case):
    case = deadline_case
    due = (REFERENCE + timedelta(hours=1)).astimezone(timezone(timedelta(hours=-5)))
    set_stage(case, due=due)
    first = check_deadlines(
        case.engine, apply=True,
        reference_time=REFERENCE.astimezone(timezone(timedelta(hours=3))),
    )
    assert first.errors == []
    assert first.reference_time == REFERENCE
    assert first.counts.upcoming == 3
    second = run(case, apply=True)
    assert second.counts.upcoming == 0 and second.counts.existing == 3
    key = event_key("upcoming", case.stages[0]["id"], due)
    assert key == event_key("upcoming", case.stages[0]["id"], due.astimezone(timezone.utc))
    assert len(event_key("upcoming", 9_223_372_036_854_775_807, due)) <= 200
    assert {row["event_key"] for row in notifications(case)} == {key}


def test_recipients_are_scoped_unique_and_active(deadline_case):
    case = deadline_case
    first = run(case, apply=True)
    assert first.counts.upcoming == 3
    assert {row["recipient_id"] for row in notifications(case)} == {
        case.actors[name]["id"] for name in ("student", "supervisor", "teacher")
    }
    for row in notifications(case):
        assert str(case.project_id) in row["message"]
        assert case.stages[0]["title"] in row["message"]
        assert "2035-05-10T13:00:00.000000Z" in row["message"]
    case.request("admin", "PATCH", f"/admin/users/{case.actors['supervisor']['id']}/status", 200,
                 json={"is_active": False})
    set_stage(case, due=REFERENCE + timedelta(hours=2))
    second = run(case, apply=True)
    assert second.counts.upcoming == 2
    assert second.counts.inactive_recipients == 1
    assert len(notifications(case)) == 5


@pytest.mark.parametrize("supervisor", ["teacher", None])
def test_duplicate_functions_and_missing_supervisor(deadline_case, supervisor):
    case = deadline_case
    with case.engine.begin() as connection:
        connection.execute(text("UPDATE public.projects SET supervisor_id = :supervisor WHERE id = :id"), {
            "id": case.project_id,
            "supervisor": case.actors[supervisor]["id"] if supervisor else None,
        })
    result = run(case, apply=True)
    assert result.counts.upcoming == 2
    assert {row["recipient_id"] for row in notifications(case)} == {
        case.actors[name]["id"] for name in ("student", "teacher")
    }


def test_idempotence_window_new_deadline_and_read_history(deadline_case):
    case = deadline_case
    assert run(case, apply=True).counts.upcoming == 3
    initial = notifications(case)
    read = case.request("student", "PATCH", f"/notifications/{initial[0]['id']}/read", 200)
    assert read["read_at"] is not None
    initial = [dict(row) for row in notifications(case)]
    assert run(case, apply=True, warning_hours=168).counts.existing == 3
    assert len(audits(case)) == 1
    overdue = check_deadlines(case.engine, apply=True, reference_time=REFERENCE + timedelta(hours=2))
    assert overdue.errors == [] and overdue.counts.overdue == 3
    set_stage(case, due=REFERENCE + timedelta(hours=4))
    assert run(case, apply=True).counts.upcoming == 3
    assert [dict(row) for row in notifications(case)[:3]] == initial
    assert len(notifications(case)) == 9 and len(audits(case)) == 3


def test_dry_run_is_read_only_and_counts_candidates_and_existing(deadline_case):
    case = deadline_case
    before = whole_database(case)
    simulation = run(case)
    assert simulation.apply is False
    assert simulation.counts.upcoming == 3 and simulation.counts.existing == 0
    assert whole_database(case) == before
    assert run(case, apply=True).counts.upcoming == 3
    before = whole_database(case)
    simulation = run(case)
    assert simulation.counts.upcoming == 0 and simulation.counts.existing == 3
    assert whole_database(case) == before


def test_audit_matches_insertions_and_academic_data_are_unchanged(deadline_case):
    case = deadline_case
    before = whole_database(case)
    status_before = case.status()
    result = run(case, apply=True)
    after = whole_database(case)
    for table in before.keys() - {"notifications", "audit_events"}:
        assert after[table] == before[table]
    assert len(after["notifications"]) - len(before["notifications"]) == result.counts.upcoming == 3
    assert len(after["audit_events"]) - len(before["audit_events"]) == 1
    item = audits(case)[0]
    assert item["actor_id"] is None
    assert item["entity_type"] == "stages" and item["entity_id"] == case.stages[0]["id"]
    assert item["details"] == {
        "project_id": case.project_id, "stage_id": case.stages[0]["id"], "kind": "upcoming",
        "due_at": "2035-05-10T13:00:00.000000Z", "reference_time": "2035-05-10T12:00:00.000000Z",
        "notification_ids": [row["id"] for row in notifications(case)], "count": 3,
    }
    for forbidden in ("password", "jwt", "authorization", "storage_key"):
        assert forbidden not in json.dumps(dict(item), default=str).lower()
    status_after = case.status()
    status_before.pop("calculated_at")
    status_after.pop("calculated_at")
    assert status_after == status_before
    assert run(case, apply=True).counts.upcoming == 0
    assert len(audits(case)) == 1


def wait_for_project_waiters(engine, expected, timeout=4):
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        with engine.connect() as connection:
            count = connection.execute(text("""
                SELECT COUNT(*) FROM pg_stat_activity
                WHERE wait_event_type = 'Lock' AND query LIKE '%public.projects%'
                  AND query LIKE '%FOR UPDATE%' AND pid <> pg_backend_pid()
            """)).scalar_one()
        if count >= expected:
            return
        Event().wait(0.01)
    raise AssertionError(f"Expected {expected} project lock waiters")


def test_concurrent_jobs_insert_only_once(deadline_case):
    case = deadline_case
    barrier = Barrier(2)
    with case.engine.connect() as blocker:
        transaction = blocker.begin()
        blocker.execute(text("SELECT id FROM public.projects WHERE id = :id FOR UPDATE"),
                        {"id": case.project_id})
        with ThreadPoolExecutor(max_workers=2) as pool:
            def execute():
                barrier.wait(timeout=4)
                return run(case, apply=True)

            futures = [pool.submit(execute) for _ in range(2)]
            try:
                wait_for_project_waiters(case.engine, 2)
            finally:
                transaction.rollback()
            results = [future.result(timeout=12) for future in futures]
    assert sorted(item.counts.upcoming for item in results) == [0, 3]
    assert sorted(item.counts.existing for item in results) == [0, 3]
    assert len(notifications(case)) == 3 and len(audits(case)) == 1


def test_acceptance_confirmed_before_job_is_respected(deadline_case):
    case = deadline_case
    case.review(case.upload())
    result = run(case, apply=True)
    assert result.counts.upcoming == result.counts.overdue == 0
    assert notifications(case) == audits(case) == []


@pytest.mark.parametrize("winner", ["review", "deadlines"])
def test_acceptance_race_obeys_project_lock_and_commit_boundary(deadline_case, winner):
    case = deadline_case
    material = case.upload()
    held, release = Event(), Event()
    def hold_project(connection, cursor, statement, parameters, execution_context, executemany):
        if (
            "SELECT * FROM public.projects" in statement and "FOR UPDATE" in statement
            and parameters.get("id") == case.project_id and not held.is_set()
            and (" AND " in statement) == (winner == "review")
        ):
            held.set()
            assert release.wait(timeout=10), "Lock holder was not released"

    def operation(name):
        return case.review(material) if name == "review" else run(case, apply=True)

    event.listen(case.engine, "after_cursor_execute", hold_project)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(operation, winner)
            try:
                assert held.wait(timeout=4)
                second = pool.submit(operation, "deadlines" if winner == "review" else "review")
                wait_for_project_waiters(case.engine, 1)
            finally:
                release.set()
            first_result, second_result = first.result(timeout=12), second.result(timeout=12)
        job = second_result if winner == "review" else first_result
        assert job.counts.upcoming == (0 if winner == "review" else 3)
        assert len(notifications(case)) == job.counts.upcoming
        assert case.status()["stages"][0]["status"] == "completed"
    finally:
        release.set()
        event.remove(case.engine, "after_cursor_execute", hold_project)


def test_rollback_includes_notifications_and_audit_and_reports_partial_success(deadline_case):
    case = deadline_case
    other_plan = case.request("outsider", "PUT", f"/projects/{case.other_project_id}/plan", 200,
                              json=case.plan_input)
    case.request("unassigned", "POST", f"/projects/{case.other_project_id}/plan/approve", 200)
    with case.engine.begin() as connection:
        connection.execute(text("""
            UPDATE public.stages SET starts_at = :due - INTERVAL '1 day', due_at = :due WHERE id = :id
        """), {"due": REFERENCE - timedelta(hours=1), "id": other_plan["stages"][0]["id"]})

    def fail_audit(connection, cursor, statement, parameters, context, executemany):
        if "INSERT INTO public.audit_events" in statement and parameters.get("id") == case.stages[0]["id"]:
            connection.exec_driver_sql("SELECT 1 / 0")

    before = whole_database(case)
    event.listen(case.engine, "before_cursor_execute", fail_audit)
    try:
        result = check_deadlines(case.engine, apply=True, reference_time=REFERENCE)
    finally:
        event.remove(case.engine, "before_cursor_execute", fail_audit)
    assert len(result.errors) == 1
    assert result.errors[0].project_id == case.project_id
    assert result.errors[0].code == "22012" and result.errors[0].outcome == "rolled_back"
    assert case.project_id not in result.successful_projects
    assert case.other_project_id in result.successful_projects
    assert result.counts.upcoming == 0 and result.counts.overdue == 3
    assert notifications(case) == audits(case) == []
    after = whole_database(case)
    for table in before.keys() - {"notifications", "audit_events"}:
        assert after[table] == before[table]
    assert len(after["notifications"]) - len(before["notifications"]) == 3
    assert len(after["audit_events"]) - len(before["audit_events"]) == 1


@pytest.mark.parametrize("change", ["completed", "due", "unapproved", "inactive"])
def test_current_state_is_rechecked_after_project_lock_wait(deadline_case, change):
    case = deadline_case
    with case.engine.connect() as blocker:
        transaction = blocker.begin()
        blocker.execute(text("SELECT id FROM public.projects WHERE id = :id FOR UPDATE"),
                        {"id": case.project_id})
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(check_deadlines, case.engine, apply=True, reference_time=REFERENCE)
            try:
                wait_for_project_waiters(case.engine, 1)
                if change == "completed":
                    blocker.execute(text("""
                        UPDATE public.stages SET status = 'completed', completed_at = :at WHERE id = :id
                    """), {"at": REFERENCE, "id": case.stages[0]["id"]})
                elif change == "due":
                    blocker.execute(text("UPDATE public.stages SET due_at = :at WHERE id = :id"), {
                        "at": REFERENCE + timedelta(days=7), "id": case.stages[0]["id"],
                    })
                elif change == "unapproved":
                    blocker.execute(text("""
                        UPDATE public.projects SET plan_approved_at = NULL, plan_approved_by = NULL WHERE id = :id
                    """), {"id": case.project_id})
                else:
                    blocker.execute(text("UPDATE public.users SET is_active = false WHERE id = :id"),
                                    {"id": case.actors["student"]["id"]})
                transaction.commit()
                result = future.result(timeout=12)
            finally:
                if transaction.is_active:
                    transaction.rollback()
    assert result.errors == []
    assert result.counts.upcoming == (2 if change == "inactive" else 0)
    assert len(notifications(case)) == result.counts.upcoming
    if change == "inactive":
        assert result.counts.inactive_recipients == 1


def test_lock_timeout_is_explicit_and_does_not_write(deadline_case):
    case = deadline_case
    before = whole_database(case)
    with case.engine.connect() as blocker:
        transaction = blocker.begin()
        blocker.execute(text("SELECT id FROM public.projects WHERE id = :id FOR UPDATE"),
                        {"id": case.project_id})
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(check_deadlines, case.engine, apply=True, reference_time=REFERENCE)
            try:
                wait_for_project_waiters(case.engine, 1)
                result = future.result(timeout=10)
            finally:
                transaction.rollback()
    assert len(result.errors) == 1 and result.errors[0].code == "55P03"
    assert result.errors[0].outcome == "rolled_back"
    assert whole_database(case) == before


CLI_RUNNER = """
import json, runpy, sys
from datetime import datetime, timezone
from unittest.mock import patch
payload = json.load(sys.stdin)
sys.argv = ['scripts.check_deadlines', *payload['args']]
class Clock(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2035, 5, 10, 12, tzinfo=timezone.utc)
with patch('dotenv.dotenv_values', return_value=payload['config']) as loader:
    with patch('app.deadlines.datetime', Clock):
        runpy.run_module('scripts.check_deadlines', run_name='__main__')
"""


def cli(config, args):
    # Only the disposable connection configuration crosses into the child;
    # no dotenv file is opened and credentials do not appear in argv/output.
    environment = {
        key: os.environ[key] for key in ("SYSTEMROOT", "PATH", "TEMP", "TMP") if key in os.environ
    }
    environment["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable, "-c", CLI_RUNNER],
        input=json.dumps({"config": config, "args": args}),
        capture_output=True, text=True, encoding="utf-8", env=environment, timeout=30,
    )


@pytest.mark.parametrize("args", [
    ["--warning-hours", "0"], ["--warning-hours", "169"],
    ["--warning-hours", "1.5"], ["--warning-hours", "no"],
    ["--apply", "--dry-run"], ["--unknown"],
])
def test_cli_invalid_arguments_do_not_load_configuration(postgres_config, args):
    response = cli({}, args)
    assert response.returncode == 2
    assert "error:" in response.stderr
    assert "Ошибка конфигурации" not in response.stderr


def test_cli_default_explicit_simulation_apply_and_repetition(deadline_case, postgres_config):
    case = deadline_case
    before = whole_database(case)
    for args in ([], ["--dry-run", "--warning-hours", "1"]):
        response = cli(postgres_config, args)
        assert response.returncode == 0, response.stderr
        assert "Симуляция" in response.stdout and "Новых кандидатов, срок приближается: 3" in response.stdout
        assert whole_database(case) == before
    response = cli(postgres_config, ["--apply", "--warning-hours", "168"])
    assert response.returncode == 0, response.stderr
    assert "Применение" in response.stdout and "Вставлено, срок приближается: 3" in response.stdout
    response = cli(postgres_config, ["--apply"])
    assert response.returncode == 0, response.stderr
    assert "Вставлено, срок приближается: 0" in response.stdout
    assert "Уже существующих уведомлений: 3" in response.stdout
    assert len(audits(case)) == 1


def test_cli_infrastructure_failure_is_nonzero_and_redacted(postgres_config):
    config = dict(postgres_config, DB_USER="nonexistent-integration-role")
    response = cli(config, ["--apply"])
    assert response.returncode == 1
    assert "Ошибка БД" in response.stderr
    assert "Ошибок: 1" in response.stdout
    for secret in (config["DB_PASSWORD"], config["JWT_SECRET"], "nonexistent-integration-role"):
        assert secret not in response.stdout + response.stderr
    response = cli({}, [])
    assert response.returncode == 1 and "Ошибка конфигурации БД" in response.stderr


def test_service_rejects_invalid_window_and_naive_clock(backend):
    for hours in (0, 169, 1.5, True):
        with pytest.raises(ValueError):
            check_deadlines(backend.engine, warning_hours=hours, reference_time=REFERENCE)
    with pytest.raises(ValueError):
        check_deadlines(backend.engine, reference_time=REFERENCE.replace(tzinfo=None))
