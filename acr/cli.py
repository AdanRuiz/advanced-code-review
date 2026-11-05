"""Command-line interface for Advanced Code Review (PB-4, SPEC.md "Arquitectura y flujo").

Each subcommand maps to a step of the pipeline. The commands are registered here so that
``python -m acr --help`` lists them; their behaviour is implemented by later backlog items.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from acr import __version__
from acr.config import load_config
from acr.fetch import DEFAULT_MAX_ZIP_BYTES, DEFAULT_REPOS_DIR, FetchError, fetch_team
from acr.git_signals import DEFAULT_REPORTS_DIR, GitSignalsError, write_git_signals
from acr.megalinter import (
    DEFAULT_IMAGE,
    DEFAULT_TIMEOUT_SECONDS,
    ENGINES,
    MegaLinterError,
    run_megalinter,
)
from acr.scc import SccError, write_scc

DEFAULT_CONFIG = Path("equipos.yml")

# Exit codes.
EXIT_OK = 0
EXIT_ERROR = 1
EXIT_NOT_IMPLEMENTED = 3

Handler = Callable[[argparse.Namespace], int]


def _not_implemented(item: str) -> Handler:
    def handler(args: argparse.Namespace) -> int:
        print(f"acr {args.command}: todavía no implementado ({item}).", file=sys.stderr)
        return EXIT_NOT_IMPLEMENTED

    return handler


def _validate(args: argparse.Namespace) -> int:
    """PB-5: report every problem in the input file; exit 1 if there is any."""
    config, errors = load_config(args.config)
    if config is None or errors:
        print(f"{args.config}: {len(errors)} error(es)", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return EXIT_ERROR
    print(f"{args.config}: válido ({config.evento.nombre}, {len(config.equipos)} equipo(s)).")
    return EXIT_OK


def _fetch(args: argparse.Namespace) -> int:
    """PB-6: fetch one or every team into repos/<id>; keep going if one team fails."""
    config, errors = load_config(args.config)
    if config is None or errors:
        print(f"{args.config}: {len(errors)} error(es); corre 'acr validate'", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return EXIT_ERROR

    teams = config.equipos
    if args.team_id is not None:
        teams = tuple(team for team in teams if team.id == args.team_id)
        if not teams:
            print(f"acr fetch: no hay un equipo con id {args.team_id!r}", file=sys.stderr)
            return EXIT_ERROR

    max_bytes = args.max_zip_mb * 1024 * 1024
    failed = 0
    for team in teams:
        try:
            result = fetch_team(team, DEFAULT_REPOS_DIR, max_zip_bytes=max_bytes)
        except FetchError as exc:
            failed += 1
            print(f"{team.id}: error: {exc}", file=sys.stderr)
        else:
            print(f"{team.id}: {result.kind} -> {result.path.as_posix()}")
    if failed:
        print(f"acr fetch: {failed} de {len(teams)} equipo(s) con error", file=sys.stderr)
        return EXIT_ERROR
    return EXIT_OK


def _git_signals(args: argparse.Namespace) -> int:
    """PB-7: write reports/<id>/git-signals.json for one or every fetched team."""
    config, errors = load_config(args.config)
    if config is None or errors:
        print(f"{args.config}: {len(errors)} error(es); corre 'acr validate'", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return EXIT_ERROR

    teams = config.equipos
    if args.team_id is not None:
        teams = tuple(team for team in teams if team.id == args.team_id)
        if not teams:
            print(f"acr git-signals: no hay un equipo con id {args.team_id!r}", file=sys.stderr)
            return EXIT_ERROR

    failed = 0
    for team in teams:
        try:
            path, signals = write_git_signals(team.id, config.evento, DEFAULT_REPOS_DIR, DEFAULT_REPORTS_DIR)
        except (GitSignalsError, OSError) as exc:
            failed += 1
            print(f"{team.id}: error: {exc}", file=sys.stderr)
            continue
        if not signals["has_git"]:
            summary = "sin historial de git"
        else:
            outside = signals["outside_event"]
            summary = (
                f"{signals['commit_count']} commit(s), {outside['before_start']} antes del inicio, "
                f"{outside['after_end']} después del cierre"
            )
        print(f"{team.id}: {summary} -> {path.as_posix()}")
    if failed:
        print(f"acr git-signals: {failed} de {len(teams)} equipo(s) con error", file=sys.stderr)
        return EXIT_ERROR
    return EXIT_OK


def _scc(args: argparse.Namespace) -> int:
    """PB-8: write reports/<id>/scc.json (languages, lines, files) for one or every team."""
    config, errors = load_config(args.config)
    if config is None or errors:
        print(f"{args.config}: {len(errors)} error(es); corre 'acr validate'", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return EXIT_ERROR

    teams = config.equipos
    if args.team_id is not None:
        teams = tuple(team for team in teams if team.id == args.team_id)
        if not teams:
            print(f"acr scc: no hay un equipo con id {args.team_id!r}", file=sys.stderr)
            return EXIT_ERROR

    failed = 0
    for team in teams:
        try:
            path, report = write_scc(team.id, DEFAULT_REPOS_DIR, DEFAULT_REPORTS_DIR)
        except (SccError, OSError) as exc:
            failed += 1
            print(f"{team.id}: error: {exc}", file=sys.stderr)
            continue
        totals = report["totals"]
        main_language = report["languages"][0]["name"] if report["languages"] else "ninguno"
        print(
            f"{team.id}: {totals['languages']} lenguaje(s) (principal: {main_language}), "
            f"{totals['files']} archivo(s), {totals['code']} línea(s) de código -> {path.as_posix()}"
        )
    if failed:
        print(f"acr scc: {failed} de {len(teams)} equipo(s) con error", file=sys.stderr)
        return EXIT_ERROR
    return EXIT_OK


def _megalinter(args: argparse.Namespace) -> int:
    """PB-9: run MegaLinter on repos/<id> (read-only) and write its reports to reports/<id>/."""
    config, errors = load_config(args.config)
    if config is None or errors:
        print(f"{args.config}: {len(errors)} error(es); corre 'acr validate'", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return EXIT_ERROR

    teams = config.equipos
    if args.team_id is not None:
        teams = tuple(team for team in teams if team.id == args.team_id)
        if not teams:
            print(f"acr megalinter: no hay un equipo con id {args.team_id!r}", file=sys.stderr)
            return EXIT_ERROR

    failed = 0
    for team in teams:
        try:
            result = run_megalinter(
                team.id,
                DEFAULT_REPOS_DIR,
                DEFAULT_REPORTS_DIR,
                engine=args.engine,
                image=args.image,
                timeout=args.timeout_min * 60,
            )
        except (MegaLinterError, OSError) as exc:
            failed += 1
            print(f"{team.id}: error: {exc}", file=sys.stderr)
            continue
        outcome = "con errores de linters" if result.returncode else "sin errores de linters"
        names = ", ".join(path.name for path in result.files)
        print(f"{team.id}: MegaLinter {outcome} -> {names} en {(DEFAULT_REPORTS_DIR / team.id).as_posix()}")
        if result.missing:
            print(f"{team.id}: aviso: no se generó {', '.join(result.missing)}", file=sys.stderr)
    if failed:
        print(f"acr megalinter: {failed} de {len(teams)} equipo(s) con error", file=sys.stderr)
        return EXIT_ERROR
    return EXIT_OK


def _positive_int(value: str) -> int:
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("debe ser un entero positivo")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="acr",
        description="Advanced Code Review: revisión estática de repositorios para hackathons.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "-c",
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
        help="archivo de entrada con el evento y los equipos (por defecto: %(default)s)",
    )

    subparsers = parser.add_subparsers(dest="command", metavar="<comando>", title="comandos")

    validate = subparsers.add_parser(
        "validate",
        help="valida equipos.yml: campos obligatorios, ids y fechas",
        description="Valida el archivo de entrada sin obtener ni analizar repositorios.",
    )
    validate.set_defaults(handler=_validate)

    fetch = subparsers.add_parser(
        "fetch",
        help="obtiene cada repositorio (URL git, carpeta o ZIP) en repos/<id>",
        description="Obtiene los repositorios de los equipos en solo lectura. Nunca los ejecuta.",
    )
    fetch.add_argument("team_id", nargs="?", metavar="ID", help="solo este equipo (por defecto: todos)")
    fetch.add_argument(
        "--max-zip-mb",
        type=_positive_int,
        default=DEFAULT_MAX_ZIP_BYTES // (1024 * 1024),
        metavar="MB",
        help="tamaño máximo de un ZIP descomprimido (por defecto: %(default)s MB)",
    )
    fetch.set_defaults(handler=_fetch)

    git_signals = subparsers.add_parser(
        "git-signals",
        help="resume el historial de git de cada equipo en reports/<id>/git-signals.json",
        description="Lee el historial de repos/<id> (sin ejecutar el código) y marca los commits "
        "fuera de evento.inicio/fin.",
    )
    git_signals.add_argument("team_id", nargs="?", metavar="ID", help="solo este equipo (por defecto: todos)")
    git_signals.set_defaults(handler=_git_signals)

    scc = subparsers.add_parser(
        "scc",
        help="cuenta lenguajes, líneas y archivos de cada equipo en reports/<id>/scc.json",
        description="Corre scc sobre repos/<id> (solo lee los archivos, no ejecuta el código).",
    )
    scc.add_argument("team_id", nargs="?", metavar="ID", help="solo este equipo (por defecto: todos)")
    scc.set_defaults(handler=_scc)

    megalinter = subparsers.add_parser(
        "megalinter",
        help="corre MegaLinter (calidad, Semgrep, Betterleaks, Trivy) y deja SARIF, JSON y Markdown "
        "en reports/<id>/",
        description="Corre el contenedor de MegaLinter con repos/<id> en solo lectura y la "
        "configuración .mega-linter.yml de este proyecto. Nunca ejecuta el código del equipo.",
    )
    megalinter.add_argument("team_id", nargs="?", metavar="ID", help="solo este equipo (por defecto: todos)")
    megalinter.add_argument(
        "--engine",
        choices=ENGINES,
        default=None,
        help="motor de contenedores (por defecto: podman si está instalado, si no docker)",
    )
    megalinter.add_argument(
        "--image",
        default=DEFAULT_IMAGE,
        help="imagen de MegaLinter, v10 o superior (por defecto: %(default)s)",
    )
    megalinter.add_argument(
        "--timeout-min",
        type=_positive_int,
        default=DEFAULT_TIMEOUT_SECONDS // 60,
        metavar="MIN",
        help="tiempo máximo por equipo (por defecto: %(default)s minutos)",
    )
    megalinter.set_defaults(handler=_megalinter)

    analyze = subparsers.add_parser(
        "analyze",
        help="corre el análisis estático completo para uno o todos los equipos",
        description="Corre los pasos del análisis en orden y deja los resultados en reports/.",
    )
    analyze.add_argument("team_id", nargs="?", metavar="ID", help="solo este equipo (por defecto: todos)")
    analyze.set_defaults(handler=_not_implemented("PB-12"))

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return EXIT_OK
    return args.handler(args)
