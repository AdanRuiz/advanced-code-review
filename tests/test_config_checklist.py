"""Tests for the checklist fields of equipos.yml (PB-56, SPEC.md "Entrada"), one per rule."""

import copy
from pathlib import Path

import pytest
import yaml

from acr.cli import EXIT_ERROR, EXIT_OK, main
from acr.config import Member, load_config, missing_checklist_fields, validate_data

PDF_BYTES = b"%PDF-1.7\n%fake\n"

BASE = {
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
        },
    ],
}


def data(**team_fields):
    d = copy.deepcopy(BASE)
    d["equipos"][0].update(team_fields)
    return d


def pdf(tmp_path: Path, name: str = "prompts.pdf", content: bytes = PDF_BYTES) -> Path:
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def locations(errors):
    return [e.location for e in errors]


MEMBERS = [{"nombre": "Ana López", "github": "analopez"}, {"nombre": "Luis", "github": "luis-dev"}]


# --- valid input -------------------------------------------------------------------------------


def test_all_checklist_fields_are_loaded(tmp_path):
    prompts = pdf(tmp_path)
    slides = pdf(tmp_path, "slides.pdf")
    d = data(integrantes=MEMBERS, presentacion=str(slides), prompts_ia=str(prompts))
    config, errors = validate_data(d)
    assert errors == []
    team = config.equipos[0]
    assert team.integrantes == (Member("Ana López", "analopez"), Member("Luis", "luis-dev"))
    assert team.presentacion == str(slides.resolve())
    assert team.prompts_ia == str(prompts.resolve())


def test_fields_are_optional():
    config, errors = validate_data(data())
    assert errors == []
    team = config.equipos[0]
    assert team.integrantes is None and team.presentacion is None and team.prompts_ia is None


@pytest.mark.parametrize(
    "url", ["https://docs.google.com/presentation/d/abc", "http://example.org/pitch"]
)
def test_presentacion_accepts_http_url(url):
    config, errors = validate_data(data(presentacion=url))
    assert errors == []
    assert config.equipos[0].presentacion == url


def test_relative_paths_resolve_from_config_folder(tmp_path):
    pdf(tmp_path / "entregas", "alfa.pdf")
    path = tmp_path / "equipos.yml"
    d = data(presentacion="entregas/alfa.pdf", prompts_ia="entregas/alfa.pdf")
    path.write_text(yaml.safe_dump(d), encoding="utf-8")
    config, errors = load_config(path)
    assert errors == []
    expected = str((tmp_path / "entregas" / "alfa.pdf").resolve())
    assert config.equipos[0].presentacion == expected
    assert config.equipos[0].prompts_ia == expected


def test_pdf_extension_is_case_insensitive(tmp_path):
    config, errors = validate_data(data(prompts_ia=str(pdf(tmp_path, "PROMPTS.PDF"))))
    assert errors == []


# --- integrantes -------------------------------------------------------------------------------


@pytest.mark.parametrize("value", [[], "analopez", {"nombre": "Ana", "github": "ana"}])
def test_integrantes_must_be_non_empty_list(value):
    config, errors = validate_data(data(integrantes=value))
    assert config is None
    assert locations(errors) == ["equipos[0].integrantes"]


def test_integrante_must_be_mapping():
    _, errors = validate_data(data(integrantes=["analopez"]))
    assert locations(errors) == ["equipos[0].integrantes[0]"]


@pytest.mark.parametrize("field", ["nombre", "github"])
def test_integrante_missing_field(field):
    member = dict(MEMBERS[0])
    del member[field]
    _, errors = validate_data(data(integrantes=[member]))
    assert locations(errors) == [f"equipos[0].integrantes[0].{field}"]
    assert "falta" in errors[0].message


def test_integrante_empty_nombre():
    _, errors = validate_data(data(integrantes=[{"nombre": "  ", "github": "ana"}]))
    assert locations(errors) == ["equipos[0].integrantes[0].nombre"]


@pytest.mark.parametrize(
    "github",
    [
        "@analopez",
        "https://github.com/analopez",
        "-ana",
        "ana-",
        "ana--lopez",
        "ana lopez",
        "a" * 40,
        123,
        "",
    ],
)
def test_integrante_invalid_github(github):
    _, errors = validate_data(data(integrantes=[{"nombre": "Ana", "github": github}]))
    assert locations(errors) == ["equipos[0].integrantes[0].github"]


@pytest.mark.parametrize("github", ["a", "Ana-Lopez", "a1-b2-c3", "a" * 39])
def test_integrante_valid_github(github):
    _, errors = validate_data(data(integrantes=[{"nombre": "Ana", "github": github}]))
    assert errors == []


def test_integrante_repeated_github_is_case_insensitive():
    members = [{"nombre": "Ana", "github": "analopez"}, {"nombre": "Otra", "github": "AnaLopez"}]
    _, errors = validate_data(data(integrantes=members))
    assert locations(errors) == ["equipos[0].integrantes[1].github"]
    assert "repetido" in errors[0].message


def test_every_integrante_error_is_reported():
    members = [{"github": "@x"}, {"nombre": "B", "github": "b"}, 5]
    _, errors = validate_data(data(integrantes=members))
    assert locations(errors) == [
        "equipos[0].integrantes[0].nombre",
        "equipos[0].integrantes[0].github",
        "equipos[0].integrantes[2]",
    ]


# --- presentacion and prompts_ia ---------------------------------------------------------------


@pytest.mark.parametrize("field", ["presentacion", "prompts_ia"])
def test_local_pdf_must_exist(tmp_path, field):
    _, errors = validate_data(data(**{field: str(tmp_path / "no-existe.pdf")}))
    assert locations(errors) == [f"equipos[0].{field}"]
    assert "no existe" in errors[0].message


@pytest.mark.parametrize("field", ["presentacion", "prompts_ia"])
def test_local_file_must_have_pdf_extension(tmp_path, field):
    other = tmp_path / "slides.pptx"
    other.write_bytes(PDF_BYTES)
    _, errors = validate_data(data(**{field: str(other)}))
    assert locations(errors) == [f"equipos[0].{field}"]
    assert ".pdf" in errors[0].message


@pytest.mark.parametrize("field", ["presentacion", "prompts_ia"])
def test_local_file_must_be_a_real_pdf(tmp_path, field):
    fake = pdf(tmp_path, "fake.pdf", b"<html>no soy un pdf</html>")
    _, errors = validate_data(data(**{field: str(fake)}))
    assert locations(errors) == [f"equipos[0].{field}"]
    assert "no es un PDF" in errors[0].message


@pytest.mark.parametrize("field", ["presentacion", "prompts_ia"])
def test_directory_is_not_a_pdf(tmp_path, field):
    folder = tmp_path / "carpeta.pdf"
    folder.mkdir()
    _, errors = validate_data(data(**{field: str(folder)}))
    assert locations(errors) == [f"equipos[0].{field}"]


@pytest.mark.parametrize("field", ["presentacion", "prompts_ia"])
@pytest.mark.parametrize("value", ["", "   ", 42, ["a.pdf"]])
def test_document_must_be_non_empty_text(field, value):
    _, errors = validate_data(data(**{field: value}))
    assert locations(errors) == [f"equipos[0].{field}"]


@pytest.mark.parametrize("url", ["ftp://example.org/pitch.pdf", "file:///C:/x.pdf", "https://"])
def test_presentacion_rejects_non_http_url(url):
    _, errors = validate_data(data(presentacion=url))
    assert locations(errors) == ["equipos[0].presentacion"]


def test_prompts_ia_rejects_url():
    _, errors = validate_data(data(prompts_ia="https://example.org/prompts.pdf"))
    assert locations(errors) == ["equipos[0].prompts_ia"]
    assert "no una URL" in errors[0].message


def test_checklist_errors_are_reported_with_other_errors(tmp_path):
    d = data(prompts_ia=str(tmp_path / "x.pdf"), integrantes=[{"nombre": "A", "github": "@a"}])
    del d["equipos"][0]["repo"]
    config, errors = validate_data(d)
    assert config is None
    assert sorted(locations(errors)) == [
        "equipos[0].integrantes[0].github",
        "equipos[0].prompts_ia",
        "equipos[0].repo",
    ]


# --- warnings when missing ---------------------------------------------------------------------


def test_missing_checklist_fields_warns_per_field():
    config, _ = validate_data(data())
    warnings = missing_checklist_fields(config)
    assert locations(warnings) == [
        "equipo-01.integrantes",
        "equipo-01.presentacion",
        "equipo-01.prompts_ia",
    ]
    assert "1.2" in warnings[0].message and "4.1" in warnings[1].message and "3.4" in warnings[2].message


def test_no_warnings_when_all_fields_present(tmp_path):
    d = data(integrantes=MEMBERS, presentacion="https://x.org/p", prompts_ia=str(pdf(tmp_path)))
    config, _ = validate_data(d)
    assert missing_checklist_fields(config) == []


# --- CLI ---------------------------------------------------------------------------------------


def write(tmp_path, content):
    path = tmp_path / "equipos.yml"
    path.write_text(yaml.safe_dump(content), encoding="utf-8")
    return path


def test_cli_validate_warns_but_exits_ok(tmp_path, capsys):
    path = write(tmp_path, data(integrantes=MEMBERS))
    assert main(["--config", str(path), "validate"]) == EXIT_OK
    captured = capsys.readouterr()
    assert "válido" in captured.out
    assert "2 aviso(s)" in captured.err
    assert "equipo-01.presentacion" in captured.err and "equipo-01.prompts_ia" in captured.err
    assert "integrantes" not in captured.err


def test_cli_validate_no_warnings_when_complete(tmp_path, capsys):
    pdf(tmp_path, "p.pdf")
    path = write(tmp_path, data(integrantes=MEMBERS, presentacion="p.pdf", prompts_ia="p.pdf"))
    assert main(["--config", str(path), "validate"]) == EXIT_OK
    assert "aviso" not in capsys.readouterr().err


def test_cli_validate_fails_on_missing_pdf(tmp_path, capsys):
    path = write(tmp_path, data(prompts_ia="entregas/no-existe.pdf"))
    assert main(["--config", str(path), "validate"]) == EXIT_ERROR
    err = capsys.readouterr().err
    assert "1 error(es)" in err and "equipos[0].prompts_ia" in err
