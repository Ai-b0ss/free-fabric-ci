from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from .state_machine import validate_state


def _load_snapshot(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except OSError as exc:
        raise ValueError(f"cannot read snapshot: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"invalid JSON at line {exc.lineno} column {exc.colno}: {exc.msg}"
        ) from exc


def validate_snapshot(path: Path) -> tuple[int, int]:
    state = _load_snapshot(path)
    if not isinstance(state, dict):
        raise ValueError("scheduler snapshot root must be an object")
    validate_state(state)
    return state["generation"], len(state["packets"])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m autopilot",
        description="Validate public Free Fabric scheduler snapshots.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    validate = sub.add_parser("validate", help="validate one scheduler state JSON file")
    validate.add_argument("snapshot", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command != "validate":  # pragma: no cover - argparse prevents this
        raise AssertionError(args.command)
    try:
        generation, packets = validate_snapshot(args.snapshot)
    except (ValueError, TypeError, KeyError) as exc:
        print(f"STATE_INVALID: {exc}", file=sys.stderr)
        return 2
    print(f"STATE_VALID generation={generation} packets={packets}")
    return 0
