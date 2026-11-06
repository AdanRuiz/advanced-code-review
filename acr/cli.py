"""Command-line interface for Advanced Code Review (PB-4, SPEC.md "Arquitectura y flujo").

Each subcommand maps to a step of the pipeline; ``acr analyze`` runs all of them in order.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from acr import __version__
from acr.analyze import STATUS_FILE, default_dolos, default_steps, run_analysis
from acr.analyze import Options as AnalyzeOptions
from acr.config import load_config
from acr.dolos import DolosError, write_dolos
from acr.fetch import DEFAULT_MAX_ZIP_BYTES, DEFAULT_REPOS_DIR, FetchError, fetch_team
from acr.git_signals import DEFAULT_REPORTS_DIR, GitSignalsError, write_git_signals
from acr.megalinter import (
    DEFAULT_IMAGE,
    DEFAULT_TIMEOUT_SECONDS,
    ENGINES,
    MegaLinterError,
    run_megalinter,
)
from acr.plagiarism import PLAGIARISM_FILE, PlagiarismError, write_plagiarism
from acr.report import DEFAULT_DASHBOARD_URL, ReportError, write_report
from acr.review import WEB_PLAGIARISM_FILE, ReviewError, render_prompt, validate_team
from acr.scanoss import ScanossError, write_scanoss
from acr.scc import SccError, write_scc
from acr.security import SECURITY_FILE, SecurityError, write_security

DEFAULT_CONFIG = Path("equipos.yml")

# Exit codes.
EXIT_OK = 0
EXIT_ERROR = 1


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


def _scanoss(args: argparse.Namespace) -> int:
    """PB-10: send only fingerprints of repos/<id> to SCANOSS; write reports/<id>/scanoss.json."""
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
            print(f"acr scanoss: no hay un equipo con id {args.team_id!r}", file=sys.stderr)
            return EXIT_ERROR

    failed = 0
    for team in teams:
        try:
            path, report = write_scanoss(team.id, DEFAULT_REPOS_DIR, DEFAULT_REPORTS_DIR)
        except (ScanossError, OSError) as exc:
            failed += 1
            print(f"{team.id}: error: {exc}", file=sys.stderr)
            continue
        totals, excluded = report["totals"], report["excluded"]
        print(
            f"{team.id}: {totals['files_matched']} de {totals['files_scanned']} archivo(s) con "
            f"coincidencias ({totals['matched_percent']}%), {excluded['files']} archivo(s) de "
            f"dependencias excluidos -> {path.as_posix()}"
        )
    if failed:
        print(f"acr scanoss: {failed} de {len(teams)} equipo(s) con error", file=sys.stderr)
        return EXIT_ERROR
    return EXIT_OK


def _dolos(args: argparse.Namespace) -> int:
    """PB-11: run Dolos once over every fetched team and write reports/dolos/pairs.csv."""
    config, errors = load_config(args.config)
    if config is None or errors:
        print(f"{args.config}: {len(errors)} error(es); corre 'acr validate'", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return EXIT_ERROR

    fetched = []
    for team in config.equipos:
        if (DEFAULT_REPOS_DIR / team.id).is_dir():
            fetched.append(team.id)
        else:
            print(f"{team.id}: aviso: no se compara; corre 'acr fetch {team.id}'", file=sys.stderr)
    try:
        result = write_dolos(fetched, DEFAULT_REPOS_DIR, DEFAULT_REPORTS_DIR)
    except (DolosError, OSError) as exc:
        print(f"acr dolos: error: {exc}", file=sys.stderr)
        return EXIT_ERROR

    summary = result.summary
    compared = [lang["language"] for lang in summary["languages"] if lang["compared"]]
    totals, excluded = summary["totals"], summary["excluded"]
    print(
        f"Dolos: {len(summary['teams'])} equipo(s), lenguajes comparados: "
        f"{', '.join(compared) or 'ninguno'}; {totals['team_pairs_with_matches']} de "
        f"{totals['team_pairs']} par(es) de equipos con archivos similares -> "
        f"{result.pairs_path.as_posix()}"
    )
    for row in result.team_pairs[:5]:
        if row["file_pairs"]:
            print(
                f"  {row['team_a']} ~ {row['team_b']}: {row['similarity']:.0%} "
                f"({row['file_a']} ~ {row['file_b']})"
            )
    if excluded["unsupported_files"]:
        extensions = ", ".join(excluded["unsupported_extensions"])
        print(
            f"aviso: {excluded['unsupported_files']} archivo(s) de lenguajes sin soporte en Dolos "
            f"no se compararon ({extensions})",
            file=sys.stderr,
        )
    if len(fetched) < len(config.equipos):
        print(f"acr dolos: {len(config.equipos) - len(fetched)} equipo(s) sin obtener", file=sys.stderr)
        return EXIT_ERROR
    return EXIT_OK


def _analyze(args: argparse.Namespace) -> int:
    """PB-12: run every step in order for one or every team; write reports/status.json."""
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
            print(f"acr analyze: no hay un equipo con id {args.team_id!r}", file=sys.stderr)
            return EXIT_ERROR

    options = AnalyzeOptions(
        repos_dir=DEFAULT_REPOS_DIR,
        reports_dir=DEFAULT_REPORTS_DIR,
        max_zip_bytes=args.max_zip_mb * 1024 * 1024,
        engine=args.engine,
        image=args.image,
        megalinter_timeout=args.timeout_min * 60,
    )
    result = run_analysis(
        config,
        teams,
        steps=default_steps(config, options),
        dolos=default_dolos(options),
        repos_dir=options.repos_dir,
        reports_dir=options.reports_dir,
        warn=lambda message: print(message, file=sys.stderr),
    )
    status_path = (DEFAULT_REPORTS_DIR / STATUS_FILE).as_posix()
    print(
        f"acr analyze: {len(result.analyzed)} equipo(s) listo(s), {len(result.failed)} con error, "
        f"{len(result.busy)} ya en curso -> {status_path}"
    )
    if result.dolos == "error":
        print("acr analyze: Dolos falló; se conservan sus reportes anteriores", file=sys.stderr)
    # PB-21: the Seguridad page reads every team, also the ones not analyzed in this run.
    security_ok = _write_security([team.id for team in config.equipos], "acr analyze")
    # PB-22: the same for the Plagio page.
    plagiarism_ok = _write_plagiarism([team.id for team in config.equipos], "acr analyze")
    return EXIT_OK if result.ok and security_ok and plagiarism_ok else EXIT_ERROR


def _write_security(team_ids: list[str], command: str) -> bool:
    try:
        path, data = write_security(team_ids, DEFAULT_REPORTS_DIR)
    except (SecurityError, OSError) as exc:
        print(f"{command}: error al escribir {SECURITY_FILE}: {exc}", file=sys.stderr)
        return False
    teams = data["teams"]
    print(
        f"Seguridad: {sum(t['cve_fixable_critical_high'] for t in teams)} CVE crítica(s)/alta(s) con "
        f"corrección, {sum(t['cve_total'] for t in teams)} CVE en total, "
        f"{sum(t['semgrep_findings'] for t in teams)} hallazgo(s) de Semgrep y "
        f"{sum(t['secrets'] for t in teams)} secreto(s) en {len(teams)} equipo(s) -> {path.as_posix()}"
    )
    missing = [t["team"] for t in teams if not (t["trivy"] and t["semgrep"] and t["secrets_scanned"])]
    if missing:
        print(
            f"aviso: sin SARIF de Trivy, Semgrep o MegaLinter para {', '.join(missing)}; "
            "corre 'acr megalinter'",
            file=sys.stderr,
        )
    return True


def _security(args: argparse.Namespace) -> int:
    """PB-21: write reports/security.json (Seguridad page) from the SARIF of every team."""
    config, errors = load_config(args.config)
    if config is None or errors:
        print(f"{args.config}: {len(errors)} error(es); corre 'acr validate'", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return EXIT_ERROR
    ok = _write_security([team.id for team in config.equipos], "acr security")
    return EXIT_OK if ok else EXIT_ERROR


def _write_plagiarism(team_ids: list[str], command: str) -> bool:
    try:
        path, data = write_plagiarism(team_ids, DEFAULT_REPORTS_DIR)
    except (PlagiarismError, OSError) as exc:
        print(f"{command}: error al escribir {PLAGIARISM_FILE}: {exc}", file=sys.stderr)
        return False
    teams = data["teams"]
    print(
        f"Plagio: {sum(t['external_matches'] for t in teams)} coincidencia(s) con código externo, "
        f"{sum(p['similar'] for p in data['team_pairs'])} par(es) de equipos similares y "
        f"{sum(t['web_matches'] for t in teams)} proyecto(s) parecido(s) en la web en "
        f"{len(teams)} equipo(s) -> {path.as_posix()}"
    )
    warnings = [
        ([t["team"] for t in teams if not t["scanoss"]], "sin scanoss.json", "corre 'acr scanoss'"),
        (
            [t["team"] for t in teams if not t["git_signals"]],
            "sin git-signals.json",
            "corre 'acr git-signals'",
        ),
        (
            [
                t["team"]
                for t in teams
                if not t["web"] and (DEFAULT_REPORTS_DIR / t["team"] / WEB_PLAGIARISM_FILE).is_file()
            ],
            f"{WEB_PLAGIARISM_FILE} no válido",
            "corre 'acr review-validate'",
        ),
    ]
    for missing, what, hint in warnings:
        if missing:
            print(f"aviso: {what} para {', '.join(missing)}; {hint}", file=sys.stderr)
    if not data["similarity"] and len(teams) > 1:
        print("aviso: sin datos de Dolos (reports/dolos/pairs.csv); corre 'acr dolos'", file=sys.stderr)
    return True


def _plagiarism(args: argparse.Namespace) -> int:
    """PB-22: write reports/plagiarism.json (Plagio page) from the results of every team."""
    config, errors = load_config(args.config)
    if config is None or errors:
        print(f"{args.config}: {len(errors)} error(es); corre 'acr validate'", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return EXIT_ERROR
    ok = _write_plagiarism([team.id for team in config.equipos], "acr plagiarism")
    return EXIT_OK if ok else EXIT_ERROR


def _review_prompt(args: argparse.Namespace) -> int:
    """PB-13: print the Claude Code prompt for the assisted review of one team."""
    config, errors = load_config(args.config)
    if config is None or errors:
        print(f"{args.config}: {len(errors)} error(es); corre 'acr validate'", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return EXIT_ERROR

    team = next((team for team in config.equipos if team.id == args.team_id), None)
    if team is None:
        print(f"acr review-prompt: no hay un equipo con id {args.team_id!r}", file=sys.stderr)
        return EXIT_ERROR
    try:
        prompt = render_prompt(team, config.evento, DEFAULT_REPOS_DIR, DEFAULT_REPORTS_DIR)
    except (ReviewError, OSError) as exc:
        print(f"acr review-prompt: error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    if args.output is not None:
        try:
            args.output.write_text(prompt, encoding="utf-8")
        except OSError as exc:
            print(f"acr review-prompt: error: {exc}", file=sys.stderr)
            return EXIT_ERROR
        print(f"{team.id}: prompt -> {args.output.as_posix()}")
        return EXIT_OK
    print(prompt, end="" if prompt.endswith("\n") else "\n")
    return EXIT_OK


def _review_validate(args: argparse.Namespace) -> int:
    """PB-13: validate reports/<id>/review.json and web-plagiarism.json against their schemas."""
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
            print(f"acr review-validate: no hay un equipo con id {args.team_id!r}", file=sys.stderr)
            return EXIT_ERROR

    failed = 0
    for team in teams:
        try:
            checks = validate_team(team.id, DEFAULT_REPORTS_DIR)
        except (ReviewError, OSError) as exc:
            failed += 1
            print(f"{team.id}: error: {exc}", file=sys.stderr)
            continue
        if all(check.ok for check in checks):
            print(f"{team.id}: {', '.join(check.name for check in checks)} válidos")
            continue
        failed += 1
        for check in checks:
            path = check.path.as_posix()
            if not check.exists:
                print(f"{team.id}: falta {path}; corre la revisión asistida "
                      f"('acr review-prompt {team.id}')", file=sys.stderr)
            elif check.errors:
                print(f"{path}: {len(check.errors)} error(es)", file=sys.stderr)
                for error in check.errors:
                    print(f"  - {error}", file=sys.stderr)
            else:
                print(f"{path}: válido")
    if failed:
        print(f"acr review-validate: {failed} de {len(teams)} equipo(s) con error", file=sys.stderr)
        return EXIT_ERROR
    return EXIT_OK


def _report(args: argparse.Namespace) -> int:
    """PB-14: write reports/<id>/reporte.md from every result available in reports/."""
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
            print(f"acr report: no hay un equipo con id {args.team_id!r}", file=sys.stderr)
            return EXIT_ERROR

    names = {team.id: team.nombre for team in config.equipos}
    failed = 0
    for team in teams:
        try:
            path = write_report(
                team, config.evento, DEFAULT_REPORTS_DIR, names=names, dashboard_url=args.dashboard_url
            )
        except (ReportError, OSError) as exc:
            failed += 1
            print(f"{team.id}: error: {exc}", file=sys.stderr)
            continue
        print(f"{team.id}: reporte -> {path.as_posix()}")
    if failed:
        print(f"acr report: {failed} de {len(teams)} equipo(s) con error", file=sys.stderr)
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

    scanoss = subparsers.add_parser(
        "scanoss",
        help="busca código open source conocido con SCANOSS (solo envía huellas) y lo deja en "
        "reports/<id>/scanoss.json",
        description="Calcula en local las huellas de repos/<id>, quita las dependencias y envía "
        "solo los hashes a SCANOSS. Nunca envía ni ejecuta el código del equipo.",
    )
    scanoss.add_argument("team_id", nargs="?", metavar="ID", help="solo este equipo (por defecto: todos)")
    scanoss.set_defaults(handler=_scanoss)

    dolos = subparsers.add_parser(
        "dolos",
        help="compara el código de todos los equipos con Dolos y deja la similitud por par en "
        "reports/dolos/pairs.csv",
        description="Corre Dolos una sola vez sobre todos los repositorios obtenidos, por lenguaje. "
        "Solo lee los archivos; nunca ejecuta el código ni lo envía fuera de la máquina.",
    )
    dolos.set_defaults(handler=_dolos)

    analyze = subparsers.add_parser(
        "analyze",
        help="corre el análisis estático completo para uno o todos los equipos",
        description="Corre los pasos del análisis en orden y deja los resultados en reports/.",
    )
    analyze.add_argument("team_id", nargs="?", metavar="ID", help="solo este equipo (por defecto: todos)")
    analyze.add_argument(
        "--max-zip-mb",
        type=_positive_int,
        default=DEFAULT_MAX_ZIP_BYTES // (1024 * 1024),
        metavar="MB",
        help="tamaño máximo de un ZIP descomprimido (por defecto: %(default)s MB)",
    )
    analyze.add_argument(
        "--engine",
        choices=ENGINES,
        default=None,
        help="motor de contenedores para MegaLinter (por defecto: podman si está instalado, si no docker)",
    )
    analyze.add_argument(
        "--image",
        default=DEFAULT_IMAGE,
        help="imagen de MegaLinter, v10 o superior (por defecto: %(default)s)",
    )
    analyze.add_argument(
        "--timeout-min",
        type=_positive_int,
        default=DEFAULT_TIMEOUT_SECONDS // 60,
        metavar="MIN",
        help="tiempo máximo de MegaLinter por equipo (por defecto: %(default)s minutos)",
    )
    analyze.set_defaults(handler=_analyze)

    security = subparsers.add_parser(
        "security",
        help="resume CVE, Semgrep y secretos de todos los equipos en reports/security.json",
        description="Lee trivy.sarif, semgrep.sarif y megalinter.sarif de cada equipo y deja en "
        "reports/security.json los datos de la página Seguridad del dashboard. No vuelve a "
        "analizar y nunca escribe el valor de un secreto.",
    )
    security.set_defaults(handler=_security)

    plagiarism = subparsers.add_parser(
        "plagiarism",
        help="resume SCANOSS, Dolos, búsqueda web y señales de git en reports/plagiarism.json",
        description="Lee scanoss.json, web-plagiarism.json y git-signals.json de cada equipo y "
        "reports/dolos/pairs.csv, y deja en reports/plagiarism.json los datos de la página Plagio "
        "del dashboard. No vuelve a analizar.",
    )
    plagiarism.set_defaults(handler=_plagiarism)

    review_prompt = subparsers.add_parser(
        "review-prompt",
        help="imprime el prompt de Claude Code para la revisión asistida de un equipo",
        description="Imprime el prompt para que Claude Code revise repos/<id> a partir de sus "
        "reportes y escriba review.json y web-plagiarism.json. No ejecuta Claude Code ni el código.",
    )
    review_prompt.add_argument("team_id", metavar="ID", help="equipo a revisar")
    review_prompt.add_argument(
        "-o", "--output", type=Path, default=None, metavar="ARCHIVO",
        help="escribe el prompt en este archivo (UTF-8) en lugar de imprimirlo",
    )
    review_prompt.set_defaults(handler=_review_prompt)

    review_validate = subparsers.add_parser(
        "review-validate",
        help="valida review.json y web-plagiarism.json de la revisión asistida",
        description="Valida reports/<id>/review.json y reports/<id>/web-plagiarism.json contra sus "
        "esquemas JSON (acr/schemas/), la checklist de IA y OWASP LLM, y que no haya secretos en claro.",
    )
    review_validate.add_argument(
        "team_id", nargs="?", metavar="ID", help="solo este equipo (por defecto: todos)"
    )
    review_validate.set_defaults(handler=_review_validate)

    report = subparsers.add_parser(
        "report",
        help="genera el reporte Markdown de cada equipo en reports/<id>/reporte.md",
        description="Arma el reporte por equipo con la plantilla de la spec a partir de los "
        "resultados que ya están en reports/. No vuelve a analizar ni muestra secretos.",
    )
    report.add_argument("team_id", nargs="?", metavar="ID", help="solo este equipo (por defecto: todos)")
    report.add_argument(
        "--dashboard-url",
        default=DEFAULT_DASHBOARD_URL,
        metavar="URL",
        help="enlace al dashboard local de Grafana en el reporte (por defecto: %(default)s)",
    )
    report.set_defaults(handler=_report)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return EXIT_OK
    return args.handler(args)
