"""PB-24: orchestrator API for the event and the teams over equipos.yml (SPEC.md "API del
orquestador", "Seguridad del orquestador")."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from acr import cli
from acr.config import load_config
from acr.orchestrator import create_app, is_loopback

EVENT = {
    "nombre": "Hackathon 2026",
    "inicio": "2026-11-01T09:00:00-06:00",
    "fin": "2026-11-03T18:00:00-06:00",
}


def team(team_id: str = "equipo-01", **extra) -> dict:
    data = {
        "id": team_id,
        "nombre": "Equipo Alfa",
        "proyecto": "Proyecto",
        "objetivo": "Objetivo",
        "repo": "https://github.com/org/alfa",
    }
    data.update(extra)
    return data


def write_config(path: Path, teams: list[dict] | None = None) -> None:
    data = {"evento": dict(EVENT), "equipos": teams if teams is not None else [team()]}
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")


def local_client(config: Path) -> TestClient:
    return TestClient(create_app(config), base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000))


@pytest.fixture
def config(tmp_path: Path) -> Path:
    path = tmp_path / "equipos.yml"
    write_config(path)
    return path


@pytest.fixture
def client(config: Path) -> TestClient:
    return local_client(config)


def saved(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


# --- Evento ---------------------------------------------------------------------------------


def test_get_event(client):
    response = client.get("/api/evento")
    assert response.status_code == 200
    assert response.json() == EVENT


def test_put_event_writes_file_and_stays_valid(client, config):
    new = {
        "nombre": "Hackathon 2027",
        "inicio": "2027-01-01T09:00:00-06:00",
        "fin": "2027-01-02T18:00:00-06:00",
    }
    response = client.put("/api/evento", json=new)
    assert response.status_code == 200
    assert response.json() == new
    loaded, errors = load_config(config)
    assert errors == []
    assert loaded.evento.nombre == "Hackathon 2027"
    assert [t.id for t in loaded.equipos] == ["equipo-01"]  # teams untouched


def test_put_event_rejects_invalid_dates_and_does_not_write(client, config):
    before = config.read_text(encoding="utf-8")
    response = client.put("/api/evento", json={**EVENT, "fin": "2026-10-01T00:00:00-06:00"})
    assert response.status_code == 422
    assert response.json()["detail"][0]["location"] == "evento.fin"
    assert config.read_text(encoding="utf-8") == before


def test_put_event_rejects_unknown_fields(client):
    response = client.put("/api/evento", json={**EVENT, "token": "x"})
    assert response.status_code == 422
    assert "token" in response.json()["detail"][0]["message"]


def test_get_event_without_file_is_404_and_put_creates_it(tmp_path):
    path = tmp_path / "equipos.yml"
    client = local_client(path)
    assert client.get("/api/evento").status_code == 404
    assert client.get("/api/equipos").json() == []
    assert client.put("/api/evento", json=EVENT).status_code == 200
    assert saved(path)["equipos"] == []
    assert client.post("/api/equipos", json=team()).status_code == 201
    assert load_config(path)[1] == []


# --- Equipos --------------------------------------------------------------------------------


def test_list_teams(client):
    response = client.get("/api/equipos")
    assert response.status_code == 200
    assert response.json() == [team()]


def test_create_team(client, config):
    new = team("equipo-02", rama="main", integrantes=[{"nombre": "Ana", "github": "ana"}])
    response = client.post("/api/equipos", json=new)
    assert response.status_code == 201
    assert response.json() == new
    loaded, errors = load_config(config)
    assert errors == []
    assert [t.id for t in loaded.equipos] == ["equipo-01", "equipo-02"]


def test_create_team_drops_null_fields(client, config):
    response = client.post("/api/equipos", json=team("equipo-02", rama=None))
    assert response.status_code == 201
    assert "rama" not in saved(config)["equipos"][1]


def test_create_team_with_duplicate_id_is_409(client, config):
    before = config.read_text(encoding="utf-8")
    response = client.post("/api/equipos", json=team())
    assert response.status_code == 409
    assert config.read_text(encoding="utf-8") == before


@pytest.mark.parametrize("bad_id", ["../etc", "Equipo", "-x", "a/b", ""])
def test_create_team_rejects_invalid_id(client, config, bad_id):
    response = client.post("/api/equipos", json=team(bad_id))
    assert response.status_code == 422
    assert response.json()["detail"][0]["location"] == "equipo.id"
    assert len(saved(config)["equipos"]) == 1


def test_create_team_reports_every_problem(client):
    response = client.post("/api/equipos", json={"id": "equipo-02", "nombre": " ", "rama": 5})
    assert response.status_code == 422
    locations = {e["location"] for e in response.json()["detail"]}
    assert {"equipo.nombre", "equipo.proyecto", "equipo.objetivo", "equipo.repo", "equipo.rama"} <= locations


def test_create_team_rejects_unknown_fields_and_non_objects(client):
    assert client.post("/api/equipos", json=team("equipo-02", token="secreto")).status_code == 422
    assert client.post("/api/equipos", json=["equipo-02"]).status_code == 422


def test_create_team_checks_local_pdf_relative_to_config(client, config):
    response = client.post("/api/equipos", json=team("equipo-02", prompts_ia="falta.pdf"))
    assert response.status_code == 422
    (config.parent / "prompts.pdf").write_bytes(b"%PDF-1.7\n")
    response = client.post("/api/equipos", json=team("equipo-02", prompts_ia="prompts.pdf"))
    assert response.status_code == 201


def test_update_team(client, config):
    response = client.put("/api/equipos/equipo-01", json=team(nombre="Nuevo nombre", commit="abc1234"))
    assert response.status_code == 200
    loaded, errors = load_config(config)
    assert errors == []
    assert loaded.equipos[0].nombre == "Nuevo nombre"
    assert loaded.equipos[0].commit == "abc1234"


def test_update_team_takes_id_from_path(client, config):
    body = team()
    del body["id"]
    assert client.put("/api/equipos/equipo-01", json=body).status_code == 200
    assert saved(config)["equipos"][0]["id"] == "equipo-01"


def test_update_team_cannot_change_id(client):
    response = client.put("/api/equipos/equipo-01", json=team("equipo-09"))
    assert response.status_code == 422


def test_update_missing_team_is_404(client):
    assert client.put("/api/equipos/equipo-77", json=team("equipo-77")).status_code == 404


def test_update_team_validates(client, config):
    before = config.read_text(encoding="utf-8")
    response = client.put("/api/equipos/equipo-01", json=team(repo=""))
    assert response.status_code == 422
    assert config.read_text(encoding="utf-8") == before


@pytest.mark.parametrize("bad_id", ["..", "Equipo-01", "a%2Fb", "%2E%2E"])
def test_invalid_path_id_is_rejected(client, config, bad_id):
    assert client.put(f"/api/equipos/{bad_id}", json=team()).status_code in (404, 422)
    assert client.delete(f"/api/equipos/{bad_id}").status_code in (404, 422)
    assert len(saved(config)["equipos"]) == 1


def test_delete_team(config):
    write_config(config, [team(), team("equipo-02")])
    client = local_client(config)
    response = client.delete("/api/equipos/equipo-01")
    assert response.status_code == 204
    assert [t["id"] for t in saved(config)["equipos"]] == ["equipo-02"]
    assert client.delete("/api/equipos/equipo-01").status_code == 404


def test_broken_yaml_is_never_overwritten(config):
    config.write_text("evento: [sin cerrar\n", encoding="utf-8")
    client = local_client(config)
    assert client.get("/api/equipos").status_code == 409
    assert client.post("/api/equipos", json=team("equipo-02")).status_code == 409
    assert client.put("/api/evento", json=EVENT).status_code == 409
    assert config.read_text(encoding="utf-8") == "evento: [sin cerrar\n"


def test_write_leaves_no_temporary_files(client, config):
    client.post("/api/equipos", json=team("equipo-02"))
    assert sorted(p.name for p in config.parent.iterdir()) == ["equipos.yml"]


# --- Solo localhost -------------------------------------------------------------------------


def test_rejects_non_loopback_client(config):
    client = TestClient(create_app(config), base_url="http://127.0.0.1:8000", client=("192.168.1.20", 50000))
    assert client.get("/api/equipos").status_code == 403
    assert client.post("/api/equipos", json=team("equipo-02")).status_code == 403
    assert len(saved(config)["equipos"]) == 1


def test_rejects_foreign_host_header(config):
    # DNS rebinding: a page on evil.example resolves to 127.0.0.1 but keeps its Host header.
    client = TestClient(create_app(config), base_url="http://evil.example:8000", client=("127.0.0.1", 50000))
    assert client.get("/api/equipos").status_code == 403


def test_rejects_foreign_origin(client, config):
    response = client.delete("/api/equipos/equipo-01", headers={"Origin": "https://evil.example"})
    assert response.status_code == 403
    assert len(saved(config)["equipos"]) == 1


def test_accepts_localhost_origin(client):
    assert client.get("/api/equipos", headers={"Origin": "http://localhost:3000"}).status_code == 200


def test_no_public_docs(client):
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404


@pytest.mark.parametrize(
    ("host", "expected"),
    [("127.0.0.1", True), ("127.5.0.1", True), ("::1", True), ("[::1]", True), ("localhost", True),
     ("0.0.0.0", False), ("192.168.1.2", False), ("evil.example", False), ("", False), (None, False)],
)
def test_is_loopback(host, expected):
    assert is_loopback(host) is expected


# --- CLI ------------------------------------------------------------------------------------


def test_serve_binds_to_localhost_only(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr("uvicorn.run", lambda app, **kwargs: calls.append(kwargs))
    assert cli.main(["-c", str(tmp_path / "equipos.yml"), "serve", "--port", "8123"]) == 0
    assert calls == [{"host": "127.0.0.1", "port": 8123, "log_level": "info"}]


def test_serve_has_no_host_option():
    with pytest.raises(SystemExit):
        cli.main(["serve", "--host", "0.0.0.0"])


def test_serve_rejects_invalid_port():
    with pytest.raises(SystemExit):
        cli.main(["serve", "--port", "70000"])
