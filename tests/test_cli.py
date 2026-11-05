import subprocess
import sys
from pathlib import Path

import pytest

from acr import __version__
from acr.cli import DEFAULT_CONFIG, EXIT_OK, build_parser, main

COMMANDS = ["validate", "fetch", "git-signals", "scc", "megalinter", "scanoss", "dolos", "analyze"]
REPO_ROOT = Path(__file__).resolve().parent.parent


def test_help_lists_commands(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    for command in COMMANDS:
        assert command in out


def test_no_command_prints_help(capsys):
    assert main([]) == EXIT_OK
    out = capsys.readouterr().out
    for command in COMMANDS:
        assert command in out


def test_version(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_config_option_default_and_override(tmp_path):
    parser = build_parser()
    assert parser.parse_args(["validate"]).config == DEFAULT_CONFIG
    custom = tmp_path / "otro.yml"
    assert parser.parse_args(["--config", str(custom), "validate"]).config == custom


@pytest.mark.parametrize("command", ["fetch", "git-signals", "scc", "megalinter", "scanoss", "analyze"])
def test_optional_team_id(command):
    parser = build_parser()
    assert parser.parse_args([command]).team_id is None
    assert parser.parse_args([command, "equipo-01"]).team_id == "equipo-01"


def test_unknown_command_fails():
    with pytest.raises(SystemExit) as exc:
        main(["nope"])
    assert exc.value.code == 2


def test_python_m_acr_help():
    # Runs our own package (not team code) to check the ``python -m acr`` entry point.
    result = subprocess.run(
        [sys.executable, "-m", "acr", "--help"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    for command in COMMANDS:
        assert command in result.stdout
