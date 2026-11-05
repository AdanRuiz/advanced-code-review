"""Tests for loading and validating equipos.yml (PB-5, SPEC.md "Entrada"), one per rule."""

import copy
from datetime import datetime
from pathlib import Path

import pytest
import yaml

from acr.cli import EXIT_ERROR, EXIT_OK, main
from acr.config import is_valid_team_id, load_config, validate_data

REPO_ROOT = Path(__file__).resolve().parent.parent

VALID = {
    "evento": {
        "nombre": "Hackathon 2026",
        "inicio": "2026-11-01T09:00:00-06:00",
        "fin": "2026-11-03T18:00:00-06:00",
    },
    "equipos": [
        {
            "id": "equipo-01",
            "nombre": "Equipo Alfa",
            "proyecto": "Proyecto A",
            "objetivo": "Objetivo A",
            "repo": "https://github.com/org/a",
            "rama": "main",
            "commit": "a1b2c3d",
        },
        {
            "id": "equipo-02",
            "nombre": "Equipo Beta",
            "proyecto": "Proyecto B",
            "objetivo": "Objetivo B",
            "repo": "C:/entregas/b.zip",
        },
    ],
}


def data():
    return copy.deepcopy(VALID)


def locations(errors):
    return [e.location for e in errors]


def write(tmp_path, content):
    path = tmp_path / "equipos.yml"
    path.write_text(content if isinstance(content, str) else yaml.safe_dump(content), encoding="utf-8")
    return path


# --- valid input -------------------------------------------------------------------------------


def test_valid_data_builds_config():
    config, errors = validate_data(data())
    assert errors == []
    assert config is not None
    assert config.evento.nombre == "Hackathon 2026"
    assert config.evento.inicio == datetime.fromisoformat("2026-11-01T09:00:00-06:00")
    assert [t.id for t in config.equipos] == ["equipo-01", "equipo-02"]
    assert config.equipos[0].commit == "a1b2c3d"
    assert config.equipos[1].rama is None and config.equipos[1].commit is None


def test_example_file_is_valid():
    config, errors = load_config(REPO_ROOT / "equipos.example.yml")
    assert errors == []
    assert config is not None and len(config.equipos) == 3


def test_yaml_timestamps_are_accepted(tmp_path):
    # Unquoted dates in YAML are parsed as datetime objects by PyYAML.
    text = yaml.safe_dump(data()).replace("'2026-11-01T09:00:00-06:00'", "2026-11-01T09:00:00-06:00")
    config, errors = load_config(write(tmp_path, text))
    assert errors == []
    assert config.evento.inicio.utcoffset() is not None


# --- missing fields ----------------------------------------------------------------------------


@pytest.mark.parametrize("field", ["nombre", "inicio", "fin"])
def test_missing_event_field(field):
    d = data()
    del d["evento"][field]
    config, errors = validate_data(d)
    assert config is None
    assert f"evento.{field}" in locations(errors)


@pytest.mark.parametrize("field", ["id", "nombre", "proyecto", "objetivo", "repo"])
def test_missing_team_field(field):
    d = data()
    del d["equipos"][1][field]
    config, errors = validate_data(d)
    assert config is None
    assert f"equipos[1].{field}" in locations(errors)
    assert any("falta" in e.message for e in errors)


@pytest.mark.parametrize("field", ["evento", "equipos"])
def test_missing_top_level_section(field):
    d = data()
    del d[field]
    config, errors = validate_data(d)
    assert config is None
    assert field in locations(errors)


def test_empty_team_list():
    d = data()
    d["equipos"] = []
    _, errors = validate_data(d)
    assert locations(errors) == ["equipos"]


def test_blank_text_field():
    d = data()
    d["equipos"][0]["nombre"] = "   "
    _, errors = validate_data(d)
    assert locations(errors) == ["equipos[0].nombre"]


def test_optional_fields_are_optional():
    d = data()
    del d["equipos"][0]["rama"]
    del d["equipos"][0]["commit"]
    config, errors = validate_data(d)
    assert errors == [] and config is not None


def test_numeric_commit_must_be_quoted():
    d = data()
    d["equipos"][0]["commit"] = 1234567
    _, errors = validate_data(d)
    assert locations(errors) == ["equipos[0].commit"]
    assert "comillas" in errors[0].message


def test_reports_all_errors_at_once():
    d = data()
    del d["evento"]["nombre"]
    del d["equipos"][0]["repo"]
    d["equipos"][1]["id"] = "Equipo 2"
    _, errors = validate_data(d)
    assert set(locations(errors)) == {"evento.nombre", "equipos[0].repo", "equipos[1].id"}


# --- invalid or repeated ids -------------------------------------------------------------------


@pytest.mark.parametrize("value", ["equipo-01", "a", "123", "team-1-b"])
def test_valid_ids(value):
    assert is_valid_team_id(value)


@pytest.mark.parametrize(
    "value",
    ["Equipo-01", "equipo_01", "equipo 01", "../etc", "a/b", "a\\b", "-equipo", "", "équipo", 12, None],
)
def test_invalid_ids(value):
    assert not is_valid_team_id(value)
    d = data()
    d["equipos"][0]["id"] = value
    config, errors = validate_data(d)
    assert config is None
    assert "equipos[0].id" in locations(errors)


def test_repeated_id():
    d = data()
    d["equipos"][1]["id"] = "equipo-01"
    config, errors = validate_data(d)
    assert config is None
    assert locations(errors) == ["equipos[1].id"]
    assert "repetido" in errors[0].message and "equipos[0]" in errors[0].message


# --- malformed dates ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    ["2026-13-01T09:00:00-06:00", "ayer", "01/11/2026 09:00", "2026-11-01", 20261101, True],
)
def test_malformed_date(value):
    d = data()
    d["evento"]["inicio"] = value
    config, errors = validate_data(d)
    assert config is None
    assert locations(errors) == ["evento.inicio"]
    assert "mal formada" in errors[0].message


def test_bare_yaml_date_is_rejected(tmp_path):
    text = yaml.safe_dump(data()).replace("'2026-11-03T18:00:00-06:00'", "2026-11-03")
    _, errors = load_config(write(tmp_path, text))
    assert locations(errors) == ["evento.fin"]


def test_end_before_start():
    d = data()
    d["evento"]["inicio"], d["evento"]["fin"] = d["evento"]["fin"], d["evento"]["inicio"]
    _, errors = validate_data(d)
    assert locations(errors) == ["evento.fin"]


def test_mixed_timezone_awareness():
    d = data()
    d["evento"]["fin"] = "2026-11-03T18:00:00"
    _, errors = validate_data(d)
    assert locations(errors) == ["evento"]


# --- file handling -----------------------------------------------------------------------------


def test_missing_file(tmp_path):
    config, errors = load_config(tmp_path / "no-existe.yml")
    assert config is None
    assert "no existe" in errors[0].message


def test_invalid_yaml(tmp_path):
    _, errors = load_config(write(tmp_path, "evento: [sin cerrar\n"))
    assert "YAML inválido" in errors[0].message


@pytest.mark.parametrize("content", ["", "- solo\n- una lista\n"])
def test_not_a_mapping(tmp_path, content):
    config, errors = load_config(write(tmp_path, content))
    assert config is None
    assert len(errors) == 1


# --- CLI ---------------------------------------------------------------------------------------


def test_cli_validate_ok(tmp_path, capsys):
    path = write(tmp_path, data())
    assert main(["--config", str(path), "validate"]) == EXIT_OK
    assert "válido" in capsys.readouterr().out


def test_cli_validate_reports_errors(tmp_path, capsys):
    d = data()
    d["equipos"][1]["id"] = "equipo-01"
    d["evento"]["fin"] = "mañana"
    path = write(tmp_path, d)
    assert main(["--config", str(path), "validate"]) == EXIT_ERROR
    err = capsys.readouterr().err
    assert "2 error(es)" in err
    assert "equipos[1].id" in err and "evento.fin" in err


def test_cli_validate_missing_file(tmp_path, capsys):
    assert main(["--config", str(tmp_path / "x.yml"), "validate"]) == EXIT_ERROR
    assert "no existe" in capsys.readouterr().err
