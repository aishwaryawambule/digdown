from digdown.discovery import discover_project


def test_discovers_services_and_depends_on(tmp_path):
    compose = tmp_path / "docker-compose.yml"
    compose.write_text(
        """
services:
  web:
    image: web:1
    depends_on:
      - db
      - redis
  db:
    image: postgres:16
  redis:
    image: redis:7
"""
    )
    proj = discover_project(str(compose))
    assert set(proj.services) == {"web", "db", "redis"}
    assert proj.depends_on["web"] == ["db", "redis"]
    assert proj.depends_on["db"] == []


def test_handles_dict_form_depends_on(tmp_path):
    compose = tmp_path / "docker-compose.yml"
    compose.write_text(
        """
services:
  app:
    image: app:1
    depends_on:
      db:
        condition: service_healthy
  db:
    image: postgres:16
"""
    )
    proj = discover_project(str(compose))
    assert proj.depends_on["app"] == ["db"]


def test_missing_file_returns_empty_project(tmp_path):
    proj = discover_project(str(tmp_path / "nope.yml"))
    assert proj.services == []
    assert proj.depends_on == {}


def test_discovers_project_name(tmp_path):
    compose = tmp_path / "docker-compose.yml"
    compose.write_text("name: myproj\nservices:\n  web:\n    image: web:1\n")
    proj = discover_project(str(compose))
    assert proj.name == "myproj"
    assert "web" in proj.services


def test_project_name_defaults_empty(tmp_path):
    compose = tmp_path / "docker-compose.yml"
    compose.write_text("services:\n  web:\n    image: web:1\n")
    assert discover_project(str(compose)).name == ""
