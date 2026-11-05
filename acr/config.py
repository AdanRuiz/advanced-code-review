"""Load and validate ``equipos.yml`` (PB-5, SPEC.md "Entrada").

The file describes the event and the teams. ``load_config`` never raises on bad content: it
returns every problem it finds as a list of ``ValidationError`` so that ``acr validate`` can
report them all at once.
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
class Team:
    id: str
    nombre: str
    proyecto: str
    objetivo: str
    repo: str
    rama: str | None = None
    commit: str | None = None


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


def _validate_team(index: int, raw: Any, errors: list[ValidationError]) -> Team | None:
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
    )


def validate_data(data: Any) -> tuple[Config | None, list[ValidationError]]:
    """Validate already-parsed YAML data. Returns the config only when there are no errors."""
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
            team = _validate_team(index, raw, errors)
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

    return validate_data(data)
