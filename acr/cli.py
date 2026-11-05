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
    fetch.set_defaults(handler=_not_implemented("PB-6"))

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
