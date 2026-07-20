from typer.testing import CliRunner

from redis_version.cli import app

runner = CliRunner()


def test_parse_json() -> None:
    result = runner.invoke(app, ["parse", "8.2.1-rc1", "--output", "json"])

    assert result.exit_code == 0
    assert '"major": 8' in result.stdout
    assert '"suffix": "-rc1"' in result.stdout


def test_major_minor_patch_parts_commands() -> None:
    assert runner.invoke(app, ["major", "v8.2.1-rc1"]).stdout.strip() == "8"
    assert runner.invoke(app, ["minor", "v8.2.1-rc1"]).stdout.strip() == "2"
    assert runner.invoke(app, ["patch", "v8.2.1-rc1"]).stdout.strip() == "1"
    assert runner.invoke(app, ["parts", "v8.2.1-rc1"]).stdout.strip() == "8 2 1 -rc1"


def test_patch_without_patch_prints_empty_line() -> None:
    result = runner.invoke(app, ["patch", "8.2"])

    assert result.exit_code == 0
    assert result.stdout == ""


def test_check_uses_shell_exit_codes() -> None:
    assert runner.invoke(app, ["check", "8.2.1", "is-ga"]).exit_code == 0
    assert runner.invoke(app, ["check", "8.2.1-rc1", "is-ga"]).exit_code == 1
    assert runner.invoke(app, ["check", "8.2.1", "unknown"]).exit_code == 2


def test_compare_operator_true() -> None:
    result = runner.invoke(app, ["compare", "8.2.1-rc1", "<=", "8.2.1"])

    assert result.exit_code == 0
    assert result.stdout == ""


def test_compare_multi_digit_minor_version() -> None:
    result = runner.invoke(app, ["compare", "8.10", ">", "8.4.4"])

    assert result.exit_code == 0
    assert result.stdout == ""


def test_compare_operator_false() -> None:
    result = runner.invoke(app, ["compare", "8.2.1", "<", "8.2.1-rc1"])

    assert result.exit_code == 1
    assert result.stdout == ""


def test_compare_equal_operator() -> None:
    assert runner.invoke(app, ["compare", "8.2.1", "==", "v8.2.1"]).exit_code == 0


def test_compare_invalid_operator() -> None:
    result = runner.invoke(app, ["compare", "8.2.1", "!=", "8.2.1"])

    assert result.exit_code == 2
    assert "Invalid comparison operator" in result.stderr
