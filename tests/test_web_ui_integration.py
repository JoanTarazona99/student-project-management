import pytest


def test_ui_shell_assets_and_existing_system_contracts(scenario):
    client = scenario.client
    for path in ("/ui", "/ui/"):
        response = client.get(path)
        assert response.status_code == 200
        assert '<html lang="ru">' in response.text
        assert "Управление учебными проектами студентов" in response.text
        assert "unsafe-inline" not in response.headers["content-security-policy"]
        assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
        assert response.headers["cache-control"] == "no-store"
    for path, media in (("/ui/assets/app.js", "text/javascript"), ("/ui/assets/style.css", "text/css")):
        response = client.get(path)
        assert response.status_code == 200
        assert response.headers["content-type"].startswith(media)
        assert response.headers["x-content-type-options"] == "nosniff"
    assert client.get("/").json()["documentation"] == "/docs"
    assert client.get("/health").json()["status"] == "ok"
    assert client.get("/docs").status_code == 200
    assert "content-security-policy" not in client.get("/docs").headers


@pytest.mark.parametrize("path", [
    "/ui/assets/.env", "/ui/assets/database/01_schema_student_projects.sql",
    "/ui/assets/uploads/file.bin", "/ui/assets/backups/backup.sql",
    "/ui/.env", "/ui/assets/%2e%2e/%2e%2e/.env", "/ui/assets/index.html",
])
def test_ui_does_not_serve_private_or_arbitrary_files(scenario, path):
    assert scenario.client.get(path).status_code == 404
