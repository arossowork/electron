"""Extension points of the build pipeline.

The engine knows nothing about agents, product-person protocols or evaluations. A separate package (hwharness in this
repository) can register functions here through the `hwflow.hooks` entry-point group; each entry point is a callable
that takes no argument and calls the `register_*` functions below. Without such a package every hook list is empty and
the engine runs unchanged.

Hooks:
- check findings: `fn(design) -> list[finding]`, appended to the findings of the `check` stage.
- check state: `fn(design, findings) -> {key: value}`, merged into the result of the `check` stage.
- check inputs: `fn(design) -> list[str]`, the project paths the check hooks read besides design.yaml (files, or
  folders ending in "/"; `build/...` files too). They are inputs of the `check` stage: its cache key hashes them and
  the stage's scratch copy of the project holds them (ADR 0001, 2.3).
- report extras: `fn(design) -> dict`, stored in build/status.json under `name` after the report is written. The
  keys in `history_keys` are copied into the line appended to build/history.jsonl.
- commands: extra `hwflow <name>` subcommands (`add_arguments(parser)`, `run(design, args) -> int`).
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import dataclass, field
from importlib.metadata import entry_points
from typing import Any

ENTRY_POINT_GROUP = "hwflow.hooks"

CheckFindings = Callable[[Any], list[dict]]
CheckState = Callable[[Any, list[dict]], dict]
ReportExtra = Callable[[Any], dict]
CheckInputs = Callable[[Any], list[str]]


@dataclass
class ReportHook:
    name: str
    fn: ReportExtra
    history_keys: tuple[str, ...] = ()


@dataclass
class CommandHook:
    name: str
    help: str
    add_arguments: Callable[[argparse.ArgumentParser], None]
    run: Callable[[Any, argparse.Namespace], int]


@dataclass
class Registry:
    check_findings: list[CheckFindings] = field(default_factory=list)
    check_state: list[CheckState] = field(default_factory=list)
    check_inputs: list[CheckInputs] = field(default_factory=list)
    report_extras: list[ReportHook] = field(default_factory=list)
    commands: dict[str, CommandHook] = field(default_factory=dict)
    loaded: bool = False


_R = Registry()


def register_check_findings(fn: CheckFindings) -> None:
    _R.check_findings.append(fn)


def register_check_state(fn: CheckState) -> None:
    _R.check_state.append(fn)


def register_check_inputs(fn: CheckInputs) -> None:
    _R.check_inputs.append(fn)


def register_report_extra(name: str, fn: ReportExtra, history_keys: tuple[str, ...] = ()) -> None:
    _R.report_extras.append(ReportHook(name, fn, tuple(history_keys)))


def register_command(name: str, help: str, add_arguments: Callable[[argparse.ArgumentParser], None],
                     run: Callable[[Any, argparse.Namespace], int]) -> None:
    _R.commands[name] = CommandHook(name, help, add_arguments, run)


def registry() -> Registry:
    """The registry, after the entry points of installed packages have been loaded (once)."""
    if not _R.loaded:
        _R.loaded = True
        for ep in entry_points(group=ENTRY_POINT_GROUP):
            ep.load()()
    return _R


def reset() -> Registry:
    """Empty registry that does not load entry points (tests)."""
    global _R
    _R = Registry(loaded=True)
    return _R
