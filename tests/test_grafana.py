"""Checks of the Grafana provisioning (PB-17, SPEC.md "Dashboard local" > "Navegación").

They read the provisioning files and dashboards as data: no Grafana, container engine or network.
"""

import json
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
GRAFANA = ROOT / "services" / "grafana"
DASHBOARDS = GRAFANA / "dashboards"
TAG = "advanced-code-review"
INFINITY = "yesoreyeram-infinity-datasource"

# Pages of SPEC.md "Dashboard local", in navbar order (Grafana sorts tag links by title).
PAGES = {
    "resumen": "1. Resumen",
    "equipo": "2. Equipo",
    "calidad": "3. Calidad",
    "seguridad": "4. Seguridad",
    "plagio": "5. Plagio",
    "calificacion": "6. Calificación",
}
WITH_TOOL = {"calidad", "seguridad"}


@pytest.fixture(scope="module")
def compose() -> dict:
    return yaml.safe_load((ROOT / "compose.yaml").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def datasources() -> dict:
    path = GRAFANA / "provisioning" / "datasources" / "datasources.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return {source["uid"]: source for source in data["datasources"]}


@pytest.fixture(scope="module")
def dashboards() -> dict:
    return {
        path.stem: json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(DASHBOARDS.glob("*.json"))
    }


def variables(dashboard: dict) -> dict:
    return {variable["name"]: variable for variable in dashboard["templating"]["list"]}


# --- compose ----------------------------------------------------------------------------------


def test_grafana_mounts_provisioning_read_only(compose):
    volumes = compose["services"]["grafana"]["volumes"]
    assert "./services/grafana/provisioning:/etc/grafana/provisioning:ro" in volumes
    assert "./services/grafana/dashboards:/etc/grafana/dashboards:ro" in volumes


def test_infinity_plugin_is_pinned_and_not_auto_updated(compose):
    env = {key: str(value) for key, value in compose["services"]["grafana"]["environment"].items()}
    assert re.fullmatch(rf"{INFINITY}@\d+\.\d+\.\d+", env["GF_PLUGINS_PREINSTALL_SYNC"])
    assert env["GF_PLUGINS_PREINSTALL_AUTO_UPDATE"] == "false"
    assert env["GF_ANALYTICS_CHECK_FOR_PLUGIN_UPDATES"] == "false"


def test_grafana_gets_file_server_password_without_default(compose):
    env = compose["services"]["grafana"]["environment"]
    assert re.fullmatch(r"\$\{ACR_FILES_PASSWORD:\?[^}]*\}", env["ACR_FILES_PASSWORD"])
    assert env["ACR_FILES_USER"] == compose["services"]["files"]["environment"]["ACR_FILES_USER"]


def test_home_dashboard_is_resumen(compose):
    env = compose["services"]["grafana"]["environment"]
    assert env["GF_DASHBOARDS_DEFAULT_HOME_DASHBOARD_PATH"] == "/etc/grafana/dashboards/resumen.json"


# --- datasources ------------------------------------------------------------------------------


def test_infinity_reads_only_the_local_file_server(datasources):
    infinity = datasources["acr-infinity"]
    assert infinity["type"] == INFINITY
    assert infinity["jsonData"]["allowedHosts"] == ["http://files:8080"]
    assert infinity["editable"] is False


def test_infinity_credentials_come_from_environment(datasources):
    infinity = datasources["acr-infinity"]
    assert infinity["basicAuth"] is True
    assert infinity["jsonData"]["auth_method"] == "basicAuth"
    assert infinity["basicAuthUser"] == "$ACR_FILES_USER"
    assert infinity["secureJsonData"] == {"basicAuthPassword": "$ACR_FILES_PASSWORD"}


def test_prometheus_is_the_internal_service(datasources):
    prometheus = datasources["acr-prometheus"]
    assert prometheus["type"] == "prometheus"
    assert prometheus["url"] == "http://prometheus:9090"


def test_dashboard_provider_loads_the_mounted_folder():
    path = GRAFANA / "provisioning" / "dashboards" / "advanced-code-review.yaml"
    (provider,) = yaml.safe_load(path.read_text(encoding="utf-8"))["providers"]
    assert provider["type"] == "file"
    assert provider["options"]["path"] == "/etc/grafana/dashboards"
    assert provider["allowUiUpdates"] is False


# --- dashboards -------------------------------------------------------------------------------


def test_there_are_the_six_pages(dashboards):
    assert {slug: dashboard["title"] for slug, dashboard in dashboards.items()} == PAGES
    assert sorted(PAGES.values()) == list(PAGES.values())  # navbar order


def test_uids_are_unique_and_stable(dashboards):
    assert {dashboard["uid"] for dashboard in dashboards.values()} == {
        f"acr-{slug}" for slug in PAGES
    }


def test_every_page_has_the_tag_and_the_navbar(dashboards):
    for slug, dashboard in dashboards.items():
        assert TAG in dashboard["tags"], slug
        (link,) = [link for link in dashboard["links"] if link["type"] == "dashboards"]
        assert link["type"] == "dashboards", slug
        assert link["tags"] == [TAG], slug
        assert link["asDropdown"] is False, slug  # buttons, not a dropdown
        assert link["includeVars"] is True, slug  # keeps "equipo" across pages
        assert link["keepTime"] is True, slug
        assert dashboard["editable"] is False, slug


def test_equipo_variable_on_every_page(dashboards):
    for slug, dashboard in dashboards.items():
        equipo = variables(dashboard)["equipo"]
        assert equipo["datasource"] == {"type": INFINITY, "uid": "acr-infinity"}, slug
        assert equipo["multi"] is True and equipo["includeAll"] is True, slug
        query = equipo["query"]["infinityQuery"]
        assert query["url"] == "http://files:8080/reports/status.json", slug
        assert query["root_selector"] == "teams", slug
        assert [column["selector"] for column in query["columns"]] == ["id"], slug


def test_herramienta_variable_only_on_quality_and_security(dashboards):
    for slug, dashboard in dashboards.items():
        found = variables(dashboard)
        if slug in WITH_TOOL:
            tool = found["herramienta"]
            assert tool["datasource"] == {"type": "prometheus", "uid": "acr-prometheus"}, slug
            assert tool["multi"] is True and tool["includeAll"] is True, slug
        else:
            assert "herramienta" not in found, slug
        assert set(found) <= {"equipo", "herramienta"}, slug


def test_dashboards_use_only_provisioned_datasources(dashboards, datasources):
    def uids(node):
        if isinstance(node, dict):
            if isinstance(node.get("datasource"), dict):
                yield node["datasource"]["uid"]
            for value in node.values():
                yield from uids(value)
        elif isinstance(node, list):
            for value in node:
                yield from uids(value)

    for slug, dashboard in dashboards.items():
        assert set(uids(dashboard)) <= set(datasources), slug


def test_infinity_urls_point_to_the_file_server(dashboards):
    for slug, dashboard in dashboards.items():
        without_links = {key: value for key, value in dashboard.items() if key != "links"}
        for url in re.findall(r'"url":\s*"(http[^"]*)"', json.dumps(without_links)):
            assert url.startswith("http://files:8080/reports/"), (slug, url)


# --- export buttons (PB-23, SPEC.md "Exportación de reportes") ---------------------------------

EXPORT_LINKS = {
    "PDF equipo": "http://localhost:8080/reports/${equipo}/reporte.pdf",
    "MD equipo": "http://localhost:8080/reports/${equipo}/reporte.md",
    "ZIP todos": "http://localhost:8080/reports/todos.zip",
}


def test_every_page_has_the_export_buttons_after_the_navbar(dashboards):
    for slug, dashboard in dashboards.items():
        links = dashboard["links"]
        assert links[0]["type"] == "dashboards", slug
        exports = links[1:]
        assert {link["title"]: link["url"] for link in exports} == EXPORT_LINKS, slug
        assert [link["title"] for link in exports] == list(EXPORT_LINKS), slug  # navbar order
        for link in exports:
            assert link["type"] == "link", (slug, link["title"])
            assert link["targetBlank"] is True, (slug, link["title"])
            # Grafana variables in the query string are not needed and would leak into the URL.
            assert link["includeVars"] is False and link["keepTime"] is False, (slug, link["title"])


def test_export_buttons_use_the_browser_side_file_server(compose):
    # The browser reaches the file server on localhost:8080, published only on 127.0.0.1.
    assert "127.0.0.1:8080:8080" in compose["services"]["files"]["ports"]
    for url in EXPORT_LINKS.values():
        assert url.startswith("http://localhost:8080/reports/")
