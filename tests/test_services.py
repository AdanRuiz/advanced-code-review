"""Checks of the local services (PB-15, SPEC.md "Dashboard local").

They read compose.yaml and the service configs as data: no container engine or network needed.
"""

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
COMPOSE = ROOT / "compose.yaml"
SERVICES = ROOT / "services"


@pytest.fixture(scope="module")
def compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


def env(service: dict) -> dict:
    return {key: str(value) for key, value in service.get("environment", {}).items()}


def test_has_the_four_services(compose):
    assert set(compose["services"]) == {"grafana", "loki", "prometheus", "files"}


def test_images_are_pinned(compose):
    for name, service in compose["services"].items():
        image = service["image"]
        assert ":" in image.rsplit("/", 1)[-1], name
        assert not image.endswith(":latest"), name


def test_published_ports_bind_only_localhost(compose):
    published = {}
    for name, service in compose["services"].items():
        for port in service.get("ports", []):
            assert isinstance(port, str), name
            assert port.startswith("127.0.0.1:"), f"{name} publica {port} fuera de localhost"
            published[name] = port
    assert published == {"grafana": "127.0.0.1:3000:3000", "files": "127.0.0.1:8080:8080"}


def test_loki_and_prometheus_are_internal_only(compose):
    assert compose["networks"]["backend"]["internal"] is True
    for name in ("loki", "prometheus"):
        service = compose["services"][name]
        assert "ports" not in service and "expose" not in service, name
        assert service["networks"] == ["backend"], name
        assert service.get("network_mode") is None, name


def test_no_service_uses_host_network_or_privileges(compose):
    for name, service in compose["services"].items():
        assert service.get("network_mode") is None, name
        assert not service.get("privileged"), name
        assert service["cap_drop"] == ["ALL"], name
        assert "no-new-privileges:true" in service["security_opt"], name
        for volume in service.get("volumes", []):
            assert "docker.sock" not in volume and "podman.sock" not in volume, name


def test_grafana_requires_login(compose):
    variables = env(compose["services"]["grafana"])
    assert variables["GF_AUTH_ANONYMOUS_ENABLED"] == "false"
    assert variables["GF_USERS_ALLOW_SIGN_UP"] == "false"
    assert variables["GF_PUBLIC_DASHBOARDS_ENABLED"] == "false"
    # The password has no default: compose stops if it is not set.
    assert re.fullmatch(r"\$\{ACR_GRAFANA_ADMIN_PASSWORD:\?[^}]*\}", variables["GF_SECURITY_ADMIN_PASSWORD"])


def test_grafana_sends_nothing_out(compose):
    variables = env(compose["services"]["grafana"])
    for key in (
        "GF_SNAPSHOTS_EXTERNAL_ENABLED",
        "GF_ANALYTICS_REPORTING_ENABLED",
        "GF_ANALYTICS_CHECK_FOR_UPDATES",
        "GF_ANALYTICS_CHECK_FOR_PLUGIN_UPDATES",
        "GF_NEWS_NEWS_FEED_ENABLED",
    ):
        assert variables[key] == "false", key


def test_files_serves_reports_read_only_with_password(compose):
    service = compose["services"]["files"]
    assert "./reports:/srv/reports:ro" in service["volumes"]
    assert all(volume.endswith(":ro") for volume in service["volumes"])
    assert service["read_only"] is True
    variables = env(service)
    assert re.fullmatch(r"\$\{ACR_FILES_PASSWORD:\?[^}]*\}", variables["ACR_FILES_PASSWORD"])


def test_config_files_exist(compose):
    for service in compose["services"].values():
        for volume in service.get("volumes", []):
            source = volume.split(":", 1)[0]
            if source.startswith("./") and source != "./reports":
                assert (ROOT / source).exists(), source


def test_caddyfile_requires_auth_and_does_not_list():
    caddyfile = (SERVICES / "files" / "Caddyfile").read_text(encoding="utf-8")
    assert "basic_auth" in caddyfile
    assert "{$ACR_FILES_USER} {$ACR_FILES_HASH}" in caddyfile
    assert "browse" not in caddyfile
    assert "admin off" in caddyfile
    assert re.search(r"@write not method GET HEAD", caddyfile)
    assert "@hidden path_regexp /\\." in caddyfile
    assert "sandbox" in caddyfile


def _caddy_matcher(caddyfile: str, name: str) -> re.Pattern:
    match = re.search(rf"@{name} path_regexp (\S+)", caddyfile)
    assert match, name
    return re.compile(match.group(1))


def test_caddyfile_export_buttons_with_many_teams_go_to_the_zip():
    # PB-23: with equipo = Todos or several teams the PDF/MD buttons download todos.zip.
    caddyfile = (SERVICES / "files" / "Caddyfile").read_text(encoding="utf-8")
    assert "redir @many_teams /reports/todos.zip 302" in caddyfile
    many = _caddy_matcher(caddyfile, "many_teams")
    for path in (
        "/reports/{equipo-01,equipo-02}/reporte.pdf",
        "/reports/%7Bequipo-01,equipo-02%7D/reporte.md",
        "/reports/$__all/reporte.pdf",
        "/reports/equipo-01,equipo-02/reporte.pdf",
        "/reports//reporte.pdf",
    ):
        assert many.search(path), path
    for path in ("/reports/equipo-01/reporte.pdf", "/reports/equipo-01/reporte.md", "/reports/todos.zip"):
        assert not many.search(path), path


def test_caddyfile_exports_download_as_attachment():
    caddyfile = (SERVICES / "files" / "Caddyfile").read_text(encoding="utf-8")
    assert "header @export Content-Disposition attachment" in caddyfile
    export = _caddy_matcher(caddyfile, "export")
    for path in ("/reports/todos.zip", "/reports/equipo-01/reporte.pdf", "/reports/equipo-01/reporte.md"):
        assert export.search(path), path
    for path in ("/reports/status.json", "/reports/equipo-01/scc.json", "/reports/dolos/pairs.csv"):
        assert not export.search(path), path


def test_entrypoint_refuses_empty_credentials():
    script = (SERVICES / "files" / "entrypoint.sh").read_text(encoding="utf-8")
    assert 'if [ -z "${ACR_FILES_USER:-}" ] || [ -z "${ACR_FILES_PASSWORD:-}" ]' in script
    assert "exit 1" in script
    assert "unset ACR_FILES_PASSWORD" in script


def test_loki_sends_no_usage_stats():
    config = yaml.safe_load((SERVICES / "loki" / "loki.yaml").read_text(encoding="utf-8"))
    assert config["analytics"]["reporting_enabled"] is False


def test_prometheus_config_is_valid_yaml():
    config = yaml.safe_load((SERVICES / "prometheus" / "prometheus.yml").read_text(encoding="utf-8"))
    assert config["scrape_configs"]


def test_env_file_is_ignored():
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".env" in ignored
    assert "reports/" in ignored
