"""Orchestrator API: event data and team CRUD over ``equipos.yml`` (PB-24, SPEC.md "Página
Configuración", "API del orquestador" and "Seguridad del orquestador").

Every endpoint reads and writes the same ``equipos.yml`` that is edited by hand. Each change is
validated with the rules of ``acr.config`` before it is written:

- ``PUT /api/evento`` validates the event; ``POST``/``PUT /api/equipos`` validate the team and
  that its ``id`` is unique. Only the edited part is validated, so the file can be filled step by
  step from an empty one (``acr validate`` still checks the whole file).
- A file that is not valid YAML (or not a map) is never overwritten: the API answers 409 so the
  person editing it by hand fixes it first.
- Writes are atomic (temporary file + rename) and serialized with a lock. Comments in the file are
  not kept when the API saves it.

Only ``localhost``: ``acr serve`` binds to 127.0.0.1, and the app also rejects requests that do
not come from a loopback address or whose ``Host``/``Origin`` is not ``localhost`` (protection
against DNS rebinding and against other web pages calling the API from the reviewer's browser).
The API never fetches nor runs the teams' code.
"""

from __future__ import annotations

import ipaddress
import os
import tempfile
import threading
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlsplit

import yaml
from fastapi import Body, FastAPI, Request
from fastapi.responses import JSONResponse

from acr.config import (
    CHECKLIST_FIELDS,
    EVENT_DATES,
    EVENT_REQUIRED,
    TEAM_OPTIONAL,
    TEAM_REQUIRED,
    ValidationError,
    _validate_event,
    _validate_team,
    is_valid_team_id,
)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
# Any JSON value: acr.config's validation gives the error messages, not pydantic.
JsonBody = Annotated[Any, Body()]

EVENT_FIELDS = (*EVENT_REQUIRED, *EVENT_DATES)
TEAM_FIELDS = (*TEAM_REQUIRED, *TEAM_OPTIONAL, *CHECKLIST_FIELDS)


class ApiError(Exception):
    """An error answered as JSON: ``{"detail": [{"location": ..., "message": ...}]}``."""

    def __init__(self, status: int, errors: list[ValidationError]) -> None:
        super().__init__(status, errors)
        self.status = status
        self.errors = errors


def _error(status: int, location: str, message: str) -> ApiError:
    return ApiError(status, [ValidationError(location, message)])


def is_loopback(host: str | None) -> bool:
    """True for ``localhost`` and loopback IP addresses (IPv4 127.0.0.0/8 and IPv6 ::1)."""
    if not host:
        return False
    host = host.strip("[]").lower()
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _hostname(value: str) -> str | None:
    """Hostname of a ``Host`` header (``name[:port]``) or of an ``Origin`` URL."""
    try:
        return urlsplit(value if "://" in value else f"//{value}").hostname
    except ValueError:
        return None


def _is_local_request(request: Request) -> bool:
    client = request.client.host if request.client else None
    if not is_loopback(client):
        return False
    host = request.headers.get("host")
    if host is None or not is_loopback(_hostname(host)):
        return False
    origin = request.headers.get("origin")
    if origin is not None and not is_loopback(_hostname(origin)):
        return False
    return True


def _without_none(raw: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in raw.items() if value is not None}


def _check_fields(where: str, raw: Any, allowed: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise _error(422, where, "el cuerpo debe ser un objeto JSON")
    unknown = sorted(set(raw) - set(allowed))
    if unknown:
        raise _error(
            422, where, f"campo(s) desconocido(s): {', '.join(unknown)}; válidos: {', '.join(allowed)}"
        )
    return _without_none(raw)


def _to_json(value: Any) -> Any:
    """YAML timestamps become ISO 8601 strings; everything else is already JSON-compatible."""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _to_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_to_json(item) for item in value]
    return value


class TeamsFile:
    """Read-modify-write access to ``equipos.yml``, serialized with a lock."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.lock = threading.Lock()

    def read(self) -> dict[str, Any]:
        """The parsed file; an empty map if it does not exist. 409 if it cannot be used."""
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        except (OSError, UnicodeDecodeError) as exc:
            raise _error(409, str(self.path), f"no se pudo leer: {exc}") from exc
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise _error(409, str(self.path), f"YAML inválido; corrígelo a mano: {exc}") from exc
        if data is None:
            return {}
        if not isinstance(data, dict):
            raise _error(409, str(self.path), "debe contener las claves evento y equipos")
        teams = data.get("equipos")
        if teams is not None and not isinstance(teams, list):
            raise _error(409, f"{self.path}: equipos", "debe ser una lista; corrígelo a mano")
        return data

    def write(self, data: dict[str, Any]) -> None:
        text = yaml.safe_dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".equipos-", suffix=".yml", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def update(self, change: Callable[[dict[str, Any]], Any]) -> Any:
        """Apply ``change`` to the parsed file and save it; returns what ``change`` returns."""
        with self.lock:
            data = self.read()
            result = change(data)
            self.write(data)
            return result


def _team_index(teams: list[Any], team_id: str) -> int | None:
    for index, raw in enumerate(teams):
        if isinstance(raw, dict) and raw.get("id") == team_id:
            return index
    return None


def _check_path_id(team_id: str) -> None:
    if not is_valid_team_id(team_id):
        raise _error(
            422, "id", f"id inválido ({team_id!r}): solo minúsculas, números y guiones, "
            "y debe empezar con letra o número",
        )


def create_app(config_path: Path) -> FastAPI:
    """The orchestrator app over ``config_path`` (``equipos.yml``)."""
    store = TeamsFile(config_path)
    base_dir = store.path.parent
    app = FastAPI(
        title="Advanced Code Review: orquestador",
        description="API local para editar equipos.yml (solo localhost).",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.middleware("http")
    async def only_localhost(request: Request, call_next):
        if not _is_local_request(request):
            return JSONResponse(
                status_code=403,
                content={"detail": [{"location": "request", "message": "solo se permite localhost"}]},
            )
        return await call_next(request)

    @app.exception_handler(ApiError)
    async def api_error(_request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status,
            content={"detail": [{"location": e.location, "message": e.message} for e in exc.errors]},
        )

    def validate_team(raw: dict[str, Any], others: list[Any]) -> None:
        errors: list[ValidationError] = []
        _validate_team(0, raw, errors, base_dir)
        if _team_index(others, raw.get("id")) is not None:
            errors.append(ValidationError("equipo.id", f"id repetido ({raw.get('id')!r})"))
        if errors:
            raise ApiError(
                422,
                [ValidationError(e.location.replace("equipos[0]", "equipo", 1), e.message) for e in errors],
            )

    @app.get("/api/evento")
    def get_event() -> Any:
        with store.lock:
            data = store.read()
        event = data.get("evento")
        if not isinstance(event, dict):
            raise _error(404, "evento", "todavía no hay datos del evento")
        return _to_json(event)

    @app.put("/api/evento")
    def put_event(payload: JsonBody) -> Any:
        event = _check_fields("evento", payload, EVENT_FIELDS)
        errors: list[ValidationError] = []
        parsed = _validate_event(event, errors)
        if parsed is None:
            raise ApiError(422, errors)
        saved = {"nombre": parsed.nombre, "inicio": parsed.inicio, "fin": parsed.fin}

        def change(data: dict[str, Any]) -> None:
            data["evento"] = saved
            if data.get("equipos") is None:
                data["equipos"] = []

        store.update(change)
        return _to_json(saved)

    @app.get("/api/equipos")
    def list_teams() -> Any:
        with store.lock:
            data = store.read()
        return _to_json(data.get("equipos") or [])

    @app.post("/api/equipos", status_code=201)
    def create_team(payload: JsonBody) -> Any:
        team = _check_fields("equipo", payload, TEAM_FIELDS)

        def change(data: dict[str, Any]) -> dict[str, Any]:
            if data.get("equipos") is None:
                data["equipos"] = []
            teams = data["equipos"]
            if is_valid_team_id(team.get("id")) and _team_index(teams, team["id"]) is not None:
                raise _error(409, "equipo.id", f"ya existe un equipo con id {team['id']!r}")
            validate_team(team, teams)
            teams.append(team)
            return team

        return _to_json(store.update(change))

    @app.put("/api/equipos/{team_id}")
    def update_team(team_id: str, payload: JsonBody) -> Any:
        _check_path_id(team_id)
        team = _check_fields("equipo", payload, TEAM_FIELDS)
        if team.setdefault("id", team_id) != team_id:
            raise _error(422, "equipo.id", "el id no se puede cambiar; da de baja el equipo y crea otro")

        def change(data: dict[str, Any]) -> dict[str, Any]:
            teams = data.get("equipos") or []
            index = _team_index(teams, team_id)
            if index is None:
                raise _error(404, "id", f"no existe el equipo {team_id!r}")
            validate_team(team, teams[:index] + teams[index + 1 :])
            teams[index] = team
            return team

        return _to_json(store.update(change))

    @app.delete("/api/equipos/{team_id}", status_code=204)
    def delete_team(team_id: str) -> None:
        _check_path_id(team_id)

        def change(data: dict[str, Any]) -> None:
            teams = data.get("equipos") or []
            index = _team_index(teams, team_id)
            if index is None:
                raise _error(404, "id", f"no existe el equipo {team_id!r}")
            del teams[index]

        store.update(change)

    return app


def serve(config_path: Path, port: int = DEFAULT_PORT) -> None:
    """Run the orchestrator on 127.0.0.1 only; there is no option to listen elsewhere."""
    import uvicorn

    uvicorn.run(create_app(config_path), host=DEFAULT_HOST, port=port, log_level="info")
