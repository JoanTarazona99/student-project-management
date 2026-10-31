import importlib
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
TEST_PASSWORD = "integration-only-not-a-real-password"


def pytest_addoption(parser):
    parser.addoption("--postgres-backend", choices=("docker", "native"), default="docker")
    parser.addoption("--pg-bin", default=None, help="Directory containing initdb and postgres")


def checked(command, **kwargs):
    try:
        return subprocess.run(command, check=True, capture_output=True, text=True, **kwargs)
    except subprocess.CalledProcessError as error:
        details = (error.stderr or error.stdout or str(error.returncode)).strip()
        raise RuntimeError(f"{Path(command[0]).name} failed: {details[-4000:]}") from error


@pytest.fixture(scope="session")
def postgres_config(pytestconfig):
    """Only connect to a server created here; never accept an external DSN."""
    backend = pytestconfig.getoption("--postgres-backend")
    password = secrets.token_urlsafe(32)
    config = {
        "DB_HOST": "127.0.0.1",
        "DB_NAME": "student_projects",
        "DB_USER": "spm_dev",
        "DB_PASSWORD": password,
        "JWT_SECRET": secrets.token_urlsafe(48),
    }
    with tempfile.TemporaryDirectory(prefix="spm-integration-pg-") as directory:
        root = Path(directory)
        process = None
        container = None
        log = None
        try:
            if backend == "docker":
                if not shutil.which("docker"):
                    raise RuntimeError("Docker is required; alternatively use --postgres-backend=native.")
                checked(["docker", "info"])
                name = "spm-integration-" + uuid4().hex
                container = checked([
                    "docker", "run", "--detach", "--rm", "--name", name,
                    "--label", "spm.integration-test=true",
                    "--publish", "127.0.0.1::5432",
                    "--tmpfs", "/var/lib/postgresql/data",
                    "--env", "POSTGRES_DB=postgres",
                    "--env", "POSTGRES_USER=spm_dev",
                    "--env", f"POSTGRES_PASSWORD={password}",
                    "postgres:17",
                ]).stdout.strip()
                binding = checked(["docker", "port", container, "5432/tcp"]).stdout.strip()
                config["DB_PORT"] = binding.rsplit(":", 1)[1]
            else:
                bin_dir = pytestconfig.getoption("--pg-bin")
                suffix = ".exe" if os.name == "nt" else ""
                initdb = str(Path(bin_dir) / ("initdb" + suffix)) if bin_dir else shutil.which("initdb")
                postgres = str(Path(bin_dir) / ("postgres" + suffix)) if bin_dir else shutil.which("postgres")
                if not initdb or not postgres:
                    raise RuntimeError("initdb and postgres are required; specify --pg-bin.")
                password_file = root / "password"
                password_file.write_text(password, encoding="ascii")
                data = root / "data"
                checked([
                    initdb, "-D", str(data), "-U", "spm_dev",
                    "--pwfile", str(password_file), "--auth=scram-sha-256",
                    "--encoding=UTF8", "--no-locale",
                ])
                password_file.unlink()
                with socket.socket() as probe:
                    probe.bind(("127.0.0.1", 0))
                    port = probe.getsockname()[1]
                config["DB_PORT"] = str(port)
                log = (root / "postgres.log").open("w", encoding="utf-8")
                process = subprocess.Popen([
                    postgres, "-D", str(data), "-h", "127.0.0.1", "-p", str(port),
                    "-c", "timezone=UTC", "-c", "unix_socket_directories=",
                ], stdout=log, stderr=subprocess.STDOUT)

            connection_options = {
                "host": config["DB_HOST"], "port": int(config["DB_PORT"]),
                "user": config["DB_USER"], "password": password,
                "dbname": "postgres", "connect_timeout": 2,
            }
            deadline = time.monotonic() + 60
            while True:
                try:
                    with psycopg.connect(**connection_options, autocommit=True) as connection:
                        # Ownership comes from the process/container above, not these names.
                        connection.execute("CREATE DATABASE student_projects")
                    break
                except psycopg.OperationalError as error:
                    if time.monotonic() >= deadline or (process and process.poll() is not None):
                        raise RuntimeError("Disposable PostgreSQL did not become ready.") from error
                    time.sleep(0.2)
            connection_options["dbname"] = "student_projects"
            with psycopg.connect(**connection_options, autocommit=True) as connection:
                schema = (ROOT / "database" / "01_schema_student_projects.sql").read_text(encoding="utf-8")
                connection.execute(schema, prepare=False)
            yield config
        finally:
            if process is not None:
                # Only terminate the exact child launched by this fixture.
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=15)
            if log is not None:
                log.close()
            if container is not None:
                checked(["docker", "stop", "--time", "15", container])


@pytest.fixture(scope="session")
def backend(postgres_config):
    if "app.database" in sys.modules:
        raise RuntimeError("app.database was imported before test isolation; refusing to continue.")
    # Intercept the loader before import: the real .env is never opened.
    with patch("dotenv.dotenv_values", return_value=dict(postgres_config)) as loader:
        main = importlib.import_module("app.main")
        loader.assert_called_once()
    database = importlib.import_module("app.database")
    materials = importlib.import_module("app.materials")
    security = importlib.import_module("app.security")
    yield SimpleNamespace(
        app=main.app, engine=database.engine, materials=materials,
        password_hash=security.hash_password(TEST_PASSWORD),
    )
    database.engine.dispose()


@pytest.fixture
def upload_root():
    with tempfile.TemporaryDirectory(prefix="spm-integration-files-") as directory:
        yield Path(directory) / "materials"


@pytest.fixture
def scenario(backend, upload_root, monkeypatch):
    monkeypatch.setattr(backend.materials, "UPLOAD_ROOT", upload_root)
    role_codes = {
        "student": ["student"],
        "supervisor": ["supervisor"],
        "teacher": ["teacher"],
        "outsider": ["student"],
        "unassigned": ["supervisor"],
        "teacher2": ["teacher"],
        "department": ["department"],
        "teacher_department": ["teacher", "department"],
        "admin": ["admin"],
        "reports_only": ["integration-report-reader"],
    }
    with backend.engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO public.roles (code, name)
            VALUES ('integration-report-reader', 'Integration report reader')
            ON CONFLICT (code) DO NOTHING
        """))
        connection.execute(text("""
            INSERT INTO public.role_permissions (role_id, permission_id)
            SELECT r.id, p.id
            FROM public.roles r CROSS JOIN public.permissions p
            WHERE r.code = 'integration-report-reader' AND p.code = 'reports.read'
            ON CONFLICT DO NOTHING
        """))
    actors = {}
    for name, roles in role_codes.items():
        email = f"{name}-{uuid4().hex}@integration.invalid"
        with backend.engine.begin() as connection:
            user_id = connection.execute(text("""
                INSERT INTO public.users (email, password_hash, first_name, last_name)
                VALUES (:email, :hash, :name, 'Integration') RETURNING id
            """), {"email": email, "hash": backend.password_hash, "name": name}).scalar_one()
            connection.execute(text("""
                INSERT INTO public.user_roles (user_id, role_id)
                SELECT :user, id FROM public.roles WHERE code = :role
            """), [
                {"user": user_id, "role": role}
                for role in roles
            ])
        actors[name] = {"id": user_id, "email": email}

    with TestClient(backend.app) as client:
        for actor in actors.values():
            response = client.post("/auth/login", json={
                "email": actor["email"], "password": TEST_PASSWORD,
            })
            assert response.status_code == 200, response.text
            actor["headers"] = {"Authorization": "Bearer " + response.json()["access_token"]}
        case = Scenario(client, backend.engine, actors, upload_root)
        case.project_id = case.create_project("student", "supervisor")
        case.other_project_id = case.create_project("outsider", "unassigned", "teacher2")
        case.plan = case.request("student", "PUT", f"/projects/{case.project_id}/plan", 200, json=case.plan_input)
        case.stages = case.plan["stages"]
        yield case


class Scenario:
    def __init__(self, client, engine, actors, storage):
        self.client = client
        self.engine = engine
        self.actors = actors
        self.storage = storage
        self.plan_input = {"stages": [
            {"title": f"Stage {i}", "starts_at": f"2099-01-0{i}T00:00:00Z",
             "due_at": f"2099-01-0{i + 1}T00:00:00Z"}
            for i in range(1, 4)
        ]}

    def request(self, actor, method, path, status, **kwargs):
        response = self.client.request(method, path, headers=self.actors[actor]["headers"], **kwargs)
        assert response.status_code == status, response.text
        return response.json()

    def create_project(self, student, supervisor, teacher="teacher"):
        topic = self.request("teacher", "POST", "/topics", 201, json={"title": uuid4().hex})
        self.request("teacher", "POST", f"/topics/{topic['id']}/approve", 200)
        project = self.request(student, "POST", "/projects", 201, json={
            "topic_id": topic["id"], "teacher_id": self.actors[teacher]["id"],
        })
        self.request(teacher, "PUT", f"/projects/{project['id']}/supervisor", 200, json={
            "supervisor_id": self.actors[supervisor]["id"],
        })
        return project["id"]

    def approve(self):
        return self.request("supervisor", "POST", f"/projects/{self.project_id}/plan/approve", 200)

    def upload(self, stage=0, data=b"Integration material\n", filename="report.txt", status=201, actor="student"):
        return self.request(actor, "POST", f"/stages/{self.stages[stage]['id']}/materials", status,
                            files={"file": (filename, data, "application/octet-stream")})

    def review(self, material, decision="accepted", remarks="", status=201, actor="supervisor"):
        return self.request(actor, "POST", f"/materials/{material['id']}/review", status,
                            json={"decision": decision, "remarks": remarks})

    def status(self):
        return self.request("student", "GET", f"/projects/{self.project_id}/status", 200)

    def snapshot(self):
        queries = (
            "SELECT * FROM public.projects WHERE id = :project ORDER BY id",
            "SELECT * FROM public.stages WHERE project_id = :project ORDER BY id",
            """SELECT mv.* FROM public.material_versions mv JOIN public.stages s ON s.id = mv.stage_id
               WHERE s.project_id = :project ORDER BY mv.id""",
            """SELECT r.* FROM public.reviews r JOIN public.material_versions mv ON mv.id = r.material_version_id
               JOIN public.stages s ON s.id = mv.stage_id WHERE s.project_id = :project ORDER BY r.id""",
            "SELECT * FROM public.notifications WHERE project_id = :project ORDER BY id",
            "SELECT * FROM public.audit_events WHERE actor_id = ANY(:actors) ORDER BY id",
        )
        with self.engine.connect() as connection:
            rows = tuple(tuple(connection.execute(text(query), {
                "project": self.project_id, "actors": [actor["id"] for actor in self.actors.values()],
            }).all()) for query in queries)
        files = tuple(sorted((path.name, path.read_bytes()) for path in self.storage.glob("*")))
        return rows, files
