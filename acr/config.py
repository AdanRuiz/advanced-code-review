"""Load and validate ``equipos.yml`` (PB-5, PB-56, SPEC.md "Entrada").

The file describes the event and the teams. The checklist fields (``integrantes``,
``presentacion`` and ``prompts_ia``) are optional: when present they are validated, and
``missing_checklist_fields`` lists the teams that lack them so ``acr validate`` can warn.

``load_config`` never raises on bad content: it returns every problem it finds as a list of
``ValidationError`` so that ``acr validate`` can report them all at once.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml

# Lowercase letters, digits and hyphens. It must start with a letter or digit so it can never
# look like a command-line option or a relative path when used as a folder name.
TEAM_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")

EVENT_REQUIRED = ("nombre",)
EVENT_DATES = ("inicio", "fin")
TEAM_REQUIRED = ("id", "nombre", "proyecto", "objetivo", "repo")
TEAM_OPTIONAL = ("rama", "commit")
# Checklist field -> point it feeds (SPEC.md "Checklist oficial de code review").
CHECKLIST_FIELDS = {"integrantes": "1.2", "presentacion": "4.1", "prompts_ia": "3.4"}

# GitHub usernames: letters, digits and single hyphens, not at the start or the end, up to 39.
GITHUB_USER_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}$")
HTTP_URL_RE = re.compile(r"^https?://[^\s/]+\S*$", re.IGNORECASE)
PDF_MAGIC = b"%PDF-"


@dataclass(frozen=True)
class ValidationError:
    location: str
    message: str

    def __str__(self) -> str:
        return f"{self.location}: {self.message}"


@dataclass(frozen=True)
class Event:
    nombre: str
    inicio: datetime
    fin: datetime


@dataclass(frozen=True)
class Member:
    nombre: str
    github: str


@dataclass(frozen=True)
class Team:
    id: str
    nombre: str
    proyecto: str
    objetivo: str
    repo: str
    rama: str | None = None
    commit: str | None = None
    integrantes: tuple[Member, ...] | None = None
    # http(s) URL, or absolute path to a local PDF (relative paths resolve from equipos.yml's folder).
    presentacion: str | None = None
    # Absolute path to a local PDF.
    prompts_ia: str | None = None


@dataclass(frozen=True)
class Config:
    evento: Event
    equipos: tuple[Team, ...]


def is_valid_team_id(value: object) -> bool:
    return isinstance(value, str) and TEAM_ID_RE.fullmatch(value) is not None


def _parse_datetime(value: Any) -> datetime | None:
    """Accept a YAML timestamp or an ISO 8601 date-time string; a bare date is not enough."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return None
    if isinstance(value, str) and "T" in value.upper():
        try:
            return datetime.fromisoformat(value.strip())
        except ValueError:
            return None
    return None


def _is_text(value: Any) -> bool:
    return isinstance(value, str) and value.strip() != ""


def _validate_event(raw: Any, errors: list[ValidationError]) -> Event | None:
    if raw is None:
        errors.append(ValidationError("evento", "falta el campo obligatorio"))
        return None
    if not isinstance(raw, dict):
        errors.append(ValidationError("evento", "debe ser un mapa con nombre, inicio y fin"))
        return None

    ok = True
    for field in EVENT_REQUIRED:
        if field not in raw or raw[field] is None:
            errors.append(ValidationError(f"evento.{field}", "falta el campo obligatorio"))
            ok = False
        elif not _is_text(raw[field]):
            errors.append(ValidationError(f"evento.{field}", "debe ser un texto no vacío"))
            ok = False

    dates: dict[str, datetime] = {}
    for field in EVENT_DATES:
        if field not in raw or raw[field] is None:
            errors.append(ValidationError(f"evento.{field}", "falta el campo obligatorio"))
            ok = False
            continue
        parsed = _parse_datetime(raw[field])
        if parsed is None:
            errors.append(
                ValidationError(
                    f"evento.{field}",
                    f"fecha mal formada ({raw[field]!r}); usa ISO 8601 con hora, "
                    "por ejemplo 2026-11-01T09:00:00-06:00",
                )
            )
            ok = False
        else:
            dates[field] = parsed

    if len(dates) == 2:
        inicio, fin = dates["inicio"], dates["fin"]
        if (inicio.tzinfo is None) != (fin.tzinfo is None):
            errors.append(
                ValidationError("evento", "inicio y fin deben tener ambas zona horaria o ninguna")
            )
            ok = False
        elif fin <= inicio:
            errors.append(ValidationError("evento.fin", "debe ser posterior a evento.inicio"))
            ok = False

    if not ok:
        return None
    return Event(nombre=raw["nombre"].strip(), inicio=dates["inicio"], fin=dates["fin"])


def _validate_members(where: str, raw: Any, errors: list[ValidationError]) -> tuple[Member, ...] | None:
    """``integrantes``: a non-empty list of ``{nombre, github}`` with distinct GitHub users."""
    if not isinstance(raw, list) or not raw:
        errors.append(
            ValidationError(where, "debe ser una lista con al menos un integrante (nombre y github)")
        )
        return None

    ok = True
    members: list[Member] = []
    first_seen: dict[str, int] = {}
    for index, item in enumerate(raw):
        item_where = f"{where}[{index}]"
        if not isinstance(item, dict):
            errors.append(ValidationError(item_where, "debe ser un mapa con nombre y github"))
            ok = False
            continue

        item_ok = True
        nombre = item.get("nombre")
        if nombre is None:
            errors.append(ValidationError(f"{item_where}.nombre", "falta el campo obligatorio"))
            item_ok = False
        elif not _is_text(nombre):
            errors.append(ValidationError(f"{item_where}.nombre", "debe ser un texto no vacío"))
            item_ok = False

        github = item.get("github")
        if github is None:
            errors.append(ValidationError(f"{item_where}.github", "falta el campo obligatorio"))
            item_ok = False
        elif not isinstance(github, str) or GITHUB_USER_RE.fullmatch(github.strip()) is None:
            errors.append(
                ValidationError(
                    f"{item_where}.github",
                    f"usuario de GitHub inválido ({github!r}): solo letras, números y guiones "
                    "sueltos, sin @ ni URL, hasta 39 caracteres",
                )
            )
            item_ok = False
        else:
            key = github.strip().lower()  # GitHub usernames are case-insensitive
            if key in first_seen:
                errors.append(
                    ValidationError(
                        f"{item_where}.github",
                        f"usuario repetido ({github.strip()!r}); ya está en {where}[{first_seen[key]}]",
                    )
                )
                item_ok = False
            else:
                first_seen[key] = index

        if item_ok:
            members.append(Member(nombre=nombre.strip(), github=github.strip()))
        else:
            ok = False
    return tuple(members) if ok else None


def _local_pdf(where: str, value: str, base_dir: Path, errors: list[ValidationError]) -> str | None:
    """Resolve a local PDF path and check that it exists and starts with the PDF signature."""
    path = Path(value)
    if not path.is_absolute():
        path = base_dir / path
    if path.suffix.lower() != ".pdf":
        errors.append(ValidationError(where, f"debe ser un archivo .pdf ({value!r})"))
        return None
    if not path.is_file():
        errors.append(ValidationError(where, f"el archivo no existe ({value!r})"))
        return None
    try:
        with path.open("rb") as handle:
            header = handle.read(len(PDF_MAGIC))
    except OSError as exc:
        errors.append(ValidationError(where, f"no se pudo leer ({value!r}): {exc}"))
        return None
    if header != PDF_MAGIC:
        errors.append(ValidationError(where, f"el archivo no es un PDF válido ({value!r})"))
        return None
    return str(path.resolve())


def _validate_document(
    where: str, field: str, value: Any, base_dir: Path, errors: list[ValidationError]
) -> str | None:
    """``presentacion``: http(s) URL or local PDF. ``prompts_ia``: local PDF only."""
    if not _is_text(value):
        errors.append(ValidationError(where, "debe ser un texto no vacío"))
        return None
    value = value.strip()
    if "://" in value:
        if field == "presentacion" and HTTP_URL_RE.fullmatch(value):
            return value
        if field == "presentacion":
            errors.append(ValidationError(where, f"URL inválida ({value!r}): usa http:// o https://"))
        else:
            errors.append(ValidationError(where, "debe ser la ruta a un PDF local, no una URL"))
        return None
    return _local_pdf(where, value, base_dir, errors)


def _validate_checklist(
    where: str, raw: dict, base_dir: Path, errors: list[ValidationError]
) -> dict[str, Any] | None:
    """PB-56: validate the optional checklist fields that are present."""
    ok = True
    values: dict[str, Any] = {}
    if raw.get("integrantes") is not None:
        values["integrantes"] = _validate_members(f"{where}.integrantes", raw["integrantes"], errors)
        ok = values["integrantes"] is not None
    for field in ("presentacion", "prompts_ia"):
        if raw.get(field) is not None:
            values[field] = _validate_document(f"{where}.{field}", field, raw[field], base_dir, errors)
            ok = ok and values[field] is not None
    return values if ok else None


def _validate_team(
    index: int, raw: Any, errors: list[ValidationError], base_dir: Path = Path(".")
) -> Team | None:
    where = f"equipos[{index}]"
    if not isinstance(raw, dict):
        errors.append(ValidationError(where, "debe ser un mapa con los campos del equipo"))
        return None

    ok = True
    for field in TEAM_REQUIRED:
        if field not in raw or raw[field] is None:
            errors.append(ValidationError(f"{where}.{field}", "falta el campo obligatorio"))
            ok = False
        elif field == "id":
            if not is_valid_team_id(raw[field]):
                errors.append(
                    ValidationError(
                        f"{where}.id",
                        f"id inválido ({raw[field]!r}): solo minúsculas, números y guiones, "
                        "y debe empezar con letra o número",
                    )
                )
                ok = False
        elif not _is_text(raw[field]):
            errors.append(ValidationError(f"{where}.{field}", "debe ser un texto no vacío"))
            ok = False

    for field in TEAM_OPTIONAL:
        value = raw.get(field)
        if value is not None and not _is_text(value):
            errors.append(
                ValidationError(
                    f"{where}.{field}",
                    "debe ser un texto no vacío (escríbelo entre comillas si solo tiene números)",
                )
            )
            ok = False

    checklist = _validate_checklist(where, raw, base_dir, errors)
    if checklist is None:
        ok = False

    if not ok:
        return None
    return Team(
        id=raw["id"],
        nombre=raw["nombre"].strip(),
        proyecto=raw["proyecto"].strip(),
        objetivo=raw["objetivo"].strip(),
        repo=raw["repo"].strip(),
        rama=raw["rama"].strip() if raw.get("rama") is not None else None,
        commit=raw["commit"].strip() if raw.get("commit") is not None else None,
        **checklist,
    )


def validate_data(
    data: Any, base_dir: Path | None = None
) -> tuple[Config | None, list[ValidationError]]:
    """Validate already-parsed YAML data. Returns the config only when there are no errors.

    Relative paths in ``presentacion`` and ``prompts_ia`` resolve from ``base_dir`` (the folder
    of ``equipos.yml``; the current directory when omitted).
    """
    base = Path(".") if base_dir is None else Path(base_dir)
    errors: list[ValidationError] = []
    if not isinstance(data, dict):
        errors.append(ValidationError("equipos.yml", "debe contener las claves evento y equipos"))
        return None, errors

    event = _validate_event(data.get("evento"), errors)

    raw_teams = data.get("equipos")
    teams: list[Team] = []
    if raw_teams is None:
        errors.append(ValidationError("equipos", "falta el campo obligatorio"))
    elif not isinstance(raw_teams, list) or not raw_teams:
        errors.append(ValidationError("equipos", "debe ser una lista con al menos un equipo"))
    else:
        first_seen: dict[str, int] = {}
        for index, raw in enumerate(raw_teams):
            team = _validate_team(index, raw, errors, base)
            if team is not None:
                teams.append(team)
            team_id = raw.get("id") if isinstance(raw, dict) else None
            if is_valid_team_id(team_id):
                if team_id in first_seen:
                    errors.append(
                        ValidationError(
                            f"equipos[{index}].id",
                            f"id repetido ({team_id!r}); ya se usa en equipos[{first_seen[team_id]}]",
                        )
                    )
                else:
                    first_seen[team_id] = index

    if errors or event is None:
        return None, errors
    return Config(evento=event, equipos=tuple(teams)), errors


def load_config(path: Path) -> tuple[Config | None, list[ValidationError]]:
    """Read and validate ``equipos.yml``. Never raises for missing files or invalid content."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, [ValidationError(str(path), "no existe; copia equipos.example.yml y llénalo")]
    except (OSError, UnicodeDecodeError) as exc:
        return None, [ValidationError(str(path), f"no se pudo leer: {exc}")]

    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        return None, [ValidationError(str(path), f"YAML inválido: {exc}")]

    return validate_data(data, Path(path).parent)


def missing_checklist_fields(config: Config) -> list[ValidationError]:
    """PB-56: one warning per team and missing ``integrantes``, ``presentacion`` or ``prompts_ia``.

    The fields are optional, but without them their checklist point needs manual review.
    """
    warnings: list[ValidationError] = []
    for team in config.equipos:
        for field, point in CHECKLIST_FIELDS.items():
            if getattr(team, field) is None:
                warnings.append(
                    ValidationError(
                        f"{team.id}.{field}",
                        f"falta; el punto {point} del checklist quedará en revisión manual",
                    )
                )
    return warnings
