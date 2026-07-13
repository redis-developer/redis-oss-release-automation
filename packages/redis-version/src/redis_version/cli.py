"""CLI for Redis version utilities."""

import json
from enum import Enum
from typing import Dict

import typer

from .version import RedisVersion

app = typer.Typer(
    name="redis-version",
    help="Redis version parsing and comparison utilities",
    add_completion=False,
)


class OutputFormat(str, Enum):
    TEXT = "text"
    JSON = "json"


def _parse_version(version: str) -> RedisVersion:
    try:
        return RedisVersion.parse(version)
    except (ValueError, Exception) as exc:
        typer.echo(f"Failed to parse version '{version}': {exc}", err=True)
        raise typer.Exit(2) from exc


@app.command()
def parse(
    version: str = typer.Argument(..., help="Redis version to parse"),
    output: OutputFormat = typer.Option(
        OutputFormat.TEXT,
        "--output",
        "-o",
        help="Output format",
    ),
) -> None:
    """Parse a Redis version."""
    parsed = _parse_version(version)
    if output is OutputFormat.JSON:
        typer.echo(json.dumps(parsed.model_dump(), sort_keys=True))
        return

    typer.echo(str(parsed))


@app.command()
def major(
    version: str = typer.Argument(..., help="Redis version to parse"),
) -> None:
    """Print the major version number."""
    typer.echo(_parse_version(version).major)


@app.command()
def minor(
    version: str = typer.Argument(..., help="Redis version to parse"),
) -> None:
    """Print the minor version number."""
    typer.echo(_parse_version(version).minor)


@app.command()
def patch(
    version: str = typer.Argument(..., help="Redis version to parse"),
) -> None:
    """Print the patch version number, if present."""
    parsed = _parse_version(version)
    if parsed.patch is not None:
        typer.echo(parsed.patch)


@app.command()
def parts(
    version: str = typer.Argument(..., help="Redis version to parse"),
) -> None:
    """Print major minor patch suffix as space-separated values."""
    parsed = _parse_version(version)
    parts = [str(parsed.major), str(parsed.minor)]
    parts.append(str(parsed.patch) if parsed.patch is not None else "0")
    parts.append(parsed.suffix if parsed.suffix else "")
    typer.echo(" ".join(parts))


@app.command()
def check(
    version: str = typer.Argument(..., help="Redis version to check"),
    check_name: str = typer.Argument(
        ...,
        help="Property to check: is-internal, is-ga, is-eol, is-rc, is-milestone",
    ),
) -> None:
    """Check a Redis version property using shell-friendly exit codes."""
    parsed = _parse_version(version)
    property_map: Dict[str, bool] = {
        "is-internal": parsed.is_internal,
        "is-ga": parsed.is_ga,
        "is-eol": parsed.is_eol,
        "is-rc": parsed.is_rc,
        "is-milestone": parsed.is_milestone,
    }

    if check_name not in property_map:
        typer.echo(f"Invalid check: {check_name}", err=True)
        typer.echo(f"Valid checks: {', '.join(sorted(property_map))}", err=True)
        raise typer.Exit(2)

    raise typer.Exit(0 if property_map[check_name] else 1)


@app.command()
def compare(
    left: str = typer.Argument(..., help="Left Redis version"),
    comparison_operator: str = typer.Argument(..., help="Operator: <, >, <=, >=, =="),
    right: str = typer.Argument(..., help="Right Redis version"),
) -> None:
    """Compare two Redis versions using shell-friendly exit codes."""
    left_version = _parse_version(left)
    right_version = _parse_version(right)

    comparisons = {
        "<": left_version < right_version,
        ">": left_version > right_version,
        "<=": left_version <= right_version,
        ">=": left_version >= right_version,
        "==": left_version == right_version,
    }
    if comparison_operator not in comparisons:
        typer.echo(f"Invalid comparison operator: {comparison_operator}", err=True)
        typer.echo("Valid operators: <, >, <=, >=, ==", err=True)
        raise typer.Exit(2)

    raise typer.Exit(0 if comparisons[comparison_operator] else 1)
